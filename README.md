# Photon

Photon builds and publishes MaraClaw's OpenClaw guest image for linux/amd64 and linux/arm64. The image installs pinned gogcli and OfficeCLI at build time and bundles the OfficeCLI skill and all 32 upstream gogcli skills in `/home/node/.openclaw/skills`. Google skills require appropriate Google credentials, authorization, and API access. The included `crabbox` skill is for maintainers and requires an external Crabbox installation and an appropriate repository checkout. Startup verifies both CLIs, the soul-guardian skill, and the two bundled Tavily skills without downloading or changing OpenClaw state or configuration. The one startup write is the built-in MCP server registration described below, made only when the gateway is about to start. Tavily validation needs no credentials; actual requests use the authenticated MaraClaw engine proxy. If `GOG_KEYRING_PASSWORD_FILE` is set, `GOG_HOME` must also be set; the validator reads the password into the process environment and creates `GOG_HOME` with mode 0700. `GOG_KEYRING_PASSWORD` is therefore inherited by the gateway and every process it starts; the MaraClaw engine relies on this. The file stays readable by the `node` user, so the export does not widen access for code already running as that user.

Mount persistent state outside `/home/node/.openclaw` so it does not hide the image skills. MaraClaw uses `/home/node/custom-state`, sets `OPENCLAW_STATE_DIR` and `OPENCLAW_CONFIG_PATH` accordingly, and adds `/home/node/.openclaw/skills` to `skills.load.extraDirs`. These skills load as `openclaw-extra`; pinned OpenClaw's bundled `gog` takes precedence over the duplicate home-installed `gog`.

The gogcli skill archive is pinned by commit and SHA256 in `docker/openclaw/install-gogcli-skills.sh`. To update it, verify the new commit archive and update both pins together; the complete `.agents/skills` tree is installed at build time.

The image also bundles `soul-guardian` from `prompt-security/clawsec`, including its AGPL-3.0 license. Its source archive commit and SHA256 are pinned in `docker/openclaw/install-soul-guardian.sh`; verify a new archive independently and update both pins together. The checksum verifies the pinned archive, not a publisher signature. Bundling does not initialize a baseline or enable monitoring: review the installed skill's `SKILL.md` and explicitly opt in to baseline creation and monitoring. Its default restore policy can overwrite changed workspace files, and guardian state may contain sensitive snapshots.

The image also installs the Humanizer skill and its upstream license at build time. Its installer owns the default archive commit and SHA256; `HUMANIZER_COMMIT` and `HUMANIZER_SHA256` optionally override those pins for local builds and publication. Startup validates the installed skill offline before bootstrap.

The Humanizer communication policy requires user-facing prose, including proposed replies and drafts, to avoid em dashes (—), even when matching a writing sample. Verbatim quotations and technical content that must remain unchanged retain their original punctuation. The final prose is checked again after humanizing.

The image also bundles TraceLattice, a sequential-thinking MCP server, as the built-in `tracelattice` server. The npm package `@iworkforces/tracelattice` is installed into `/opt/tracelattice`, and `/usr/local/bin/tracelattice` runs it with Node over the `stdio` transport, whatever `TRACELATTICE_TRANSPORT_TYPE` the caller inherited. The `tracelattice-skill` skill and the upstream license come from the pinned source archive; `docker/openclaw/install-tracelattice-skill.sh` owns the default commit and SHA256, and `TRACELATTICE_SKILL_COMMIT` and `TRACELATTICE_SKILL_SHA256` optionally override them. Startup validates the launcher, the package tree and the skill offline, without starting the server.

Just before `bootstrap-agent-container.py` starts the gateway, it adds `mcp.servers.tracelattice` (`{"command": "/usr/local/bin/tracelattice", "transport": "stdio"}`) to the active `openclaw.json` (`OPENCLAW_CONFIG_PATH`, else `$OPENCLAW_STATE_DIR/openclaw.json`, else `${OPENCLAW_HOME:-$HOME}/.openclaw/openclaw.json`) when that name is absent. An existing entry is never changed, so `"enabled": false` turns the server off, while deleting the entry only lasts until the next start. The file is rewritten atomically with its mode kept, and only when it is a regular file owned by the current user that parses as strict JSON. A missing config is left missing, and any other case is reported on stderr and never blocks the gateway. No other command touches the config. OpenClaw exposes the server's `sequentialthinking_tools` tool to the agent as `tracelattice__sequentialthinking_tools`, and each agent session that uses it runs one Node child.

Runtime skill usage policy lives in `docker/openclaw/*-soul.md`. The image combines those fragments into `/usr/local/share/openclaw/skills-soul.md` and appends that content to OpenClaw's default `SOUL.md`. MaraClaw's workspace template references the image-owned file instead of copying its instructions. Changes apply to newly initialized workspaces; existing workspace files are preserved. Skill metadata remains responsible for discovery, and each `SKILL.md` and its references describe how to call that skill.

## Startup probe policy

Each validator starts one `startup-probe.py` supervisor per run. It first checks the installed-asset inventory in a forked child of that same interpreter, using the `installed-assets.py` beside the validator, and then runs the validator's CLI or help probe if it has one; a failed inventory check ends the validator before any installed code runs. With no `GOG_KEYRING_PASSWORD_FILE`, the six validators start `python3` seven times in total: one supervisor each plus `soul_guardian.py --help`.

Each of those steps, like every other external validation probe, runs in its own session with its own 30-second deadline and 2-second termination grace. Override these with `OPENCLAW_PROBE_TIMEOUT_SECONDS` and `OPENCLAW_PROBE_GRACE_SECONDS`; both must be positive decimal seconds (fractions are allowed, but empty, zero, negative, non-finite, whitespace and exponent forms are rejected before any probe). Invalid policy exits 2, timeout exits 124, and handled HUP, INT or TERM exits 128 plus the signal number. Probe output is suppressed; captured output and file-backed passwords are private and trailing newlines are stripped.

The shared Python supervisor terminates the probe process group, escalates to KILL after the grace on timeout or cancellation, and reaps owned descendants on Linux. Residual group members are terminated on ordinary completion too, and the grace wait ends as soon as the group is gone. On Linux, descendants that left the group (for example with `setsid`) are still reaped: they receive TERM, then KILL after the grace, and the supervisor stops waiting for them after a second grace. Uninterruptible kernel waits are not covered. The final command is executed unchanged, outside the deadline, after supervision and capture files have been removed. Direct validator use requires POSIX sh, Python 3 with `waitid`/`WNOWAIT`, writable temporary storage, and both `startup-probe` helpers and `installed-assets.py` beside the validator; Linux also requires working `prctl` subreaper support. Darwin supports process-group termination for host tests; Linux is the descendant-reaping authority.

## Bootstrap completion event

The last entrypoint stage, `bootstrap-agent-container.py`, registers the built-in MCP server, starts `openclaw gateway`, waits for its real loopback `/readyz` (`--port`, else `OPENCLAW_GATEWAY_PORT`, else 18789; within `OPENCLAW_BOOTSTRAP_TIMEOUT_SECONDS`, default 120) and only then publishes `/tmp/maraclaw-bootstrap/AgentBootstrapCompleted.json`, bound to the current kernel boot id and PID 1 start time. An event from an earlier boot or container start never authorizes the next run. In the guest, `bootstrap-agent-container.py check-ready` and `bootstrap-agent-container.py exec -- <action>` require the current event and exit 75 while bootstrap is pending. Every other command is exec'd unchanged. A MaraClaw engine that waits for this event must run with an image built from this chain; older images never emit it and there is no fallback.

## Local build

Copy `.env.example` to `.env` if you want project-local configuration. Both scripts change to the Photon directory and source `.env` by default; assignments in that file override inherited environment variables. Set `ENV_FILE` to an explicit path to source a different file, or to a nonexistent path to use only the process environment. Pin defaults live only in the `Dockerfile.openclaw` ARGs; the scripts forward a pin as a build argument only when it is set and nonempty, so `.env.example` lists them commented out as overrides.

The local build loads one target (default `linux/arm64`) and defaults to `openclaw:local`. Its tag must match the engine's `MARACLAW_OPENCLAW_IMAGE` and it must be loaded on the same Docker daemon used by the worker. For example, with no overriding assignment in the selected env file:

```bash
MARACLAW_OPENCLAW_IMAGE=openclaw:2026.11.3 ./build-openclaw-local-dockerfile.sh
```

Set `DOCKER_PLATFORM=linux/amd64` for the other supported target. If you override a pin, override its paired version and checksum together.

Each `ARG` in the final stage is declared directly above the first `RUN` that reads it, and the apt layer is separate from the OpenClaw install, because BuildKit adds every build argument that has a value to the environment of each later `RUN`. Changing a pin therefore rebuilds from its first reader onward: a gogcli, OfficeCLI or skill archive pin, or `OPENCLAW_ASSET_MODE`, leaves the apt and OpenClaw layers cached. Keep new pins beside their reader, not at the top of the stage.

To reuse layers on a fresh builder, both scripts pass `OPENCLAW_BUILD_CACHE_FROM` as `--cache-from` and `OPENCLAW_BUILD_CACHE_TO` as `--cache-to`, each a BuildKit cache spec forwarded verbatim and only when set and nonempty (for example `type=local,src=/path/to/cache` and `type=local,dest=/path/to/cache,mode=max`, or `type=registry,ref=youruser/openclaw:buildcache`). No cache flag is passed by default, since the default docker driver can reject cache export, and the release workflow sets neither.

OpenClaw is installed with `npm ci` from the committed `docker/openclaw/openclaw-package/package-lock.json` into `/opt/openclaw`, and `/usr/local/bin/openclaw` links to it. The build fails if `OPENCLAW_VERSION` differs from the locked openclaw version, and it checks the OpenClaw tarball against both `OPENCLAW_SHA256` and the lock's integrity. To bump OpenClaw, update the version in `package.json` and `Dockerfile.openclaw`, delete the lock, and regenerate it:

```bash
docker run --rm --platform linux/arm64 -v "$PWD/docker/openclaw/openclaw-package":/work -w /work --entrypoint sh \
  nikolaik/python-nodejs:python3.15-nodejs26-slim@sha256:97081a38e971a9f06c2ae1fcf778d77c9e53f8c94fa1a9c9e300c1f3c9956f0a \
  -c 'npm install -g npm@12.2.2 && npm install --package-lock-only --install-strategy=shallow --ignore-scripts --allow-remote=all --no-audit --no-fund'
```

TraceLattice follows the same pattern with `docker/openclaw/tracelattice-package/`, `TRACELATTICE_VERSION` and `TRACELATTICE_SHA256`, installed into `/opt/tracelattice` with install scripts disabled. To bump it, update the version in that `package.json` and in `Dockerfile.openclaw`, update `TRACELATTICE_SHA256` to the new tarball digest, delete the lock, and run the command above with `docker/openclaw/tracelattice-package` as the mounted directory. Move the skill pins in `install-tracelattice-skill.sh` to the matching release commit at the same time.

Debian packages are resolved from `snapshot.debian.org` at `DEBIAN_SNAPSHOT`, so the apt pins stay installable after security updates supersede them; the image keeps those snapshot sources. When bumping a package pin, move the snapshot to a timestamp where every pinned version exists for amd64 and arm64.

### Pre-staged auxiliary assets

Online acquisition is the default. To use previously acquired assets, set `OPENCLAW_ASSET_MODE=staged` and `OPENCLAW_ASSET_DIR` to a local directory (paths containing spaces are supported). The local build requires the selected target and publication requires both targets:

```text
common/officecli-SKILL.md
common/gogcli-skills.tar.gz
common/soul-guardian.tar.gz
common/humanizer.tar.gz
common/tracelattice-skill.tar.gz
linux-amd64/gogcli.tar.gz
linux-amd64/officecli
linux-arm64/gogcli.tar.gz
linux-arm64/officecli
```

The existing pinned digests are checked before an asset is extracted, installed, or executed. Missing or corrupt staged inputs fail without an online fallback. Buildx provides the directory as the named `openclaw_assets` build context, mounted read-only only during installation; it is not copied into the image or added to the default context. This mode covers auxiliary assets only: base images, apt packages and the OpenClaw and TraceLattice npm packages may still require network access.

The image generates a root-owned installed-content inventory after installation. Each startup validator checks its own executables and skill trees against this image baseline, including additions, removals and relative symlink changes, before running component code. Validation is offline and runs on every invocation, including direct calls and restarts. It detects drift, not publisher authenticity, and cannot protect against root replacing both assets and inventory. Skill content (Tavily `SKILL.md` frontmatter and `openclaw.requires.env`, soul-guardian `skill.json` and `onboard_state_dir.py`) is checked once at build time by `docker/openclaw/check-skills.py`, before the inventory is created; at startup the validators verify the inventory, file presence and non-emptiness, and run the tool smoke commands (`gog --version`, `officecli --version`, `soul_guardian.py --help`). Build-time installers share `docker/openclaw/install-common.sh`, which stages, verifies and backs up payloads and restores them if installation fails.

## Publish

After `docker login`, publish both architectures under one versioned tag:

```bash
DOCKERHUB_NAMESPACE=youruser ./publish-openclaw-local-dockerfile.sh
```

Alternatively set `OPENCLAW_PUBLISH_IMAGE`; `IMAGE_TAG` defaults to `OPENCLAW_VERSION`, else to the Dockerfile's `ARG OPENCLAW_VERSION`, and `PUSH_LATEST=1` also publishes `latest`. The pushed manifest carries max-mode SLSA provenance and an SBOM attestation. Values in the selected env file take precedence over these command-line environment assignments.

### Release

Pushing a tag `openclaw-v<version>` (for example `openclaw-v2026.11.3`) runs the Photon Release workflow; `workflow_dispatch` from a tag ref works too. `<version>` must equal the Dockerfile's `ARG OPENCLAW_VERSION`, or the workflow fails before building. It needs the repository secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` and the repository variable `DOCKERHUB_NAMESPACE`. It publishes `<namespace>/openclaw:<version>` and `:latest` for both architectures, then signs the manifest-list digest with cosign keyless (Sigstore, GitHub OIDC). Verify a release with:

```bash
cosign verify \
  --certificate-identity-regexp '^https://github\.com/MaraClaw/Photon/\.github/workflows/release\.yml@refs/tags/openclaw-v.+$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  <namespace>/openclaw:<version>
```

Inspect the attestations with `docker buildx imagetools inspect <namespace>/openclaw:<version> --format '{{json .Provenance}}'` (or `'{{json .SBOM}}'`).

## Test

With uv and the Python version in `.python-version` (3.14.10 or newer) installed, run the standalone suite without Docker or other services. The dev extra includes pytest-xdist:

```bash
uv run --extra dev pytest -n auto
```

To activate the versioned pre-push hook in a clone, run:

```bash
git config core.hooksPath .githooks
```

The tests must pass for the push to proceed.

The real dual-architecture Docker integration smoke is opt-in. It needs Docker buildx, network access, both target execution environments, and host Node `v26.13.0`:

```bash
RUN_OPENCLAW_OFFICECLI_SMOKE=1 uv run --extra dev pytest tests/test_openclaw_officecli_container_smoke.py
```

The dual-platform validator QA additionally exercises normal offline, non-root entrypoint and direct-validator execution:

```bash
uv run --extra dev python tests/openclaw_validator_qa.py
OPENCLAW_ASSET_MODE=staged OPENCLAW_ASSET_DIR="/absolute/path/to/staged assets" uv run --extra dev python tests/openclaw_validator_qa.py
```

`OPENCLAW_QA_PLATFORMS` selects a comma list of `linux/amd64` and `linux/arm64` (default both), and `OPENCLAW_QA_CACHE_FROM`/`OPENCLAW_QA_CACHE_TO` pass BuildKit cache specs. Staged validator QA requires `common/` plus the layout of each selected architecture. CI runs this QA once per architecture with a GitHub Actions BuildKit cache, alongside the test suite and a lint job (ShellCheck, Hadolint, actionlint). Both QA modes need Docker Buildx, both target execution environments, and network access for the remaining build dependencies; staged QA also needs independently acquired inputs matching the pinned digests.
