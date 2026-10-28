import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from openclaw_probe_test_support import (
    INVALID_POLICY, POLICY_KEYS, clean_policy, exercise_final_child,
    exercise_invalid_policy, exercise_script_lookup, exercise_signal, exercise_timeout, install_hang, prepare_integrity, rebaseline,
)

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "docker/openclaw/linkup-skills"
VALIDATOR = ROOT / "docker/openclaw/validate-linkup.sh"
NAMES = ("linkup-search", "linkup-fetch")
REFERENCES = {
    "linkup-search": ("LINKUP_AGENT_QUERY_MENTAL_MODEL.md", "LINKUP_PROMPT_OPTIMIZER_KNOWLEDGE.md", "LINKUP_API_REFERENCE.md"),
    "linkup-fetch": ("LINKUP_API_REFERENCE.md",),
}
REQUIRED_ENV = {"LINKUP_API_BASE", "LINKUP_PROXY_TOKEN"}
VALID_METADATA = 'metadata: {"openclaw":{"requires":{"env":["LINKUP_API_BASE","LINKUP_PROXY_TOKEN"]}}}'


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("invalid", [False, True])
def test_linkup_slashless_script_lookup(installation: tuple[Path, dict[str, str]], tmp_path: Path, local: bool, invalid: bool) -> None:
    _, env = installation
    exercise_script_lookup((Path(env["VALIDATOR"]), env), tmp_path, (local, invalid))


@pytest.fixture
def installation(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    npm_root = tmp_path / "npm root with spaces"
    skills = npm_root / "openclaw/skills"
    skills.mkdir(parents=True)
    for name in NAMES:
        shutil.copytree(PACKAGES / name, skills / name)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npm = bin_dir / "npm"
    npm.write_text('#!/bin/sh\n[ "$*" = "root -g" ] || exit 2\n[ "${NPM_FAIL:-}" != 1 ] || exit 3\nprintf "%s\\n" "$NPM_ROOT"\n')
    npm.chmod(0o755)
    env = os.environ | {"PATH": f'{bin_dir}:{os.environ["PATH"]}', "NPM_ROOT": str(npm_root)}
    env.pop("LINKUP_API_BASE", None)
    env.pop("LINKUP_PROXY_TOKEN", None)
    clean_policy(env)
    prepare_integrity(tmp_path, skills, env, VALIDATOR)
    return skills, env


def test_bundled_packages_match_default_manifest() -> None:
    manifest = json.loads((PACKAGES / "manifest.json").read_text())
    assert manifest["skills"] == list(NAMES)
    assert manifest["default_skills"] == list(NAMES)
    assert {path.name for path in PACKAGES.glob("linkup-*") if path.is_dir()} == set(NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_payload_requests_use_authenticated_proxy(name: str) -> None:
    manifest = json.loads((PACKAGES / "manifest.json").read_text())
    text = (PACKAGES / name / "SKILL.md").read_text()
    requests = [
        shlex.split(command.replace("\\\n", ""))
        for block in re.findall(r"```shell\n(.*?)```", text, re.DOTALL)
        for command in block.strip().split("\n\n")
    ]
    assert name in manifest["skills"] and name in manifest["default_skills"]
    assert f"name: {name}\n" in text.split("---", 2)[1]
    metadata_line = next(line for line in text.split("---", 2)[1].splitlines() if line.startswith("metadata: "))
    metadata = json.loads(metadata_line.removeprefix("metadata: "))
    assert REQUIRED_ENV <= set(metadata["openclaw"]["requires"]["env"])
    endpoint = name.removeprefix("linkup-")
    assert requests
    for request in requests:
        assert request[0] == "curl"
        urls = [arg for arg in request if arg.startswith(("$LINKUP_API_BASE", "https://", "http://"))]
        assert len(urls) == 1
        assert urls[0] == f"$LINKUP_API_BASE/v1/{endpoint}" or urls[0].startswith(f"$LINKUP_API_BASE/v1/{endpoint}/")
        headers = [request[index + 1] for index, arg in enumerate(request) if arg == "-H"]
        assert "Authorization: Bearer $LINKUP_PROXY_TOKEN" in headers
    submission = requests[0]
    assert submission[submission.index("-X") + 1] == "POST"
    body = json.loads(submission[submission.index("-d") + 1])
    assert ("q" if endpoint == "search" else "url") in body


@pytest.mark.parametrize("exit_code", [0, 37])
def test_valid_installation_executes_command_with_original_arguments_and_status(
    installation: tuple[Path, dict[str, str]], exit_code: int,
) -> None:
    skills, env = installation
    arguments = ["space value", "", "$(literal);*", "--flag=value"]
    before = {path.relative_to(skills): path.read_bytes() for path in skills.rglob("*") if path.is_file()}
    result = subprocess.run(
        ["/bin/sh", env["VALIDATOR"], sys.executable, "-c", "import json,sys; print(json.dumps(sys.argv[2:])); sys.exit(int(sys.argv[1]))", str(exit_code), *arguments],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == exit_code
    assert json.loads(result.stdout) == arguments
    assert result.stderr == ""
    assert before == {path.relative_to(skills): path.read_bytes() for path in skills.rglob("*") if path.is_file()}


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("failure", ["missing", "empty", "malformed", "wrong-name", "empty-description", "invalid-description", "null-description"])
def test_invalid_skill_blocks_command(installation: tuple[Path, dict[str, str]], name: str, failure: str) -> None:
    skills, env = installation
    file = skills / name / "SKILL.md"
    if failure == "missing":
        file.unlink()
    elif failure == "empty":
        file.write_text("")
    elif failure == "malformed":
        file.write_text(f"---\nname: {name}\ndescription: broken\n{VALID_METADATA}\n")
    elif failure == "wrong-name":
        file.write_text(f"---\nname: wrong\ndescription: description\n{VALID_METADATA}\n---\n")
    else:
        description = {"empty-description": "# missing", "invalid-description": "[", "null-description": "null"}[failure]
        file.write_text(f"---\nname: {name}\ndescription: {description}\n{VALID_METADATA}\n---\n")
    if failure != "missing":
        rebaseline(env)
    result = subprocess.run(["/bin/sh", env["VALIDATOR"], "/bin/false"], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert result.stderr == ("Installed assets failed verification: linkup\n" if failure == "missing" else f"Required skill failed verification: {name}\n")


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("metadata", [
    "",
    'metadata: {broken}',
    'metadata: {"extra":NaN,"openclaw":{"requires":{"env":["LINKUP_API_BASE","LINKUP_PROXY_TOKEN"]}}}',
    'metadata: {}',
    'metadata: {"openclaw":{"requires":{"env":"LINKUP_API_BASE LINKUP_PROXY_TOKEN"}}}',
    'metadata: {"openclaw":{"requires":{"env":["LINKUP_API_BASE"]}}}',
    'metadata: {"openclaw":{"requires":{"env":["LINKUP_PROXY_TOKEN"]}}}',
    'metadata: {"openclaw":{"requires":{"env":["LINKUP_API_BASE", "LINKUP_PROXY_TOKEN", 1]}}}',
    'metadata: {"openclaw":{"requires":{"env":["LINKUP_API_BASE", "LINKUP_PROXY_TOKEN"]}}}\nmetadata: {}',
])
def test_invalid_metadata_blocks_command(
    installation: tuple[Path, dict[str, str]], name: str, metadata: str,
) -> None:
    skills, env = installation
    file = skills / name / "SKILL.md"
    file.write_text(f"---\nname: {name}\ndescription: description\n{metadata + chr(10) if metadata else ''}---\n")
    rebaseline(env)
    result = subprocess.run(["/bin/sh", env["VALIDATOR"], sys.executable, "-c", "pass"], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert result.stderr == f"Required skill failed verification: {name}\n"


def test_valid_metadata_with_additional_env_executes_command(installation: tuple[Path, dict[str, str]]) -> None:
    skills, env = installation
    file = skills / NAMES[0] / "SKILL.md"
    file.write_text('---\nname: linkup-search\ndescription: description\nmetadata: {"openclaw": {"requires": {"env": ["OTHER", "LINKUP_PROXY_TOKEN", "LINKUP_API_BASE"]}}}\n---\n')
    rebaseline(env)
    result = subprocess.run(["/bin/sh", env["VALIDATOR"], sys.executable, "-c", "pass"], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.parametrize("name,reference", [(name, ref) for name, refs in REFERENCES.items() for ref in refs])
@pytest.mark.parametrize("failure", ["missing", "empty"])
def test_invalid_reference_blocks_command(
    installation: tuple[Path, dict[str, str]], name: str, reference: str, failure: str,
) -> None:
    skills, env = installation
    file = skills / name / "references" / reference
    if failure == "missing":
        file.unlink()
    else:
        file.write_text("")
    if failure == "empty":
        rebaseline(env)
    result = subprocess.run(["/bin/sh", env["VALIDATOR"], "/bin/false"], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert result.stderr == ("Installed assets failed verification: linkup\n" if failure == "missing" else f"Required skill failed verification: {name}\n")


@pytest.mark.parametrize("failure", ["npm-failure", "empty-root"])
def test_npm_discovery_failure_blocks_command(installation: tuple[Path, dict[str, str]], failure: str) -> None:
    _, env = installation
    env["NPM_FAIL" if failure == "npm-failure" else "NPM_ROOT"] = "1" if failure == "npm-failure" else ""
    result = subprocess.run(["/bin/sh", env["VALIDATOR"], "/bin/false"], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert result.stderr == "Required skill failed verification: linkup\n"


@pytest.mark.parametrize("probe", ["npm", "awk", "python3"])
def test_linkup_hanging_probe_times_out(installation: tuple[Path, dict[str, str]], probe: str) -> None:
    skills, env = installation
    root = skills.parents[2]
    install_hang(root, env, root / "bin" / probe, python_probe="compile" if probe == "python3" else "")
    exercise_timeout(Path(env["VALIDATOR"]), env)


@pytest.mark.parametrize("key", POLICY_KEYS)
@pytest.mark.parametrize("value", INVALID_POLICY)
def test_invalid_policy_prevents_linkup_probes(installation: tuple[Path, dict[str, str]], key: str, value: str) -> None:
    skills, env = installation
    root = skills.parents[2]
    install_hang(root, env, root / "bin/npm")
    exercise_invalid_policy(Path(env["VALIDATOR"]), env, key, value)


@pytest.mark.parametrize("signum", [signal.SIGHUP, signal.SIGINT, signal.SIGTERM])
def test_linkup_signal_cleans_parsing_group(installation: tuple[Path, dict[str, str]], signum: int) -> None:
    skills, env = installation
    root = skills.parents[2]
    install_hang(root, env, root / "bin/awk")
    exercise_signal(Path(env["VALIDATOR"]), env, signum)


def test_linkup_restart_rechecks_hanging_probe(installation: tuple[Path, dict[str, str]]) -> None:
    skills, env = installation
    root = skills.parents[2]
    assert subprocess.run(["/bin/sh", env["VALIDATOR"], "/bin/sh", "-c", ":"], env=env, timeout=5, check=False).returncode == 0
    (root / ".linkup-checked").touch()
    install_hang(root, env, root / "bin/npm")
    exercise_timeout(Path(env["VALIDATOR"]), env)


def test_linkup_final_child_outlives_probe_deadlines(installation: tuple[Path, dict[str, str]]) -> None:
    skills, env = installation
    env["PROBE_READY"] = str(skills.parents[2] / "child-ready")
    os.mkfifo(env["PROBE_READY"])
    exercise_final_child(Path(env["VALIDATOR"]), env)


@pytest.mark.parametrize("name", NAMES)
def test_linkup_reference_tampering_prevents_discovery(installation: tuple[Path, dict[str, str]], name: str) -> None:
    skills, env = installation
    (skills / name / "references" / REFERENCES[name][0]).write_text("valid-looking reference\n")
    marker = skills.parent / "npm-ran"
    npm = Path(env["PATH"].split(os.pathsep)[0]) / "npm"
    npm.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 0\n')
    result = subprocess.run(["/bin/sh", env["VALIDATOR"], "/bin/sh", "-c", 'touch "$NPM_ROOT/child"'], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert not marker.exists()
    assert not (Path(env["NPM_ROOT"]) / "child").exists()


def test_untouched_linkup_restart_uses_same_inventory(installation: tuple[Path, dict[str, str]]) -> None:
    _, env = installation
    for _ in range(2):
        assert subprocess.run(["/bin/sh", env["VALIDATOR"], "/bin/sh", "-c", ":"], env=env, capture_output=True, text=True, check=False).returncode == 0
