import shlex
from typing import Final

import pytest

from test_openclaw_officecli_dockerfile import DOCKERFILE, _instructions

INSTALL: Final = "npm ci --prefix /opt/openclaw --install-strategy=shallow --omit=dev --no-audit --no-fund"
CLEANUP: Final = "rm -rf /root/.npm"
VALID_SOURCE: Final = (
    "USER root\nRUN apt-get update && rm -rf /var/lib/apt/lists/*\n"
    f'RUN npm pack "openclaw@${{OPENCLAW_VERSION}}" \\\n'
    f"    && {INSTALL} \\\n"
    f"    && {CLEANUP}\nUSER node\nRUN npm cache verify\n"
)


def _assert_root_cache_cleanup(source: str) -> None:
    instructions, errors = _instructions(source)
    assert errors == ()
    install_runs = tuple(index for index, (name, body) in enumerate(instructions) if name == "RUN" and "npm ci --prefix /opt/openclaw" in body)
    assert len(install_runs) == 1, "exactly one RUN must install OpenClaw with npm ci"
    run_index = install_runs[0]
    users = tuple(body for name, body in instructions[:run_index] if name == "USER")
    assert users, "the npm ci RUN must explicitly select a user"
    assert users[-1] == "root", "the npm ci RUN must execute as root"

    # Only inspect this straight && chain; other RUNs contain unrelated shell syntax.
    commands = tuple(tuple(shlex.split(command)) for command in instructions[run_index][1].split("&&"))
    install = tuple(shlex.split(INSTALL))
    cleanup = tuple(shlex.split(CLEANUP))
    assert install in commands, "the npm ci RUN must install OpenClaw with the pinned options"
    assert cleanup in commands, "remove /root/.npm in the same root RUN as installation"
    assert commands.index(cleanup) > commands.index(install), "remove /root/.npm after npm install"
    assert commands[-1] == cleanup, "root npm cleanup must finish the npm ci RUN"


def test_root_npm_cache_is_removed_when_guest_install_layer_finishes() -> None:
    # Given the actual guest image recipe.
    source = DOCKERFILE.read_text(encoding="utf-8")

    # When checking the install layer contract.
    # Then root's cache is removed after installation, before that layer is committed.
    _assert_root_cache_cleanup(source)


def test_root_cache_contract_accepts_cleanup_when_in_the_install_run() -> None:
    # Given an apt layer, then a continued root install RUN, then node's independent cache work.
    source = VALID_SOURCE

    # When checking the install layer contract.
    # Then the correctly scoped cleanup is accepted.
    _assert_root_cache_cleanup(source)


@pytest.mark.parametrize(
    ("source", "expected_error"),
    [
        pytest.param(VALID_SOURCE.replace(f" \\\n    && {CLEANUP}", ""), "same root RUN", id="missing-cleanup"),
        pytest.param(
            VALID_SOURCE.replace(f" \\\n    && {CLEANUP}", "").replace(INSTALL, f"{CLEANUP} && {INSTALL}"),
            "after npm install",
            id="before-install",
        ),
        pytest.param(
            VALID_SOURCE.replace(f" \\\n    && {CLEANUP}", f"\nRUN {CLEANUP}"),
            "same root RUN",
            id="later-run",
        ),
        pytest.param(VALID_SOURCE.replace("/root/.npm", "/home/node/.npm"), "same root RUN", id="wrong-node-cache"),
        pytest.param(VALID_SOURCE.replace("USER root", "USER node"), "execute as root", id="wrong-user"),
        pytest.param(
            VALID_SOURCE.replace(f" \\\n    && {CLEANUP}", "").replace("apt-get update", f"apt-get update && {CLEANUP}"),
            "same root RUN",
            id="cleanup-in-apt-layer",
        ),
    ],
)
def test_root_cache_contract_rejects_cleanup_when_scope_or_order_is_wrong(source: str, expected_error: str) -> None:
    # Given an install recipe with one cleanup regression.
    # When checking the same layer contract used for the real Dockerfile.
    # Then the regression fails for its specific cleanup requirement.
    with pytest.raises(AssertionError, match=expected_error):
        _assert_root_cache_cleanup(source)
