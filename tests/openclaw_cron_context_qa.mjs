// Run through test_openclaw_cron_runtime.py against the installed OpenClaw package.
import assert from "node:assert/strict";
import { AsyncLocalStorage } from "node:async_hooks";
import { randomUUID } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const root = mkdtempSync(join(tmpdir(), "openclaw-cron-context-"));
Object.assign(process.env, {
  HOME: join(root, "home"),
  OPENCLAW_HOME: join(root, "home"),
  OPENCLAW_STATE_DIR: join(root, "state"),
  OPENCLAW_CONFIG_PATH: join(root, "state", "openclaw.json"),
  TMPDIR: root,
  NODE_COMPILE_CACHE: join(root, "node-compile-cache"),
  BUN_RUNTIME_TRANSPILER_CACHE_PATH: join(root, "bun-transpiler"),
});
mkdirSync(process.env.HOME, { recursive: true });
mkdirSync(join(process.env.OPENCLAW_STATE_DIR, "cron"), { recursive: true });
const cronStorePath = join(process.env.OPENCLAW_STATE_DIR, "cron", "jobs.json");
writeFileSync(cronStorePath, JSON.stringify({ version: 1, jobs: [] }));

const dist = "/opt/openclaw/node_modules/openclaw/dist/";
const admission = await import(`${dist}agent-database-admission-B7tTtrxy.mjs`);
const { t: GatewayScheduler } = await import(`${dist}gateway-scheduler-DzQtXKjV.mjs`);
const { a: sessionChanges } = await import(`${dist}session-row-changes-vFaNnKNM.mjs`);
const { startCronMaintenance, stopCronMaintenance } = await import(`${dist}maintenance-ClKvIKaH.mjs`);
const { O: setRuntimeConfigSnapshot } = await import(`${dist}runtime-snapshot-B7oq5Q8F.mjs`);
const { i: openStateDatabase } = await import(`${dist}openclaw-state-db-8yu4b0Vo.mjs`);
const agentDatabases = await import(`${dist}openclaw-agent-db-BWwfrtJl.mjs`);
const { s: closeStateDatabase } = await import(`${dist}openclaw-state-db-cache-Bxrf1nPj.mjs`);
const { i: getLogger } = await import(`${dist}logger-DR3u4tgD.mjs`);

let now = 100000;
const scheduler = new GatewayScheduler({ clock: {
  now: () => now,
  monotonicNow: () => now,
  // The real scheduler is driven explicitly; no host timers or sleeps are needed.
  arm: () => () => {},
} });
const lifecycle = new AsyncLocalStorage();
const warnings = [];
getLogger().attachTransport({
  format: () => "",
  write(record) {
    const serialized = JSON.stringify(record);
    if (serialized.includes("cron/maintenance") && record._meta.logLevelId >= 4) {
      warnings.push(serialized);
      console.error("CRON_WARNING", serialized);
    }
  },
});

async function runNextWake() {
  const deadline = scheduler.nextWakeAtMs;
  assert.equal(typeof deadline, "number", "real scheduler must have a pending wake");
  now = deadline;
  await scheduler.wake(now);
}

try {
  setRuntimeConfigSnapshot({ agents: { list: [{ id: "main", default: true }] } });
  openStateDatabase();
  const database = agentDatabases.openOpenClawAgentDatabase({ agentId: "main" });
  let ordinaryStore;
  lifecycle.run("ordinary-caller", () => scheduler.schedule({
    id: "cron-qa-ordinary-context",
    atMs: now,
    run: () => { ordinaryStore = lifecycle.getStore(); },
  }));
  await runNextWake();
  assert.equal(ordinaryStore, "ordinary-caller", "ordinary scheduling must inherit caller ALS");
  console.log("PASS ordinary scheduler inherits caller context");

  const visits = [];
  lifecycle.run("lifecycle-one", () => startCronMaintenance(scheduler, () => {
    visits.push(lifecycle.getStore());
    return cronStorePath;
  }));
  const pending = admission.c({
    agentId: "main", paths: [database.path], pending: true, reason: "cron context QA pending",
  });
  admission.v([pending], { source: "startup" });
  assert.throws(() => admission.n("main"), {
    name: "AgentDatabaseAdmissionError",
    refusal: { ...pending },
  });

  const gate = Promise.withResolvers();
  let stale;
  let lateEvent;
  await admission.g(pending, { assertCurrent() {} }, async () => {
    admission.n("main");
    stale = AsyncLocalStorage.snapshot();
    lateEvent = gate.promise.then(() => lifecycle.run("late-producer", () => {
      sessionChanges.emit({ all: true, scope: { agentId: "main", topology: true } });
    }));
  });
  assert.equal(typeof stale, "function");
  admission.n("main");
  assert.throws(() => stale(() => admission.n("main")), {
    message: "Agent database preparation has ended: main",
  });
  console.log("PASS public admission recovered; expired preparation still refused");

  const remaining = [];
  for (const pass of [1, 2]) {
    const sessionKey = `agent:main:cron:context-qa:run:${pass}`;
    const sessionId = randomUUID();
    // Fixture rows use the installed schema; only real maintenance removes them.
    database.db.prepare(`INSERT INTO session_nodes
      (session_key, current_session_id, entry_json, updated_at, entry_valid)
      VALUES (?, ?, ?, ?, 1)`).run(
      sessionKey, sessionId, JSON.stringify({ sessionId, updatedAt: 1 }), 1,
    );
    database.db.prepare(`INSERT INTO session_windows
      (session_id, session_key, created_at, updated_at) VALUES (?, ?, 1, 1)`).run(sessionId, sessionKey);
    if (pass === 1) {
      // The callback was registered under preparation, but fires after its final clean event.
      gate.resolve();
      await lateEvent;
    } else {
      stale(() => lifecycle.run("late-producer", () => {
        sessionChanges.emit({ all: true, scope: { agentId: "main", topology: true } });
      }));
    }
    await runNextWake();
    const row = database.db.prepare("SELECT count(*) AS count FROM session_nodes WHERE session_key = ?").get(sessionKey);
    remaining.push(row.count);
    console.log(`MAINTENANCE_PASS ${pass}: remaining expired rows=${row.count}`);
  }
  assert.deepEqual(warnings, [], "late preparation events must not poison cron maintenance");
  assert.deepEqual(remaining, [0, 0], "both actual maintenance passes must prune their expired session");
  assert.deepEqual(visits, ["lifecycle-one", "lifecycle-one"], "cron must retain lifecycle ALS, including after a producer event");
  console.log("PASS two SQLite maintenance passes; no warnings; lifecycle context preserved");

  // Stop cancels an already queued sweep and removes listeners before rebinding.
  stale(() => sessionChanges.emit({ all: true, scope: { agentId: "main", topology: true } }));
  const cancelledDeadline = scheduler.nextWakeAtMs;
  assert.equal(typeof cancelledDeadline, "number");
  await stopCronMaintenance();
  assert.equal(scheduler.nextWakeAtMs, null, "stop must cancel the owned cron wake");
  now = cancelledDeadline;
  await scheduler.wake(now);
  assert.deepEqual(visits, ["lifecycle-one", "lifecycle-one"]);
  stale(() => sessionChanges.emit({ all: true, scope: { agentId: "main", topology: true } }));
  assert.equal(scheduler.nextWakeAtMs, null, "stopped listeners must not schedule another sweep");
  lifecycle.run("lifecycle-two", () => startCronMaintenance(scheduler, () => {
    visits.push(lifecycle.getStore());
    return cronStorePath;
  }));
  stale(() => lifecycle.run("late-producer", () => {
    sessionChanges.emit({ all: true, scope: { agentId: "main", topology: true } });
  }));
  await runNextWake();
  assert.deepEqual(warnings, []);
  assert.deepEqual(visits, ["lifecycle-one", "lifecycle-one", "lifecycle-two"], "restart must bind the new lifecycle context");
  console.log("PASS stop cancellation and restart context rebinding");

  admission.v([pending], { source: "startup" });
  assert.throws(() => admission.n("main"), { name: "AgentDatabaseAdmissionError", refusal: { ...pending } });
  admission.u(pending, new Error("cron context QA inspection failed"), {});
  assert.throws(() => admission.n("main"), (error) => {
    assert.ok(error instanceof admission.t);
    assert.equal(error.refusal.code, "agent-database-inspection-failed");
    return true;
  });
  admission.v([], { source: "startup" });
  admission.n("main");
  assert.throws(() => stale(() => admission.n("main")), { message: "Agent database preparation has ended: main" });
  console.log("PASS pending and failed admission refused; public recovered; stale context refused");
  console.log("OPENCLAW_CRON_CONTEXT_QA_PASS");
} finally {
  await stopCronMaintenance();
  await scheduler.stop();
  await agentDatabases.closeOpenClawAgentDatabasesAsync();
  await closeStateDatabase();
  rmSync(root, { recursive: true });
  console.log("OWNED_RUNTIME_CLOSED");
}
