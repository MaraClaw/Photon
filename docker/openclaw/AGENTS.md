# docker/openclaw

Guest **OpenClaw agent image** helpers. Not the FastAPI API. Image contract also lives in repo-root `Dockerfile.openclaw`, `build-openclaw-local-dockerfile.sh` (local `openclaw:local` tag), and `publish-openclaw-local-dockerfile.sh` (Docker Hub `${namespace}/openclaw:${OPENCLAW_VERSION}`).

## Chain

```
tini -s -- validate-gogcli.sh validate-officecli.sh validate-soul-guardian.sh validate-linkup.sh bootstrap-agent-container.py openclaw gateway
USER: node
```

| File | When | Role |
|---|---|---|
| `install-officecli.sh` | image build | Download pinned target binary and skill, verify both SHA256 digests, and install them into the image |
| `install-linkup.sh` | image build | Install the bundled Linkup skills offline into the OpenClaw skills directory |
| `validate-gogcli.sh` | entrypoint | Verify gog, optionally load `GOG_KEYRING_PASSWORD_FILE`, and exec the supplied command unchanged |
| `validate-officecli.sh` | entrypoint | Verify OfficeCLI and exec the supplied command unchanged |
| `validate-soul-guardian.sh` | entrypoint | Verify the skill assets and non-mutating help probe, then exec the supplied command unchanged |
| `validate-linkup.sh` | entrypoint | Verify the two bundled Linkup skills and references offline, then exec the supplied command unchanged |
| `bootstrap-agent-container.py` | entrypoint (last), `docker exec` | Publish and enforce `AgentBootstrapCompleted` (see below); imports `bootstrap_event.py`, which owns the guest identity and the event file |

## Bootstrap event

`bootstrap-agent-container.py` runs after all four validators. Any command except the long-running `openclaw gateway [run] [--options]` (so also `openclaw gateway status`) is exec'd unchanged and emits nothing. For the gateway it launches one child, waits for the real loopback `/readyz` (port from `--port`, else `OPENCLAW_GATEWAY_PORT`, else 18789, as OpenClaw itself resolves it, so set the env var if `gateway.port` in `openclaw.json` differs; budget `OPENCLAW_BOOTSTRAP_TIMEOUT_SECONDS`, default 120), confirms the child is still alive, then atomically publishes `/tmp/maraclaw-bootstrap/AgentBootstrapCompleted.json`: `{"version":1,"event":"AgentBootstrapCompleted","boot_id":<kernel boot id>,"pid1_start_ticks":"<PID 1 /proc stat field 22>"}`. It then relays signals and exits with the child's status. Early exit, timeout (124) or HUP/INT/QUIT/TERM before readiness publish nothing and leave no child.

The event is valid only while both identity values match the running guest, so a file left by an earlier boot or container start never authorizes this one; removal is hygiene, not the guard. `check-ready` validates the event and then probes `/readyz` (0 ready, 1 not ready); `exec -- ACTION...` validates it and then execs the action. Both exit 75 while bootstrap is pending (missing, malformed, unsupported or mismatched event) and nothing is cached.

The engine and the image are a matched pair: an image built without this helper never emits the event, and there is no fallback for it.

## Pins

- Base: architecture-pinned `nikolaik/python-nodejs` with Python 3.14.9, Node 26.12.1, and Debian Trixie. Backend image is `python:3.14.8-slim-trixie`. Sandbox Node image is `node:26.12.1-slim`.
- gogcli supports **linux/amd64 and linux/arm64**, each with its own pinned digest. `build-openclaw-local-dockerfile.sh` uses **docker buildx --load** for one `DOCKER_PLATFORM` (default `linux/arm64`); other targets fail explicitly.
- Publish requires `DOCKERHUB_NAMESPACE` or `OPENCLAW_PUBLISH_IMAGE` and an existing `docker login`. It builds both platforms with **docker buildx --push** under `${repo}:${IMAGE_TAG}` (default `OPENCLAW_VERSION`), adding `:latest` only when `PUSH_LATEST=1`. It does not load or retag a local image.
- OfficeCLI and its skill are baked into the image; startup never downloads them or changes OpenClaw state or configuration.
- Soul Guardian is baked into the image; startup validates it without initializing state or enabling monitoring.
- Linkup skills are installed from `linkup-skills/` at build time; startup validates them offline without credentials.
- OpenClaw is installed from its checksum-verified pinned npm archive without local memory-provider patches.

## Avoid

- Do not put `GOG_KEYRING_PASSWORD` in Docker env/args; file path only.
- Image tests live in Photon `tests/`; gogcli product docs and routing tests remain in MaraClaw engine.
- Guest LLM is whatever `openclaw.json` names. Engine writes primary + registered secondary/fallback at container start, then `openclaw_routing` rewrites the selected primary per queued turn. The gateway watches that file (hot reload). Do not add a second classifier inside the image.
