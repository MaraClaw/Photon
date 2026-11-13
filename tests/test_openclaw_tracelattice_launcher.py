import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

LAUNCHER: Final = Path(__file__).resolve().parents[1] / "docker/openclaw/tracelattice.sh"
ENTRY: Final = "/opt/tracelattice/node_modules/@iworkforces/tracelattice/dist/cli.js"
FAKE_NODE: Final = """#!{python}
import json, os, sys
print(json.dumps([sys.argv[1:], os.environ.get("TRACELATTICE_TRANSPORT_TYPE"), os.environ["PASSTHROUGH"]]))
sys.exit(int(os.environ["STATUS"]))
"""


@pytest.mark.parametrize("inherited", [None, "streamable-http", "stdio"])
def test_launcher_runs_the_server_entry_with_node_on_stdio(tmp_path: Path, inherited: str | None) -> None:
    node = tmp_path / "node"
    node.write_text(FAKE_NODE.format(python=sys.executable))
    node.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if not key.startswith("TRACELATTICE_")}
    env |= {"PATH": f'{tmp_path}{os.pathsep}{env["PATH"]}', "PASSTHROUGH": "value", "STATUS": "37"}
    if inherited is not None:
        env["TRACELATTICE_TRANSPORT_TYPE"] = inherited
    args = ["space value", "", "$(literal);*"]
    result = subprocess.run(["sh", str(LAUNCHER), *args], env=env, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 37
    assert json.loads(result.stdout) == [[ENTRY, *args], "stdio", "value"]
    assert result.stderr == ""
