# docker/openclaw

Guest **OpenClaw agent image** helpers. Not the FastAPI API. Image contract also lives in repo-root `Dockerfile.openclaw`, `build-openclaw-local-dockerfile.sh` (local `openclaw:local` tag), and `publish-openclaw-local-dockerfile.sh` (Docker Hub `${namespace}/openclaw:${OPENCLAW_VERSION}`).

## Chain

```
tini -s -- validate-gogcli.sh validate-officecli.sh validate-soul-guardian.sh openclaw gateway
USER: node
```

| File | When | Role |
|---|---|---|
| `install-officecli.sh` | image build | Download pinned target binary and skill, verify both SHA256 digests, and install them into the image |
| `validate-gogcli.sh` | entrypoint | Verify gog, optionally load `GOG_KEYRING_PASSWORD_FILE`, and exec the supplied command unchanged |
| `validate-officecli.sh` | entrypoint | Verify OfficeCLI and exec the supplied command unchanged |
| `validate-soul-guardian.sh` | entrypoint | Verify the skill assets and non-mutating help probe, then exec the supplied command unchanged |

## Pins

- Base: architecture-pinned `nikolaik/python-nodejs` with Python 3.14.9, Node 26.12.1, and Debian Trixie. Backend image is `python:3.14.8-slim-trixie`. Sandbox Node image is `node:26.12.1-slim`.
- gogcli supports **linux/amd64 and linux/arm64**, each with its own pinned digest. `build-openclaw-local-dockerfile.sh` uses **docker buildx --load** for one `DOCKER_PLATFORM` (default `linux/arm64`); other targets fail explicitly.
- Publish requires `DOCKERHUB_NAMESPACE` or `OPENCLAW_PUBLISH_IMAGE` and an existing `docker login`. It builds both platforms with **docker buildx --push** under `${repo}:${IMAGE_TAG}` (default `OPENCLAW_VERSION`), adding `:latest` only when `PUSH_LATEST=1`. It does not load or retag a local image.
- OfficeCLI and its skill are baked into the image; startup never downloads them or changes OpenClaw state or configuration.
- Soul Guardian is baked into the image; startup validates it without initializing state or enabling monitoring.
- OpenClaw is installed from its checksum-verified pinned npm archive without local memory-provider patches.

## Avoid

- Do not put `GOG_KEYRING_PASSWORD` in Docker env/args; file path only.
- Image tests live in Photon `tests/`; gogcli product docs and routing tests remain in MaraClaw engine.
- Guest LLM is whatever `openclaw.json` names. Engine writes primary + registered secondary/fallback at container start, then `openclaw_routing` rewrites the selected primary per queued turn. The gateway watches that file (hot reload). Do not add a second classifier inside the image.
