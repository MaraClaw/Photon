import shlex
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile.openclaw"


def _instructions(source: str) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    instructions: list[tuple[str, str]] = []
    errors: list[str] = []
    pending = ""
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        pending = f"{pending} {line}".strip()
        if line.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        keyword, separator, body = pending.partition(" ")
        if separator:
            body = body.strip()
            try:
                shlex.split(body)
            except ValueError:
                errors.append("malformed shell tokenization")
            else:
                instructions.append((keyword.upper(), body))
        pending = ""
    if pending:
        errors.append("malformed dangling Dockerfile continuation")
    return tuple(instructions), tuple(errors)
