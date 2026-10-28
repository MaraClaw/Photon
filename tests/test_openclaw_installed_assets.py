import os
import subprocess
import sys
from pathlib import Path

import pytest

from openclaw_probe_test_support import prepare_integrity, rebaseline

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def inventory(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    skills = tmp_path / "skills"
    skills.mkdir()
    env = os.environ.copy()
    prepare_integrity(tmp_path, skills, env, ROOT / "docker/openclaw/validate-gogcli.sh")
    return skills, env


def verify(env: dict[str, str], component: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, env["INTEGRITY_HELPER"], "verify", "--component", component, "--inventory-dir", env["INTEGRITY_DIR"]], capture_output=True, text=True, check=False)


@pytest.mark.parametrize("component,name", [("gogcli", "crabbox"), ("officecli", "officecli"), ("soul-guardian", "soul-guardian"), ("linkup", "linkup-fetch")])
@pytest.mark.parametrize("change", ["bytes", "missing", "extra"])
def test_owned_changes_are_rejected(inventory: tuple[Path, dict[str, str]], component: str, name: str, change: str) -> None:
    skills, env = inventory
    asset = skills / name / "fixture"
    assert verify(env, component).returncode == 0
    if change == "bytes":
        asset.write_text("valid-looking replacement\n")
    elif change == "missing":
        asset.unlink()
    else:
        (asset.parent / "extra").write_text("extra\n")
    assert verify(env, component).returncode == 1


def test_symlink_targets_are_recorded_without_following(inventory: tuple[Path, dict[str, str]], tmp_path: Path) -> None:
    skills, env = inventory
    outside = tmp_path / "outside"
    outside.write_text("before\n")
    link = skills / "crabbox" / "link"
    link.symlink_to(os.path.relpath(outside, link.parent))
    rebaseline(env)
    outside.write_text("after\n")
    assert verify(env, "gogcli").returncode == 0
    link.unlink()
    link.symlink_to("different")
    assert verify(env, "gogcli").returncode == 1


def test_ancestor_symlink_replacement_is_rejected(inventory: tuple[Path, dict[str, str]], tmp_path: Path) -> None:
    skills, env = inventory
    moved = tmp_path / "moved-skills"
    skills.rename(moved)
    skills.symlink_to(moved, target_is_directory=True)
    assert verify(env, "gogcli").returncode == 1


def test_empty_or_non_absolute_creation_inputs_are_rejected(inventory: tuple[Path, dict[str, str]]) -> None:
    _, env = inventory
    result = subprocess.run([sys.executable, env["INTEGRITY_HELPER"], "create", "--skills-root", "relative", "--gog-roots-file", env["INTEGRITY_ROOTS"], "--output-dir", env["INTEGRITY_DIR"]], capture_output=True, text=True, check=False)
    assert result.returncode == 1
