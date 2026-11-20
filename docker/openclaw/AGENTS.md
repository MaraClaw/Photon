# docker/openclaw

Guest **OpenClaw agent image** helpers. Image contract also lives in repo-root `Dockerfile.openclaw`, `build-openclaw-local-dockerfile.sh` (local `openclaw:local` tag), and `publish-openclaw-local-dockerfile.sh` (Docker Hub `${namespace}/openclaw:${OPENCLAW_VERSION}`). Pin defaults live only in the `Dockerfile.openclaw` ARGs; both scripts source `openclaw-buildx-common.sh` and forward a pin only when it is set.

## Chain

```
tini -s -- validate-gogcli.sh validate-officecli.sh validate-soul-guardian.sh validate-tavily.sh validate-humanizer.sh validate-tracelattice.sh bootstrap-agent-container.py openclaw gateway
USER: node
```

| File | When | Role |
|---|---|---|
| `install-common.sh` | image build | Shared staging, backup and rollback helpers sourced by every `install-*.sh`; copied beside the installers and removed after the last one |
| `install-officecli.sh` | image build | Download pinned target binary and skill, verify both SHA256 digests, and install them into the image |
| `install-tavily.sh` | image build | Install the bundled Tavily skills offline into the OpenClaw skills directory |
| `install-humanizer.sh` | image build | Install the pinned Humanizer skill and upstream license from an online or staged archive |
| `install-tracelattice-skill.sh` | image build | Install the pinned `tracelattice-skill` skill, its two references and the upstream license from an online or staged archive |
| `tracelattice.sh` | image build, MCP child | Installed as `/usr/local/bin/tracelattice`; runs the TraceLattice package in `/opt/tracelattice` with Node and forces the `stdio` transport |
| `patch-mcp-reconnect.py` | image build | Patch the pinned OpenClaw runtime to await an in-flight reconnection before using a disconnected session; refuse version or source drift |
| `check-skills.py` | image build | Check Tavily `SKILL.md` frontmatter/metadata and soul-guardian `skill.json`/`onboard_state_dir.py` before the integrity inventory is created |
| `validate-gogcli.sh` | entrypoint | Verify gog, optionally load `GOG_KEYRING_PASSWORD_FILE`, and exec the supplied command unchanged |
| `validate-officecli.sh` | entrypoint | Verify OfficeCLI and exec the supplied command unchanged |
| `validate-soul-guardian.sh` | entrypoint | Verify the skill inventory and files and the non-mutating help probe, then exec the supplied command unchanged |
| `validate-tavily.sh` | entrypoint | Verify the two bundled Tavily skills' inventory, files and references offline, then exec the supplied command unchanged |
| `validate-humanizer.sh` | entrypoint | Verify the Humanizer skill offline, then exec the supplied command unchanged |
| `validate-tracelattice.sh` | entrypoint | Verify the TraceLattice launcher, package tree and skill offline without starting the server, then exec the supplied command unchanged |
| `bootstrap-agent-container.py` | entrypoint (last), `docker exec` | Publish and enforce `AgentBootstrapCompleted` (see below); imports `bootstrap_event.py`, which owns the guest identity and the event file, and `builtin_mcp.py` |
| `builtin_mcp.py` | gateway start | Add absent built-in `mcp.servers` entries and migrate missing `enabled` flags in the active `openclaw.json` (see below); never raises |

## Startup probes

Each validator sources `startup-probe.sh` and calls `probe_verify COMPONENT INVENTORY_DIR SKILLS_ROOT [COMMAND...]` once. That starts one `startup-probe.py --verify` supervisor, which forks a child to run `verify()` from the `installed-assets.py` beside the validator (own session, default signal dispositions, no control descriptor, no second interpreter) and then, if the check passed, runs COMMAND. Each step gets its own `OPENCLAW_PROBE_TIMEOUT_SECONDS` deadline and `OPENCLAW_PROBE_GRACE_SECONDS` grace, and cancellation is checked between them. Status 1 means the inventory check failed and COMMAND never started, 3 means COMMAND failed; 2, 124 and 129/130/143 keep their meanings (invalid policy, timeout, HUP/INT/TERM). `installed-assets.py` remains the only owner of the scan and comparison logic. `probe_run` and `probe_capture` still supervise a single command; `validate-gogcli.sh` uses `probe_capture` for the keyring password file. On Linux each step's wait selects on a pidfd so the supervisor continues as soon as the step exits; cancellation is still read every 20 ms, and platforms without pidfds keep the 20 ms poll.

Control and capture files live in a unique mode-0700 directory allocated per validator invocation. Cleanup closes the control descriptor and removes only that invocation's storage before final exec; stale PID-named files never block another invocation. Escaped descendants always receive KILL before cleanup gives up, including with sub-20ms grace periods or a delayed supervisor loop.

## Bootstrap event

`bootstrap-agent-container.py` runs after all six validators. It parses root selectors and gateway option/value roles to distinguish a foreground `openclaw gateway [run] [--options]` from management commands, preserving the original argv. Management and unrelated commands are exec'd unchanged and leave configuration and completion state alone. For the gateway it validates policy and identity, registers built-in MCP servers in the selected profile environment, launches one child, waits for loopback `/readyz`, confirms the child is still alive, then atomically publishes `/tmp/maraclaw-bootstrap/AgentBootstrapCompleted.json`: `{"version":2,"event":"AgentBootstrapCompleted","boot_id":<kernel boot id>,"pid1_start_ticks":"<PID 1 /proc stat field 22>","gateway_port":<integer TCP port>}`. Port precedence is `--port`, `OPENCLAW_GATEWAY_PORT`, then the selected profile's default (18789 for the default profile); set the flag or environment variable if `gateway.port` in the config differs. The startup budget is `OPENCLAW_BOOTSTRAP_TIMEOUT_SECONDS`, default 120. The gateway child gets `OPENCLAW_NO_RESPAWN=1` unless the caller set the variable (OpenClaw's embedding preset): restarts stay in the tracked child instead of a detached replacement, a restart that cannot reuse the runtime exits, and control-plane `update.run` is refused. The helper then relays signals and exits with the child's status. Early exit, timeout (124) or HUP/INT/QUIT/TERM before readiness publish nothing and leave no child.

The event is valid only while both identity values match the running guest and `gateway_port` is an integer in 1–65535, excluding booleans. Older schemas are denied. A file left by an earlier boot or container start never authorizes this one; removal is hygiene, not the guard. `check-ready` validates the event and probes `/readyz` on its recorded port (0 ready, 1 not ready); `exec -- ACTION...` validates it and then execs the action. Both exit 75 while bootstrap is pending and nothing is cached.

The engine and the image are a matched pair: an image built without this helper never emits the event, and there is no fallback for it.

## Built-in MCP server

`builtin_mcp.register(report, *, environ=None)` runs before the gateway process exists, using the supplied root-selector environment or `os.environ`. It resolves the config as OpenClaw does: trimmed `OPENCLAW_CONFIG_PATH`, else trimmed `OPENCLAW_STATE_DIR/openclaw.json`, else `.openclaw[-<profile>]/openclaw.json` under the effective `OPENCLAW_HOME`/`HOME`. A valid named profile keeps its spelling; blank, invalid and case-insensitive `default` profiles use `.openclaw`. It adds absent `mcp.servers.tracelattice = {"command": "/usr/local/bin/tracelattice", "transport": "stdio", "enabled": true}` and adds only a missing `enabled` flag to existing literal objects. Existing flags and non-object entries remain untouched. Registration silently skips exact `OPENCLAW_CONFIG_READONLY=1` / `OPENCLAW_NIX_MODE=1`, and `$include` at the root, `mcp`, `mcp.servers` or the TraceLattice entry. Independent includes elsewhere do not prevent registration. The engine keeps guest `mcp.servers` entries when regenerating the file.

The write is a same-directory temp file renamed over the original, with the mode kept, and it happens only for a regular file owned by the current user that parses as strict JSON with object-valued `mcp` and `mcp.servers`. The helper re-stats the file before the rename and gives up if it changed. A missing config stays missing; every other case is one stderr report and the gateway starts anyway. The image does not use `openclaw mcp set`, which takes about two seconds and also stamps `meta` and writes backup and state files.

OpenClaw names MCP tools `serverName__toolName`, so the agent calls `tracelattice__sequentialthinking_tools`; the server itself and the upstream skill call the tool `sequentialthinking_tools`. `tracelattice-soul.md` names only the skill; the skill body covers the tool and notes that a client may show it with a server prefix. Each agent session that uses the tool keeps one Node child.

## Pins

Runtime skill selection policy belongs in the `*-soul.md` fragments. The Dockerfile composes them into `/usr/local/share/openclaw/skills-soul.md` for engine-created workspaces and OpenClaw's default `SOUL.md` for standalone workspaces. Keep discovery metadata in each skill, execution guidance in its body, and API schemas in its references; avoid repeating selection policy there.

- Base: architecture-pinned `nikolaik/python-nodejs:python3.15-nodejs26-slim` with Python 3.15 and Node 26.
- gogcli supports **linux/amd64 and linux/arm64**, each with its own pinned digest. `build-openclaw-local-dockerfile.sh` uses **docker buildx --load** for one `DOCKER_PLATFORM` (default `linux/arm64`); other targets fail explicitly.
- Publish requires `DOCKERHUB_NAMESPACE` or `OPENCLAW_PUBLISH_IMAGE` and an existing `docker login`. It builds both platforms with **docker buildx --push** under `${repo}:${IMAGE_TAG}` (default `OPENCLAW_VERSION`, else the Dockerfile's `ARG OPENCLAW_VERSION`) with max-mode provenance and an SBOM attestation, adding `:latest` only when `PUSH_LATEST=1`. It does not load or retag a local image.
- OfficeCLI and its skill are baked into the image; startup never downloads them or changes OpenClaw state or configuration.
- Soul Guardian is baked into the image; startup validates it without initializing state or enabling monitoring.
- Tavily skills are installed from `tavily-skills/` at build time; startup validates them offline without credentials.
- Humanizer is installed at build time with installer-owned archive pins; startup validates it offline.
- TraceLattice lives in `/opt/tracelattice`, installed by `npm ci --ignore-scripts` from `tracelattice-package/package-lock.json` after its archive is checked against `TRACELATTICE_SHA256` and the lock integrity. Its `dist/cli.js` has a `bun` shebang and the image has no bun, so the launcher runs it with `node`. The skill comes from the release commit archive with installer-owned pins (`TRACELATTICE_SKILL_COMMIT`, `TRACELATTICE_SKILL_SHA256`); bump the npm pins and the skill pins together.
- OpenClaw lives in `/opt/openclaw/node_modules/openclaw` (not the global npm root), installed by `npm ci` from `openclaw-package/package-lock.json` after its archive is checked against `OPENCLAW_SHA256` and the lock integrity, without local memory-provider patches. Only install scripts approved through `allowScripts` in that `package.json` run: OpenClaw's own plus the pinned `@google/genai`, `esbuild` and `protobufjs` versions. Its `devDependencies` are omitted from the image (`--omit=dev`).
- OpenClaw `2026.11.6` binds its cron maintenance scheduling callback to the gateway lifecycle upstream, so no local cron patch is needed. When bumping OpenClaw, update the exact bundle imports in `tests/openclaw_cron_context_qa.mjs` and run `OPENCLAW_CRON_QA_IMAGE=<built-image> uv run --extra dev pytest tests/test_openclaw_cron_runtime.py`. That offline test covers late events from expired preparation, stop/restart rebinding and fail-closed pending/failed admission; never weaken preparation guards to get a pass.
- The stable release still needs `patch-mcp-reconnect.py` for the first stdio tool call after child closure: cached catalog reads start reconnection in the background, so the active-session lookup must await it when disconnected. The patch retains the abort-aware wait, background refresh of connected sessions and process cleanup certification. Reassess the patch when bumping OpenClaw, update exact imports in `tests/openclaw_mcp_runtime_qa.mjs`, and run `OPENCLAW_MCP_QA_IMAGE=<built-image> uv run --extra dev pytest tests/test_openclaw_mcp_runtime.py`.
- The last root `RUN` removes `/tmp/node-compile-cache`, which the base image and root npm steps create as root; otherwise OpenClaw's worker compile cache (`os.tmpdir()/node-compile-cache/openclaw/<version>/<build>`) is unavailable to `node`. Validator QA asserts the directory is absent or writable. Build-time prewarming with OpenClaw's `gateway-prewarm.mjs` added 26 MB and was not faster than this writable cold cache, so the image does not prewarm.
- OpenClaw 2026.11.6 writes its default log file to `$OPENCLAW_STATE_DIR/logs/`; the image sets no log path.
- Apt resolves from `snapshot.debian.org` at the `DEBIAN_SNAPSHOT` ARG; a pin bump may need a newer snapshot.
- `uv` intentionally upgrades to the latest published release when its Docker layer rebuilds; a cached layer retains its installed version.
- Each final-stage `ARG` sits directly above the first `RUN` that reads it, and apt is its own layer ahead of the OpenClaw install: a build argument with a value enters the environment of every later `RUN`, so a pin declared earlier would rebuild layers that never read it. Installers carry their own default pins, so an `ARG` declared after its installer is silently ignored.
- Both scripts forward `OPENCLAW_BUILD_CACHE_FROM`/`OPENCLAW_BUILD_CACHE_TO` as `--cache-from`/`--cache-to` through `openclaw_collect_cache_args` in `openclaw-buildx-common.sh`, only when set and nonempty; the release workflow sets neither.
- Releases come from `.github/workflows/release.yml` on `openclaw-v<version>` tags. The same-commit reusable CI workflow gates publication, including native dual-architecture image QA with the existing MCP reconnect and shared full gateway smoke scenarios. The versioned manifest's Buildx metadata digest is cosign-signed and verified before that exact digest is promoted to `latest`; releases are serialized across tags.

## Avoid

- Do not put `GOG_KEYRING_PASSWORD` in Docker env/args; file path only. `validate-gogcli.sh` exports it from that file, so the gateway and its children inherit it (the engine smoke tests assert this).
- Image tests live in Photon `tests/`; gogcli product docs and routing tests remain in MaraClaw engine.
- Guest LLM is whatever `openclaw.json` names. Engine writes primary + registered secondary/fallback at container start, then `openclaw_routing` rewrites the selected primary per queued turn. The gateway watches that file (hot reload). Do not add a second classifier inside the image. The only image write to that file is the built-in `mcp.servers` registration and missing-flag migration above; do not widen it to other keys.
