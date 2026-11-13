import copy
import json
import re
from typing import Any, Final

from test_openclaw_dockerfile_trixie_pins import DOCKERFILE, REPO_ROOT, _argument_values

PACKAGE_DIR: Final = REPO_ROOT / "docker" / "openclaw" / "tracelattice-package"
NAME: Final = "@iworkforces/tracelattice"
LOCKED: Final = f"node_modules/{NAME}"
EXACT_VERSION: Final = re.compile(r"\d+\.\d+\.\d+")
REGISTRY: Final = "https://registry.npmjs.org/"


def _load(name: str) -> dict[str, Any]:
    return json.loads((PACKAGE_DIR / name).read_text(encoding="utf-8"))


def _lock_errors(package: dict[str, Any], lock: dict[str, Any], version: str) -> tuple[str, ...]:
    errors: list[str] = []
    packages = lock["packages"]
    if package.get("dependencies") != {NAME: version} or not EXACT_VERSION.fullmatch(version):
        errors.append(f"package.json must depend only on {NAME} at the exact version {version}")
    if "allowScripts" in package:
        errors.append("the server is installed with --ignore-scripts, so no script may be approved")
    if lock.get("lockfileVersion") != 3:
        errors.append("lockfileVersion must be 3")
    if packages.get("", {}).get("dependencies") != package.get("dependencies"):
        errors.append("lock root dependencies must equal package.json")
    if packages.get(LOCKED, {}).get("version") != version:
        errors.append(f"locked tracelattice version must equal {version}")
    for path, entry in packages.items():
        if not path:
            continue
        if "integrity" not in entry:
            errors.append(f"{path} has no integrity")
        elif not str(entry.get("resolved", "")).startswith(REGISTRY):
            errors.append(f"{path} does not resolve to the npm registry")
    return tuple(errors)


def test_tracelattice_lock_matches_dockerfile_version_and_pins_every_dependency() -> None:
    version = _argument_values(DOCKERFILE.read_text(encoding="utf-8"))["TRACELATTICE_VERSION"]
    assert _lock_errors(_load("package.json"), _load("package-lock.json"), version) == ()


def test_lock_contract_rejects_version_drift_and_unverifiable_packages() -> None:
    package, lock = _load("package.json"), _load("package-lock.json")
    version = package["dependencies"][NAME]
    drifted_package = copy.deepcopy(package)
    drifted_package["dependencies"][NAME] = f"^{version}"
    unlocked = copy.deepcopy(lock)
    unlocked["packages"][LOCKED]["version"] = "0.0.0"
    no_integrity = copy.deepcopy(lock)
    del no_integrity["packages"][LOCKED]["integrity"]
    git_source = copy.deepcopy(lock)
    git_source["packages"][LOCKED]["resolved"] = "git+https://example.invalid/tracelattice.git"
    scripts = copy.deepcopy(package)
    scripts["allowScripts"] = {NAME: True}

    assert _lock_errors(package, lock, version) == ()
    assert any("exact version" in error for error in _lock_errors(drifted_package, lock, version))
    assert any("locked tracelattice version" in error for error in _lock_errors(package, unlocked, version))
    assert any("no integrity" in error for error in _lock_errors(package, no_integrity, version))
    assert any("npm registry" in error for error in _lock_errors(package, git_source, version))
    assert any("--ignore-scripts" in error for error in _lock_errors(scripts, lock, version))
    assert any("exact version" in error for error in _lock_errors(package, lock, "9.9.9"))
