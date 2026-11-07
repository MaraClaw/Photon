import copy
import json
import re
from pathlib import Path
from typing import Any, Final

from test_openclaw_dockerfile_trixie_pins import DOCKERFILE, REPO_ROOT, _argument_values

PACKAGE_DIR: Final = REPO_ROOT / "docker" / "openclaw" / "openclaw-package"
EXACT_VERSION: Final = re.compile(r"\d+\.\d+\.\d+")
REGISTRY: Final = "https://registry.npmjs.org/"


def _load(name: str) -> dict[str, Any]:
    return json.loads((PACKAGE_DIR / name).read_text(encoding="utf-8"))


def _bundler_has_integrity(path: str, packages: dict[str, Any]) -> bool:
    parts = path.split("/node_modules/")
    return any(
        not packages[ancestor].get("inBundle") and "integrity" in packages[ancestor]
        for ancestor in ("/node_modules/".join(parts[:count]) for count in range(1, len(parts)))
        if ancestor in packages
    )


def _lock_errors(package: dict[str, Any], lock: dict[str, Any], openclaw_version: str) -> tuple[str, ...]:
    errors: list[str] = []
    packages = lock["packages"]
    if package.get("dependencies") != {"openclaw": openclaw_version} or not EXACT_VERSION.fullmatch(openclaw_version):
        errors.append(f"package.json must depend only on openclaw at the exact version {openclaw_version}")
    if package.get("allowScripts") != {"openclaw": True}:
        errors.append("allowScripts must approve openclaw and nothing else")
    if lock.get("lockfileVersion") != 3:
        errors.append("lockfileVersion must be 3")
    if packages.get("", {}).get("dependencies") != package.get("dependencies"):
        errors.append("lock root dependencies must equal package.json")
    if packages.get("node_modules/openclaw", {}).get("version") != openclaw_version:
        errors.append(f"locked openclaw version must equal {openclaw_version}")
    for path, entry in packages.items():
        if not path:
            continue
        if entry.get("inBundle"):
            # Bundled files ship inside the bundler's tarball, which carries the integrity.
            if not _bundler_has_integrity(path, packages):
                errors.append(f"{path} is bundled by a package without integrity")
        elif "integrity" not in entry:
            errors.append(f"{path} has no integrity")
        elif not str(entry.get("resolved", "")).startswith(REGISTRY):
            errors.append(f"{path} does not resolve to the npm registry")
    return tuple(errors)


def test_openclaw_lock_matches_dockerfile_version_and_pins_every_dependency() -> None:
    # Given
    version = _argument_values(DOCKERFILE.read_text(encoding="utf-8"))["OPENCLAW_VERSION"]

    # When
    errors = _lock_errors(_load("package.json"), _load("package-lock.json"), version)

    # Then
    assert errors == ()


def test_openclaw_package_files_are_whitelisted_in_the_docker_context() -> None:
    # Given
    lines = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    # When
    # Then
    assert "!docker/openclaw/openclaw-package/package.json" in lines
    assert "!docker/openclaw/openclaw-package/package-lock.json" in lines


def test_lock_contract_rejects_version_drift_and_unverifiable_packages() -> None:
    # Given
    package, lock = _load("package.json"), _load("package-lock.json")
    version = package["dependencies"]["openclaw"]
    drifted_package = copy.deepcopy(package)
    drifted_package["dependencies"]["openclaw"] = f"^{version}"
    unlocked = copy.deepcopy(lock)
    unlocked["packages"]["node_modules/openclaw"]["version"] = "0.0.0"
    no_integrity = copy.deepcopy(lock)
    del no_integrity["packages"]["node_modules/openclaw"]["integrity"]
    git_source = copy.deepcopy(lock)
    git_source["packages"]["node_modules/openclaw"]["resolved"] = "git+https://example.invalid/openclaw.git"
    broad_scripts = copy.deepcopy(package)
    broad_scripts["allowScripts"]["esbuild"] = True

    # When
    # Then
    assert _lock_errors(package, lock, version) == ()
    assert any("exact version" in error for error in _lock_errors(drifted_package, lock, version))
    assert any("locked openclaw version" in error for error in _lock_errors(package, unlocked, version))
    assert any("no integrity" in error for error in _lock_errors(package, no_integrity, version))
    assert any("npm registry" in error for error in _lock_errors(package, git_source, version))
    assert any("allowScripts" in error for error in _lock_errors(broad_scripts, lock, version))
    assert any("exact version" in error for error in _lock_errors(package, lock, "9.9.9"))
