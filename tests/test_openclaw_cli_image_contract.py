import json
import shlex
from pathlib import Path

from test_openclaw_build_script import ARCHIVE_PIN_NAMES, OFFICECLI_PINS
from test_openclaw_officecli_dockerfile import _instructions

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile.openclaw"


def test_officecli_build_arguments_are_available_to_installer_but_not_runtime() -> None:
    instructions, errors = _instructions(DOCKERFILE.read_text())
    assert not errors
    active_args: dict[str, str] = {}
    runtime_env: set[str] = set()
    install_args: dict[str, str] = {}
    for instruction, body in instructions:
        if instruction == "ARG":
            name, _, value = body.partition("=")
            active_args[name] = value
        if instruction == "ENV":
            runtime_env.update(token.partition("=")[0] for token in shlex.split(body))
        if instruction == "RUN" and "sh /tmp/install-officecli.sh" in body:
            install_args = active_args.copy()
    assert {name: install_args[name] for name in OFFICECLI_PINS} == OFFICECLI_PINS
    assert not runtime_env.intersection(OFFICECLI_PINS)


def test_archive_pin_arguments_are_final_stage_defaultless_and_not_runtime_env() -> None:
    instructions, errors = _instructions(DOCKERFILE.read_text())
    assert not errors
    global_args: set[str] = set()
    stage_args: list[str] = []
    runtime_env: set[str] = set()
    installer_args: dict[str, list[str]] = {}
    stage_count = 0
    for instruction, body in instructions:
        if instruction == "FROM":
            stage_count += 1
            stage_args = []
        if instruction == "ARG":
            if stage_count == 0:
                global_args.add(body.partition("=")[0])
            stage_args.append(body)
        if instruction == "ENV":
            runtime_env.update(token.partition("=")[0] for token in shlex.split(body))
        if instruction == "RUN":
            for installer in ("install-gogcli-skills", "install-soul-guardian"):
                if f"sh /tmp/{installer}.sh" in body:
                    installer_args[installer] = stage_args.copy()
    assert stage_count == 4
    assert not global_args.intersection(ARCHIVE_PIN_NAMES)
    assert set(installer_args) == {"install-gogcli-skills", "install-soul-guardian"}
    for declared in installer_args.values():
        for name in ARCHIVE_PIN_NAMES:
            assert declared.count(name) == 1
    assert not runtime_env.intersection(ARCHIVE_PIN_NAMES)


def test_image_installs_pinned_cli_and_runs_validator_as_entrypoint() -> None:
    source = DOCKERFILE.read_text()
    lines = source.splitlines()
    entrypoint = next(line for line in lines if line.startswith("ENTRYPOINT "))
    command = next(line for line in lines if line.startswith("CMD "))
    assert json.loads(entrypoint.removeprefix("ENTRYPOINT ")) == [
        "tini", "-s", "--", "/usr/local/bin/validate-gogcli.sh",
        "/usr/local/bin/validate-officecli.sh", "/usr/local/bin/validate-soul-guardian.sh",
        "/usr/local/bin/validate-linkup.sh", "/usr/local/bin/bootstrap-agent-container.py",
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
    assert 'source=/tmp/linkup-skills' in installer
    assert '"$source/manifest.json"' in installer
    assert 'cp -a -- "$stage/incoming/$name" "$destination/$name"' in installer
    assert 'chmod -R a+rX "$stage/incoming/$name"' in installer
    assert '"$destination"/linkup-*' not in installer
    assert 'rm -rf -- "$source"' in installer
    path = next(line for line in lines if line.startswith("ENV PATH="))
    assert "/usr/local/bin" in path.split()[1].removeprefix("PATH=").split(":")
    assert source.index("sh /tmp/install-officecli.sh") < source.index("USER node")
    assert "bootstrap-officecli" not in source


def test_image_installs_and_includes_probe_helpers() -> None:
    lines = DOCKERFILE.read_text().splitlines()
    allowed = (DOCKERFILE.parent / ".dockerignore").read_text().splitlines()
    for helper in ("startup-probe.sh", "startup-probe.py", "bootstrap_event.py", "bootstrap-agent-container.py"):
        assert f"COPY docker/openclaw/{helper} /usr/local/bin/{helper}" in lines
        assert f"!docker/openclaw/{helper}" in allowed
    chmod = next(line for line in lines if line.startswith("RUN chmod 0755 /usr/local/bin/validate-officecli.sh"))
    assert {"/usr/local/bin/bootstrap_event.py", "/usr/local/bin/bootstrap-agent-container.py"} <= set(shlex.split(chmod))


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
