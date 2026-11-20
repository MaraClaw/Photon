// Run through test_openclaw_mcp_runtime.py against the installed OpenClaw package.
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const root = mkdtempSync(join(tmpdir(), "openclaw-mcp-reconnect-"));
Object.assign(process.env, {
  HOME: join(root, "home"),
  OPENCLAW_HOME: join(root, "home"),
  OPENCLAW_STATE_DIR: join(root, "state"),
  OPENCLAW_CONFIG_PATH: join(root, "state", "openclaw.json"),
  TMPDIR: root,
  NODE_COMPILE_CACHE: join(root, "node-compile-cache"),
  BUN_RUNTIME_TRANSPILER_CACHE_PATH: join(root, "bun-transpiler"),
});
for (const directory of ["home", "state", "workspace", "agent"]) {
  mkdirSync(join(root, directory));
}

const dist = "/opt/openclaw/node_modules/openclaw/dist/";
const { createSessionMcpRuntime } = await import(`${dist}agents/agent-bundle-mcp-runtime.js`);
const { t: runtimeOwners } = await import(`${dist}agent-bundle-mcp-runtime-owner-COu6tF9k.mjs`);
const { t: StdioTransport } = await import(`${dist}mcp-stdio-transport-CFsQ4AA_.mjs`);
const { n: withRequestSignal } = await import(`${dist}agent-bundle-mcp-request-context-B-LzHCC-.mjs`);

// Observe the actual owned child; transport connection, messages and cleanup stay real.
const children = [];
const startProcess = StdioTransport.prototype.startProcess;
StdioTransport.prototype.startProcess = async function () {
  await startProcess.call(this);
  assert.ok(this.process, "real stdio transport must own a process");
  children.push({ transport: this, process: this.process, pid: this.pid });
  console.log(`CHILD_STARTED pid=${this.pid}`);
};

const cfg = {
  plugins: { enabled: false },
  mcp: { servers: { tracelattice: { command: "/usr/local/bin/tracelattice", transport: "stdio" } } },
};
const runtimeOptions = {
  sessionId: "mcp-reconnect-qa",
  sessionKey: "agent:main:mcp-reconnect-qa",
  workspaceDir: join(root, "workspace"),
  agentDir: join(root, "agent"),
  cfg,
};
let runtime = createSessionMcpRuntime(runtimeOptions);

async function callThought(number, historyLength) {
  const result = await runtime.callTool("tracelattice", "sequentialthinking_tools", {
    session_id: "mcp-reconnect-qa",
    thought: `Actual TraceLattice reconnect regression call ${number}.`,
    thought_number: number,
    total_thoughts: 5,
    next_thought_needed: true,
  });
  assert.notEqual(result.isError, true, "real TraceLattice call must succeed");
  const content = result.content.find((entry) => entry.type === "text");
  assert.ok(content, "TraceLattice must return a text result");
  const response = JSON.parse(content.text);
  assert.equal(response.thought_number, number);
  assert.equal(response.thought_history_length, historyLength, "history must belong to the current child");
  console.log(`TOOL_CALL_PASS number=${number} history=${response.thought_history_length}`);
}

try {
  const catalog = await runtime.getCatalog();
  assert.ok(catalog.tools.some((tool) => tool.serverName === "tracelattice" && tool.toolName === "sequentialthinking_tools"));
  await callThought(1, 1);
  await callThought(2, 2);
  assert.equal(children.length, 1, "connected calls must reuse one child");

  const original = children[0];
  assert.equal(typeof original.pid, "number");
  const exited = original.process.wait();
  process.kill(original.pid, "SIGTERM");
  const exit = await exited;
  assert.equal(exit.code, 0, "SIGTERM must produce a clean real TraceLattice exit");
  await original.transport.close();
  console.log(`CHILD_CLOSED pid=${original.pid} code=${exit.code}`);

  // No discovery, delay or retry is allowed between observed closure and this call.
  await assert.doesNotReject(() => callThought(3, 1), "immediate next actual tool call must await reconnect");
  assert.equal(children.length, 2, "one replacement child must serve the first call");
  assert.notEqual(children[1].pid, original.pid);
  await callThought(4, 2);

  const cancelled = AbortSignal.abort(new Error("QA caller cancelled"));
  await assert.rejects(withRequestSignal(cancelled, () => runtime.callTool(
    "tracelattice", "sequentialthinking_tools", {},
  )), { message: "QA caller cancelled" });
  assert.equal(children.length, 2, "cancelled requests must not start another child");
  console.log("PASS caller cancellation remains enforced");

  const owner = runtimeOwners.get(runtime);
  assert.ok(owner, "installed runtime must expose its reload owner");
  await owner.reload({ cfg: {
    ...cfg,
    mcp: { servers: { tracelattice: { ...cfg.mcp.servers.tracelattice, enabled: false } } },
  } });
  await runtime.joinCleanup();
  assert.equal(readFileSync(`/proc/${process.pid}/task/${process.pid}/children`, "utf8").trim(), "", "reload must reap every owned child");
  console.log("PASS reload disabled server and joined cleanly");
  await runtime.dispose();
  await runtime.joinCleanup();
  runtime = createSessionMcpRuntime(runtimeOptions);
  await callThought(5, 1);
  assert.equal(children.length, 3, "new runtime must create one fresh child for active dispose");
} finally {
  try {
    await runtime.dispose();
    await runtime.joinCleanup();
    for (const child of children) {
      await child.process.waitForExtinction();
      await child.transport.close();
    }
    assert.equal(readFileSync(`/proc/${process.pid}/task/${process.pid}/children`, "utf8").trim(), "", "dispose must leave no owned children");
    console.log("OWNED_MCP_RUNTIME_CLOSED");
  } finally {
    StdioTransport.prototype.startProcess = startProcess;
    rmSync(root, { recursive: true });
  }
}
console.log("OPENCLAW_MCP_RECONNECT_QA_PASS");
