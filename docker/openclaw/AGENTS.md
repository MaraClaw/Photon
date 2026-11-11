# docker/openclaw

Guest **OpenClaw agent image** helpers. Image contract also lives in repo-root `Dockerfile.openclaw`, `build-openclaw-local-dockerfile.sh` (local `openclaw:local` tag), and `publish-openclaw-local-dockerfile.sh` (Docker Hub `${namespace}/openclaw:${OPENCLAW_VERSION}`). Pin defaults live only in the `Dockerfile.openclaw` ARGs; both scripts source `openclaw-buildx-common.sh` and forward a pin only when it is set.

## Chain

```
tini -s -- validate-gogcli.sh validate-officecli.sh validate-soul-guardian.sh validate-tavily.sh validate-humanizer.sh bootstrap-agent-container.py openclaw gateway
USER: node
```

| File | When | Role |
|---|---|---|
| `install-common.sh` | image build | Shared staging, backup and rollback helpers sourced by every `install-*.sh`; copied beside the installers and removed after the last one |
| `install-officecli.sh` | image build | Download pinned target binary and skill, verify both SHA256 digests, and install them into the image |
| `install-tavily.sh` | image build | Install the bundled Tavily skills offline into the OpenClaw skills directory |
| `install-humanizer.sh` | image build | Install the pinned Humanizer skill and upstream license from an online or staged archive |
| `check-skills.py` | image build | Check Tavily `SKILL.md` frontmatter/metadata and soul-guardian `skill.json`/`onboard_state_dir.py` before the integrity inventory is created |
| `validate-gogcli.sh` | entrypoint | Verify gog, optionally load `GOG_KEYRING_PASSWORD_FILE`, and exec the supplied command unchanged |
| `validate-officecli.sh` | entrypoint | Verify OfficeCLI and exec the supplied command unchanged |
| `validate-soul-guardian.sh` | entrypoint | Verify the skill inventory and files and the non-mutating help probe, then exec the supplied command unchanged |
| `validate-tavily.sh` | entrypoint | Verify the two bundled Tavily skills' inventory, files and references offline, then exec the supplied command unchanged |
| `validate-humanizer.sh` | entrypoint | Verify the Humanizer skill offline, then exec the supplied command unchanged |
| `bootstrap-agent-container.py` | entrypoint (last), `docker exec` | Publish and enforce `AgentBootstrapCompleted` (see below); imports `bootstrap_event.py`, which owns the guest identity and the event file |

## Bootstrap event

`bootstrap-agent-container.py` runs after all five validators. Any command except the long-running `openclaw gateway [run] [--options]` (so also `openclaw gateway status`) is exec'd unchanged and emits nothing. For the gateway it launches one child, waits for the real loopback `/readyz` (port from `--port`, else `OPENCLAW_GATEWAY_PORT`, else 18789, as OpenClaw itself resolves it, so set the env var if `gateway.port` in `openclaw.json` differs; budget `OPENCLAW_BOOTSTRAP_TIMEOUT_SECONDS`, default 120), confirms the child is still alive, then atomically publishes `/tmp/maraclaw-bootstrap/AgentBootstrapCompleted.json`: `{"version":1,"event":"AgentBootstrapCompleted","boot_id":<kernel boot id>,"pid1_start_ticks":"<PID 1 /proc stat field 22>"}`. It then relays signals and exits with the child's status. Early exit, timeout (124) or HUP/INT/QUIT/TERM before readiness publish nothing and leave no child.

The event is valid only while both identity values match the running guest, so a file left by an earlier boot or container start never authorizes this one; removal is hygiene, not the guard. `check-ready` validates the event and then probes `/readyz` (0 ready, 1 not ready); `exec -- ACTION...` validates it and then execs the action. Both exit 75 while bootstrap is pending (missing, malformed, unsupported or mismatched event) and nothing is cached.

The engine and the image are a matched pair: an image built without this helper never emits the event, and there is no fallback for it.

## Pins

Runtime skill selection policy belongs in the `*-soul.md` fragments. The Dockerfile composes them into `/usr/local/share/openclaw/skills-soul.md` for engine-created workspaces and OpenClaw's default `SOUL.md` for standalone workspaces. Keep discovery metadata in each skill, execution guidance in its body, and API schemas in its references; avoid repeating selection policy there.

- Base: architecture-pinned `nikolaik/python-nodejs:python3.15-nodejs26-slim` with Python 3.15 and Node 26.
- gogcli supports **linux/amd64 and linux/arm64**, each with its own pinned digest. `build-openclaw-local-dockerfile.sh` uses **docker buildx --load** for one `DOCKER_PLATFORM` (default `linux/arm64`); other targets fail explicitly.
- Publish requires `DOCKERHUB_NAMESPACE` or `OPENCLAW_PUBLISH_IMAGE` and an existing `docker login`. It builds both platforms with **docker buildx --push** under `${repo}:${IMAGE_TAG}` (default `OPENCLAW_VERSION`, else the Dockerfile's `ARG OPENCLAW_VERSION`) with max-mode provenance and an SBOM attestation, adding `:latest` only when `PUSH_LATEST=1`. It does not load or retag a local image.
- OfficeCLI and its skill are baked into the image; startup never downloads them or changes OpenClaw state or configuration.
- Soul Guardian is baked into the image; startup validates it without initializing state or enabling monitoring.
- Tavily skills are installed from `tavily-skills/` at build time; startup validates them offline without credentials.
- Humanizer is installed at build time with installer-owned archive pins; startup validates it offline.
- OpenClaw lives in `/opt/openclaw/node_modules/openclaw` (not the global npm root), installed by `npm ci` from `openclaw-package/package-lock.json` after its archive is checked against `OPENCLAW_SHA256` and the lock integrity, without local memory-provider patches. Only OpenClaw's own install scripts run, approved through `allowScripts` in that `package.json`.
- After `npm ci`, `patch-cron-maintenance.py` requires OpenClaw 2026.11.3 and the exact SHA256 preimage of `dist/maintenance-CT4-WlcF.mjs`, then binds only maintenance `scope.schedule` to its lifecycle async context before event subscribers can schedule from a temporary prep scope. Drift or repeat application fails the build before writing. Remove the helper, build invocation, and context whitelist when upstream fixes this lifecycle context bug.
- Apt resolves from `snapshot.debian.org` at the `DEBIAN_SNAPSHOT` ARG; a pin bump may need a newer snapshot.
- Releases come from `.github/workflows/release.yml` on `openclaw-v<version>` tags and are cosign-signed keyless by digest.

## Avoid

- Do not put `GOG_KEYRING_PASSWORD` in Docker env/args; file path only. `validate-gogcli.sh` exports it from that file, so the gateway and its children inherit it (the engine smoke tests assert this).
- Image tests live in Photon `tests/`; gogcli product docs and routing tests remain in MaraClaw engine.
- Guest LLM is whatever `openclaw.json` names. Engine writes primary + registered secondary/fallback at container start, then `openclaw_routing` rewrites the selected primary per queued turn. The gateway watches that file (hot reload). Do not add a second classifier inside the image.
