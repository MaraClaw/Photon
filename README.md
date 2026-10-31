# Photon

Photon builds and publishes MaraClaw's OpenClaw guest image for linux/amd64 and linux/arm64. The image installs pinned gogcli and OfficeCLI at build time and bundles the OfficeCLI skill and all 32 upstream gogcli skills, replacing OpenClaw's built-in `gog` skill. Google skills require appropriate Google credentials, authorization, and API access. The included `crabbox` skill is for maintainers and requires an external Crabbox installation and an appropriate repository checkout. Startup verifies both CLIs, the soul-guardian skill, and the two bundled Linkup skills without downloading or changing OpenClaw state or configuration. Linkup validation needs no credentials; actual requests use the authenticated MaraClaw engine proxy. If `GOG_KEYRING_PASSWORD_FILE` is set, `GOG_HOME` must also be set; the validator reads the password into the process environment and creates `GOG_HOME` with mode 0700.

The gogcli skill archive is pinned by commit and SHA256 in `docker/openclaw/install-gogcli-skills.sh`. To update it, verify the new commit archive and update both pins together; the complete `.agents/skills` tree is installed at build time.

The image also bundles `soul-guardian` from `prompt-security/clawsec`, including its AGPL-3.0 license. Its source archive commit and SHA256 are pinned in `docker/openclaw/install-soul-guardian.sh`; verify a new archive independently and update both pins together. The checksum verifies the pinned archive, not a publisher signature. Bundling does not initialize a baseline or enable monitoring: review the installed skill's `SKILL.md` and explicitly opt in to baseline creation and monitoring. Its default restore policy can overwrite changed workspace files, and guardian state may contain sensitive snapshots.

## Startup probe policy

Each external validation probe has a 30-second deadline and a 2-second termination grace. Override these with `OPENCLAW_PROBE_TIMEOUT_SECONDS` and `OPENCLAW_PROBE_GRACE_SECONDS`; both must be positive decimal seconds (fractions are allowed, but empty, zero, negative, non-finite, whitespace and exponent forms are rejected before any probe). Invalid policy exits 2, timeout exits 124, and handled HUP, INT or TERM exits 128 plus the signal number. Probe output is suppressed; captured npm metadata and file-backed passwords are private and trailing newlines are stripped.

The shared Python supervisor terminates the probe process group, escalates to KILL after the grace on timeout or cancellation, and reaps owned descendants on Linux. Residual group members are terminated on ordinary completion too. This covers non-daemonizing probes that stay in their process group, not escaped sessions or uninterruptible kernel waits. The final command is executed unchanged, outside the deadline, after supervision and capture files have been removed. Direct validator use requires POSIX sh, Python 3 with `waitid`/`WNOWAIT`, writable temporary storage, and both `startup-probe` helpers beside the validator; Linux also requires working `prctl` subreaper support. Darwin supports process-group termination for host tests; Linux is the descendant-reaping authority.

## Bootstrap completion event

The last entrypoint stage, `bootstrap-agent-container.py`, starts `openclaw gateway`, waits for its real loopback `/readyz` (`--port`, else `OPENCLAW_GATEWAY_PORT`, else 18789; within `OPENCLAW_BOOTSTRAP_TIMEOUT_SECONDS`, default 120) and only then publishes `/tmp/maraclaw-bootstrap/AgentBootstrapCompleted.json`, bound to the current kernel boot id and PID 1 start time. An event from an earlier boot or container start never authorizes the next run. In the guest, `bootstrap-agent-container.py check-ready` and `bootstrap-agent-container.py exec -- <action>` require the current event and exit 75 while bootstrap is pending. Every other command is exec'd unchanged. A MaraClaw engine that waits for this event must run with an image built from this chain; older images never emit it and there is no fallback.

## Local build

Copy `.env.example` to `.env` if you want project-local configuration. Both scripts change to the Photon directory and source `.env` by default; assignments in that file override inherited environment variables. Set `ENV_FILE` to an explicit path to source a different file, or to a nonexistent path to use only the process environment and script defaults.

The local build loads one target (default `linux/arm64`) and defaults to `openclaw:local`. Its tag must match the engine's `MARACLAW_OPENCLAW_IMAGE` and it must be loaded on the same Docker daemon used by the worker. For example, with no overriding assignment in the selected env file:

```bash
MARACLAW_OPENCLAW_IMAGE=openclaw:2026.10.9 ./build-openclaw-local-dockerfile.sh
```

Set `DOCKER_PLATFORM=linux/amd64` for the other supported target. Keep the OpenClaw version and checksum aligned with the intended image tag.

### Pre-staged auxiliary assets

Online acquisition is the default. To use previously acquired assets, set `OPENCLAW_ASSET_MODE=staged` and `OPENCLAW_ASSET_DIR` to a local directory (paths containing spaces are supported). The local build requires the selected target and publication requires both targets:

```text
common/officecli-SKILL.md
common/gogcli-skills.tar.gz
common/soul-guardian.tar.gz
linux-amd64/gogcli.tar.gz
linux-amd64/officecli
linux-arm64/gogcli.tar.gz
linux-arm64/officecli
```

The existing pinned digests are checked before an asset is extracted, installed, or executed. Missing or corrupt staged inputs fail without an online fallback. Buildx provides the directory as the named `openclaw_assets` build context, mounted read-only only during installation; it is not copied into the image or added to the default context. This mode covers auxiliary assets only: base images, apt packages and the OpenClaw npm package may still require network access.

The image generates a root-owned installed-content inventory after installation. Each startup validator checks its own executables and skill trees against this image baseline, including additions, removals and relative symlink changes, before running component code. Validation is offline and runs on every invocation, including direct calls and restarts. It detects drift, not publisher authenticity, and cannot protect against root replacing both assets and inventory.

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

To activate the versioned pre-push hook in a clone, run:

```bash
git config core.hooksPath .githooks
```

The tests must pass for the push to proceed.

The real dual-architecture Docker integration smoke is opt-in. It needs Docker buildx, network access, both target execution environments, and host Node `v26.12.1`:

```bash
RUN_OPENCLAW_OFFICECLI_SMOKE=1 uv run --extra dev pytest tests/test_openclaw_officecli_container_smoke.py
```

The dual-platform validator QA additionally exercises normal offline, non-root entrypoint and direct-validator execution:

```bash
uv run --extra dev python tests/openclaw_validator_qa.py
OPENCLAW_ASSET_MODE=staged OPENCLAW_ASSET_DIR="/absolute/path/to/staged assets" uv run --extra dev python tests/openclaw_validator_qa.py
```

Staged validator QA requires the complete two-architecture layout above. Both QA modes need Docker Buildx, both target execution environments, and network access for the remaining build dependencies; staged QA also needs independently acquired inputs matching the pinned digests.
