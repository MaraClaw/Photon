# Photon

Photon builds and publishes MaraClaw's OpenClaw guest image for linux/amd64 and linux/arm64. The image installs pinned gogcli and OfficeCLI at build time and bundles the OfficeCLI skill alongside OpenClaw's gog skill. Startup verifies both CLIs without downloading or changing OpenClaw state or configuration. If `GOG_KEYRING_PASSWORD_FILE` is set, `GOG_HOME` must also be set; the validator reads the password into the process environment and creates `GOG_HOME` with mode 0700.

## Local build

Copy `.env.example` to `.env` if you want project-local configuration. Both scripts change to the Photon directory and source `.env` by default; assignments in that file override inherited environment variables. Set `ENV_FILE` to an explicit path to source a different file, or to a nonexistent path to use only the process environment and script defaults.

The local build loads one target (default `linux/arm64`) and defaults to `openclaw:local`. Its tag must match the engine's `MARACLAW_OPENCLAW_IMAGE` and it must be loaded on the same Docker daemon used by the worker. For example, with no overriding assignment in the selected env file:

```bash
MARACLAW_OPENCLAW_IMAGE=openclaw:2026.10.7 ./build-openclaw-local-dockerfile.sh
```

Set `DOCKER_PLATFORM=linux/amd64` for the other supported target. Keep the OpenClaw version and checksum aligned with the intended image tag.

## Publish

After `docker login`, publish both architectures under one versioned tag:

```bash
DOCKERHUB_NAMESPACE=youruser ./publish-openclaw-local-dockerfile.sh
```

Alternatively set `OPENCLAW_PUBLISH_IMAGE`; `IMAGE_TAG` defaults to `OPENCLAW_VERSION`, and `PUSH_LATEST=1` also publishes `latest`. Values in the selected env file take precedence over these command-line environment assignments.

## Test

With Python 3.14.8 and uv installed, run the standalone suite without Docker or other services:

```bash
uv run --extra dev pytest
```

The real dual-architecture Docker integration smoke is opt-in. It needs Docker buildx, network access, both target execution environments, and host Node `v26.12.1`:

```bash
RUN_OPENCLAW_OFFICECLI_SMOKE=1 uv run --extra dev pytest tests/test_openclaw_officecli_container_smoke.py
```
