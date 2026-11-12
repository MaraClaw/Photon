import json
import re
import shlex
from pathlib import Path

from test_openclaw_build_script import ARCHIVE_PIN_NAMES, OFFICECLI_PINS
from test_openclaw_officecli_dockerfile import _instructions

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile.openclaw"
ARCHIVE_PIN_OWNERS = {
    "install-gogcli-skills": ("GOGCLI_SKILLS_COMMIT", "GOGCLI_SKILLS_SHA256"),
    "install-soul-guardian": ("CLAWSEC_COMMIT", "CLAWSEC_SHA256"),
    "install-humanizer": ("HUMANIZER_COMMIT", "HUMANIZER_SHA256"),
}
# The RUN that first reads each build argument, in Dockerfile order. A build argument with a
# value enters the environment of every later RUN, so one declared any earlier than this
# would invalidate layers that do not read it.
ARGUMENT_READERS = (
    ("apt-get -o Acquire::Retries=3 install", ("DEBIAN_SNAPSHOT", "TINI_VERSION", "CA_CERTIFICATES_VERSION", "CURL_VERSION", "LIBCURL4_VERSION", "BASH_VERSION", "LIBICU76_VERSION")),
    ("npm ci --prefix /opt/openclaw", ("OPENCLAW_VERSION", "OPENCLAW_SHA256")),
    ('tar -xzf "$GOGCLI_ARCHIVE"', ("TARGETOS", "TARGETARCH", "OPENCLAW_ASSET_MODE", "GOGCLI_VERSION", "GOGCLI_AMD64_SHA256", "GOGCLI_ARM64_SHA256")),
    ("sh /tmp/install-officecli.sh", tuple(OFFICECLI_PINS)),
    *((f"sh /tmp/{installer}.sh", names) for installer, names in ARCHIVE_PIN_OWNERS.items()),
)


def _final_stage() -> tuple[tuple[str, str], ...]:
    instructions, errors = _instructions(DOCKERFILE.read_text())
    assert not errors
    start = max(index for index, (instruction, _body) in enumerate(instructions) if instruction == "FROM")
    return instructions[start + 1:]


def test_each_build_argument_is_declared_once_directly_above_its_first_reader() -> None:
    stage = _final_stage()
    for needle, names in ARGUMENT_READERS:
        (reader,) = (index for index, (instruction, body) in enumerate(stage) if instruction == "RUN" and needle in body)
        block: list[str] = []
        cursor = reader - 1
        while cursor >= 0 and stage[cursor][0] == "ARG":
            block.append(stage[cursor][1].partition("=")[0])
            cursor -= 1
        assert sorted(block) == sorted(names), needle
        for name in names:
            earlier = [body for instruction, body in stage[:reader] if instruction == "RUN" and re.search(rf"\$\{{?{name}\b", body)]
            assert not earlier, f"{name} is read before it is declared"
    declared = [body.partition("=")[0] for instruction, body in stage if instruction == "ARG"]
    assert sorted(declared) == sorted(name for _needle, names in ARGUMENT_READERS for name in names)


def test_apt_and_openclaw_install_are_separate_layers_with_their_own_cleanup() -> None:
    runs = [body for instruction, body in _final_stage() if instruction == "RUN"]
    (apt,) = (body for body in runs if "apt-get" in body)
    (openclaw,) = (body for body in runs if "npm" in body)
    assert runs.index(apt) < runs.index(openclaw)
    assert "snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}" in apt
    assert apt.endswith("&& rm -rf /var/lib/apt/lists/*")
    steps = (
        "npm install -g npm@12.2.2",
        "sha256sum -c /tmp/openclaw.tgz.sha256",
        "sha512sum -c /tmp/openclaw.lock.sha512",
        "npm ci --prefix /opt/openclaw --install-strategy=shallow --omit=dev --no-audit --no-fund",
        "rm -rf /root/.npm",
    )
    positions = [openclaw.index(step) for step in steps]
    assert positions == sorted(positions)
    assert openclaw.endswith("&& rm -rf /root/.npm")


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
            for installer in ("install-gogcli-skills", "install-soul-guardian", "install-humanizer"):
                if f"sh /tmp/{installer}.sh" in body:
                    installer_args[installer] = stage_args.copy()
    assert stage_count == 4
    assert not global_args.intersection(ARCHIVE_PIN_NAMES)
    assert set(installer_args) == set(ARCHIVE_PIN_OWNERS)
    for installer, declared in installer_args.items():
        for name in ARCHIVE_PIN_OWNERS[installer]:
            assert declared.count(name) == 1, f"{name} must be declared, without a default, before {installer}"
    for name in ARCHIVE_PIN_NAMES:
        assert stage_args.count(name) == 1
    assert not runtime_env.intersection(ARCHIVE_PIN_NAMES)


def test_image_installs_pinned_cli_and_runs_validator_as_entrypoint() -> None:
    source = DOCKERFILE.read_text()
    lines = source.splitlines()
    entrypoint = next(line for line in lines if line.startswith("ENTRYPOINT "))
    command = next(line for line in lines if line.startswith("CMD "))
    assert json.loads(entrypoint.removeprefix("ENTRYPOINT ")) == [
        "tini", "-s", "--", "/usr/local/bin/validate-gogcli.sh",
        "/usr/local/bin/validate-officecli.sh", "/usr/local/bin/validate-soul-guardian.sh",
        "/usr/local/bin/validate-tavily.sh", "/usr/local/bin/validate-humanizer.sh", "/usr/local/bin/bootstrap-agent-container.py",
    ]
    assert json.loads(command.removeprefix("CMD ")) == ["openclaw", "gateway"]
    assert "COPY docker/openclaw/install-officecli.sh /tmp/install-officecli.sh" in lines
    assert "online) sh /tmp/install-officecli.sh ;;" in source
    assert 'staged) sh /tmp/install-officecli.sh --binary-file "/staged/linux-${TARGETARCH}/officecli" --skill-file /staged/common/officecli-SKILL.md ;;' in source
    for name in ("gogcli", "officecli", "soul-guardian", "tavily", "humanizer"):
        assert f"COPY --chmod=0755 docker/openclaw/validate-{name}.sh /usr/local/bin/validate-{name}.sh" in lines
        assert f"!docker/openclaw/validate-{name}.sh" in (DOCKERFILE.parent / ".dockerignore").read_text().splitlines()
    assert not [line for line in lines if line.startswith("RUN chmod")]
    assert "COPY docker/openclaw/tavily-skills/ /tmp/tavily-skills/" in lines
    assert "COPY docker/openclaw/install-tavily.sh /tmp/install-tavily.sh" in lines
    assert "RUN sh /tmp/install-tavily.sh && rm /tmp/install-tavily.sh /tmp/install-common.sh" in lines
    assert '!docker/openclaw/install-tavily.sh' in (DOCKERFILE.parent / '.dockerignore').read_text().splitlines()
    assert '!docker/openclaw/tavily-skills/' in (DOCKERFILE.parent / '.dockerignore').read_text().splitlines()
    assert '!docker/openclaw/tavily-skills/**' in (DOCKERFILE.parent / '.dockerignore').read_text().splitlines()
    installer = (DOCKERFILE.parent / 'docker/openclaw/install-tavily.sh').read_text()
    assert installer.startswith('#!/bin/sh\nset -eu\n')
    assert 'destination="$HOME/.openclaw/skills"' in installer
    assert 'install -d -m 0755 "$destination"' in installer
    assert 'source=/tmp/tavily-skills' in installer
    assert '"$source/manifest.json"' in installer
    assert 'cp -a -- "$stage/incoming/$name" "$destination/$name"' in installer
    assert 'chmod -R a+rX "$stage/incoming/$name"' in installer
    assert '"$destination"/tavily-*' not in installer
    assert 'rm -rf -- "$source"' in installer
    path = next(line for line in lines if line.startswith("ENV PATH="))
    assert "/usr/local/bin" in path.split()[1].removeprefix("PATH=").split(":")
    assert source.index("sh /tmp/install-officecli.sh") < source.index("USER node")
    assert "bootstrap-officecli" not in source


def test_shared_installer_helper_spans_the_installers_and_is_whitelisted() -> None:
    lines = DOCKERFILE.read_text().splitlines()
    helper_copy = lines.index("COPY docker/openclaw/install-common.sh /tmp/install-common.sh")
    installers = ("officecli", "gogcli-skills", "soul-guardian", "humanizer", "tavily")
    first = lines.index("COPY docker/openclaw/install-officecli.sh /tmp/install-officecli.sh")
    last_run = lines.index("RUN sh /tmp/install-tavily.sh && rm /tmp/install-tavily.sh /tmp/install-common.sh")
    assert helper_copy < first < last_run
    assert sum("rm " in line and "/tmp/install-common.sh" in line for line in lines) == 1
    assert "!docker/openclaw/install-common.sh" in (DOCKERFILE.parent / ".dockerignore").read_text().splitlines()
    for name in installers:
        installer = (DOCKERFILE.parent / f"docker/openclaw/install-{name}.sh").read_text()
        assert '# shellcheck source=install-common.sh\n. "$(dirname -- "$0")/install-common.sh"\n' in installer


def test_image_installs_and_includes_probe_helpers() -> None:
    lines = DOCKERFILE.read_text().splitlines()
    allowed = (DOCKERFILE.parent / ".dockerignore").read_text().splitlines()
    for helper in ("startup-probe.sh", "startup-probe.py", "bootstrap_event.py", "bootstrap-agent-container.py"):
        assert f"COPY --chmod=0755 docker/openclaw/{helper} /usr/local/bin/{helper}" in lines
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
    assert source.index("RUN sh /tmp/install-tavily.sh") < inventory < source.index("USER node")
    assert source.index("sh /tmp/install-humanizer.sh") < inventory
    assert "!docker/openclaw/install-humanizer.sh" in allowed
    assert "!docker/openclaw/check-skills.py" in allowed
    check = (
        "COPY docker/openclaw/check-skills.py /tmp/check-skills.py\n"
        'RUN python3 /tmp/check-skills.py --skills-root "$HOME/.openclaw/skills" && rm /tmp/check-skills.py\n'
    )
    assert source.index("RUN sh /tmp/install-tavily.sh") < source.index(check) < inventory
    assert '--skills-root "$HOME/.openclaw/skills"' in source[inventory:]
    assert "--gog-roots-file /usr/local/share/openclaw-integrity/gog-skill-roots.txt" in source[inventory:]
    assert "--output-dir /usr/local/share/openclaw-integrity" in source[inventory:]


def test_staged_assets_are_bind_mounted_and_use_pinned_gog_digest() -> None:
    source = DOCKERFILE.read_text()
    assert "FROM scratch AS openclaw_assets" in source
    assert "ARG OPENCLAW_ASSET_MODE=online" in source
    assert source.count("--mount=type=bind,from=openclaw_assets,target=/staged") == 5
    assert 'GOGCLI_ARCHIVE="/staged/linux-${TARGETARCH}/gogcli.tar.gz"' in source
    assert source.index('"${GOGCLI_SHA256#sha256:}" "$GOGCLI_ARCHIVE"') < source.index('tar -xzf "$GOGCLI_ARCHIVE"')
    assert "--archive-file /staged/common/gogcli-skills.tar.gz" in source
    assert "--archive-file /staged/common/soul-guardian.tar.gz" in source
    assert "--archive-file /staged/common/humanizer.tar.gz" in source
    assert source.count("--ownership-output /usr/local/share/openclaw-integrity/gog-skill-roots.txt") == 2
