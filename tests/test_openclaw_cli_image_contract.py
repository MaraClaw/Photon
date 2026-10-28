import json
import shlex
from pathlib import Path

from test_openclaw_officecli_dockerfile import _instructions

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile.openclaw"


def test_image_installs_pinned_cli_and_runs_validator_as_entrypoint() -> None:
    source = DOCKERFILE.read_text()
    lines = source.splitlines()
    entrypoint = next(line for line in lines if line.startswith("ENTRYPOINT "))
    command = next(line for line in lines if line.startswith("CMD "))
    assert json.loads(entrypoint.removeprefix("ENTRYPOINT ")) == [
        "tini", "-s", "--", "/usr/local/bin/validate-gogcli.sh",
        "/usr/local/bin/validate-officecli.sh", "/usr/local/bin/validate-soul-guardian.sh",
        "/usr/local/bin/validate-linkup.sh",
    ]
    assert json.loads(command.removeprefix("CMD ")) == ["openclaw", "gateway"]
    assert "COPY docker/openclaw/install-officecli.sh /tmp/install-officecli.sh" in lines
    assert "online) sh /tmp/install-officecli.sh ;;" in source
    assert 'staged) sh /tmp/install-officecli.sh --binary-file "/staged/linux-${TARGETARCH}/officecli" --skill-file /staged/common/officecli-SKILL.md ;;' in source
    assert "COPY docker/openclaw/validate-gogcli.sh /usr/local/bin/validate-gogcli.sh" in lines
    assert "RUN chmod 0755 /usr/local/bin/validate-gogcli.sh" in lines
    for name in ("officecli", "soul-guardian", "linkup"):
        assert f"COPY docker/openclaw/validate-{name}.sh /usr/local/bin/validate-{name}.sh" in lines
        assert f"!docker/openclaw/validate-{name}.sh" in (DOCKERFILE.parent / ".dockerignore").read_text().splitlines()
    chmod = next(line for line in lines if line.startswith("RUN chmod 0755 /usr/local/bin/validate-officecli.sh"))
    assert {f"/usr/local/bin/validate-{name}.sh" for name in ("officecli", "soul-guardian", "linkup")} <= set(shlex.split(chmod))
    assert "COPY docker/openclaw/linkup-skills/ /tmp/linkup-skills/" in lines
    assert "COPY docker/openclaw/install-linkup.sh /tmp/install-linkup.sh" in lines
    assert "RUN sh /tmp/install-linkup.sh && rm /tmp/install-linkup.sh" in lines
    assert '!docker/openclaw/install-linkup.sh' in (DOCKERFILE.parent / '.dockerignore').read_text().splitlines()
    assert '!docker/openclaw/linkup-skills/' in (DOCKERFILE.parent / '.dockerignore').read_text().splitlines()
    assert '!docker/openclaw/linkup-skills/**' in (DOCKERFILE.parent / '.dockerignore').read_text().splitlines()
    installer = (DOCKERFILE.parent / 'docker/openclaw/install-linkup.sh').read_text()
    assert installer.startswith('#!/bin/sh\nset -eu\n')
    assert 'destination="$(npm root -g)/openclaw/skills"' in installer
    assert 'install -d -m 0755 "$destination"' in installer
    assert 'cp -R /tmp/linkup-skills/linkup-* "$destination/"' in installer
    assert 'chmod -R a+rX "$destination"/linkup-*' in installer
    assert 'rm -rf /tmp/linkup-skills' in installer
    path = next(line for line in lines if line.startswith("ENV PATH="))
    assert "/usr/local/bin" in path.split()[1].removeprefix("PATH=").split(":")
    assert source.index("sh /tmp/install-officecli.sh") < source.index("USER node")
    assert "bootstrap-officecli" not in source


def test_image_installs_and_includes_probe_helpers() -> None:
    lines = DOCKERFILE.read_text().splitlines()
    allowed = (DOCKERFILE.parent / ".dockerignore").read_text().splitlines()
    for helper in ("startup-probe.sh", "startup-probe.py"):
        assert f"COPY docker/openclaw/{helper} /usr/local/bin/{helper}" in lines
        assert f"!docker/openclaw/{helper}" in allowed


def test_officecli_flags_are_effective_for_install_and_gateway() -> None:
    instructions, errors = _instructions(DOCKERFILE.read_text())
    assert not errors
    environment: dict[str, str] = {}
    install_environment: dict[str, str] = {}
    for instruction, body in instructions:
        if instruction == "ENV":
            for token in shlex.split(body):
                name, _, value = token.partition("=")
                environment[name] = value
        if instruction == "RUN" and "sh /tmp/install-officecli.sh" in body:
            install_environment = environment.copy()
    for name in ("OFFICECLI_SKIP_UPDATE", "OFFICECLI_NO_AUTO_RESIDENT"):
        assert install_environment.get(name) == "1"
        assert environment.get(name) == "1"


def test_image_inventory_is_created_after_installers_before_node() -> None:
    source = DOCKERFILE.read_text()
    allowed = (DOCKERFILE.parent / ".dockerignore").read_text().splitlines()
    assert "!docker/openclaw/installed-assets.py" in allowed
    assert "COPY docker/openclaw/installed-assets.py /usr/local/bin/installed-assets.py" in source
    inventory = source.index("RUN python3 /usr/local/bin/installed-assets.py create")
    assert source.index("RUN sh /tmp/install-linkup.sh") < inventory < source.index("USER node")
    assert '--skills-root "$(npm root -g)/openclaw/skills"' in source[inventory:]
    assert "--gog-roots-file /usr/local/share/openclaw-integrity/gog-skill-roots.txt" in source[inventory:]
    assert "--output-dir /usr/local/share/openclaw-integrity" in source[inventory:]


def test_staged_assets_are_bind_mounted_and_use_pinned_gog_digest() -> None:
    source = DOCKERFILE.read_text()
    assert "FROM scratch AS openclaw_assets" in source
    assert "ARG OPENCLAW_ASSET_MODE=online" in source
    assert source.count("--mount=type=bind,from=openclaw_assets,target=/staged") == 4
    assert 'GOGCLI_ARCHIVE="/staged/linux-${TARGETARCH}/gogcli.tar.gz"' in source
    assert source.index('"${GOGCLI_SHA256#sha256:}" "$GOGCLI_ARCHIVE"') < source.index('tar -xzf "$GOGCLI_ARCHIVE"')
    assert "--archive-file /staged/common/gogcli-skills.tar.gz" in source
    assert "--archive-file /staged/common/soul-guardian.tar.gz" in source
    assert source.count("--ownership-output /usr/local/share/openclaw-integrity/gog-skill-roots.txt") == 2
