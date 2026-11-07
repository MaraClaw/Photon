import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "docker/openclaw/check-skills.py"
PACKAGES = ROOT / "docker/openclaw/tavily-skills"
NAMES = ("tavily-search", "tavily-crawl")
METADATA = 'metadata: {"openclaw":{"requires":{"env":["TAVILY_API_BASE","TAVILY_API_KEY"]}}}'
DOCUMENTS = {
    "empty": "",
    "no-opening-delimiter": "name: {name}\ndescription: description\n{metadata}\n---\n",
    "unclosed": "---\nname: {name}\ndescription: description\n{metadata}\n",
    "wrong-name": "---\nname: wrong\ndescription: description\n{metadata}\n---\n",
    "missing-name": "---\ndescription: description\n{metadata}\n---\n",
    "duplicate-name": "---\nname: {name}\nname: {name}\ndescription: description\n{metadata}\n---\n",
    "missing-description": "---\nname: {name}\n{metadata}\n---\n",
    "duplicate-description": "---\nname: {name}\ndescription: one\ndescription: two\n{metadata}\n---\n",
    "comment-description": "---\nname: {name}\ndescription: # missing\n{metadata}\n---\n",
    "flow-description": "---\nname: {name}\ndescription: [\n{metadata}\n---\n",
    "null-description": "---\nname: {name}\ndescription: null\n{metadata}\n---\n",
    "commented-boolean-description": "---\nname: {name}\ndescription: Yes  # comment\n{metadata}\n---\n",
    "mapping-description": "---\nname: {name}\ndescription: key: value\n{metadata}\n---\n",
    "trailing-colon-description": "---\nname: {name}\ndescription: Examples:\n{metadata}\n---\n",
    "missing-metadata": "---\nname: {name}\ndescription: description\n---\n",
    "duplicate-metadata": "---\nname: {name}\ndescription: description\n{metadata}\n{metadata}\n---\n",
    "unknown-line": "---\nname: {name}\ndescription: description\n{metadata}\nlicense: MIT\n---\n",
}
INVALID_METADATA = (
    "metadata: ",
    "metadata: {broken}",
    'metadata: {"extra":NaN,"openclaw":{"requires":{"env":["TAVILY_API_BASE","TAVILY_API_KEY"]}}}',
    "metadata: {}",
    'metadata: {"openclaw":{"requires":{"env":"TAVILY_API_BASE TAVILY_API_KEY"}}}',
    'metadata: {"openclaw":{"requires":{"env":["TAVILY_API_BASE"]}}}',
    'metadata: {"openclaw":{"requires":{"env":["TAVILY_API_KEY"]}}}',
    'metadata: {"openclaw":{"requires":{"env":["TAVILY_API_BASE", "TAVILY_API_KEY", 1]}}}',
)
VALID_DOCUMENTS = {
    "additional-env": '---\nname: {name}\ndescription: description\nmetadata: {{"openclaw": {{"requires": {{"env": ["OTHER", "TAVILY_API_KEY", "TAVILY_API_BASE"]}}}}}}\n---\n',
    "colon-without-space": "---\nname: {name}\ndescription: See https://example.com/docs\n{metadata}\n---\n",
    "boolean-prefix": "---\nname: {name}\ndescription: Nonsense\n{metadata}\n---\n",
    "body-after-delimiter": "---\nname: {name}\ndescription: description\n{metadata}\n---\nunknown: line\n",
}


@pytest.fixture
def skills(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    for name in NAMES:
        shutil.copytree(PACKAGES / name, root / name)
    guardian = root / "soul-guardian"
    (guardian / "scripts").mkdir(parents=True)
    (guardian / "skill.json").write_text("{}\n")
    (guardian / "scripts/onboard_state_dir.py").write_text('raise RuntimeError("must not execute")\n')
    return root


def check(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CHECKER), "--skills-root", str(root)], capture_output=True, text=True, check=False)


def assert_rejected(result: subprocess.CompletedProcess[str], name: str) -> None:
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == f"Required skill failed verification: {name}\n"


def test_bundled_tavily_skills_pass(skills: Path) -> None:
    result = check(skills)
    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert not list(skills.rglob("__pycache__"))


@pytest.mark.parametrize("name", NAMES)
def test_missing_skill_is_rejected(skills: Path, name: str) -> None:
    (skills / name / "SKILL.md").unlink()
    assert_rejected(check(skills), name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("failure", DOCUMENTS)
def test_invalid_skill_document_is_rejected(skills: Path, name: str, failure: str) -> None:
    (skills / name / "SKILL.md").write_text(DOCUMENTS[failure].format(name=name, metadata=METADATA))
    assert_rejected(check(skills), name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("metadata", INVALID_METADATA)
def test_invalid_metadata_is_rejected(skills: Path, name: str, metadata: str) -> None:
    (skills / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: description\n{metadata}\n---\n")
    assert_rejected(check(skills), name)


@pytest.mark.parametrize("document", VALID_DOCUMENTS)
def test_valid_skill_document_is_accepted(skills: Path, document: str) -> None:
    for name in NAMES:
        (skills / name / "SKILL.md").write_text(VALID_DOCUMENTS[document].format(name=name, metadata=METADATA))
    result = check(skills)
    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.parametrize("failure", ["missing-json", "malformed-json", "missing-onboarding", "broken-onboarding"])
def test_invalid_soul_guardian_is_rejected(skills: Path, failure: str) -> None:
    guardian = skills / "soul-guardian"
    if failure == "missing-json":
        (guardian / "skill.json").unlink()
    elif failure == "malformed-json":
        (guardian / "skill.json").write_text("{broken\n")
    elif failure == "missing-onboarding":
        (guardian / "scripts/onboard_state_dir.py").unlink()
    else:
        (guardian / "scripts/onboard_state_dir.py").write_text("def broken(:\n")
    assert_rejected(check(skills), "soul-guardian")
