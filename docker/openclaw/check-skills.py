#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Final

TAVILY_SKILLS: Final = ("tavily-search", "tavily-crawl")
TAVILY_ENV: Final = frozenset({"TAVILY_API_BASE", "TAVILY_API_KEY"})
DESCRIPTION: Final = re.compile(r"description: [A-Za-z]")
DESCRIPTION_COLON: Final = re.compile(r":[ \t\n\r\f\v]")
DESCRIPTION_SCALAR: Final = re.compile(r"(null|true|false|yes|no|on|off)([ \t\n\r\f\v]*(#.*)?)?")


class SkillError(Exception):
    pass


def reject_constant(value: str) -> int:
    raise ValueError(value)


def frontmatter_metadata(text: str, name: str) -> str:
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    if not lines or lines[0] != "---":
        raise SkillError(name)
    found_name = found_description = False
    metadata: list[str] = []
    closed = False
    for line in lines[1:]:
        if line == "---":
            closed = True
            break
        if line.startswith("name: "):
            if line != f"name: {name}" or found_name:
                raise SkillError(name)
            found_name = True
        elif line.startswith("metadata: "):
            if metadata:
                raise SkillError(name)
            metadata.append(line[len("metadata: "):])
        elif DESCRIPTION.match(line):
            description = line[len("description: "):]
            if (
                found_description or DESCRIPTION_COLON.search(description) or description.endswith(":")
                or DESCRIPTION_SCALAR.fullmatch(description.lower())
            ):
                raise SkillError(name)
            found_description = True
        else:
            raise SkillError(name)
    if not (closed and found_name and found_description and metadata):
        raise SkillError(name)
    return metadata[0]


def check_tavily(skills_root: Path, name: str) -> None:
    try:
        text = (skills_root / name / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, ValueError) as error:
        raise SkillError(name) from error
    metadata = frontmatter_metadata(text, name)
    try:
        env = json.loads(metadata, parse_constant=reject_constant)["openclaw"]["requires"]["env"]
    except (ValueError, KeyError, TypeError, RecursionError) as error:
        raise SkillError(name) from error
    if not (isinstance(env, list) and all(isinstance(item, str) for item in env) and TAVILY_ENV <= set(env)):
        raise SkillError(name)


def check_soul_guardian(skills_root: Path) -> None:
    skill = skills_root / "soul-guardian"
    try:
        with open(skill / "skill.json", encoding="utf-8") as metadata:
            json.load(metadata)
        onboarding = skill / "scripts/onboard_state_dir.py"
        compile(onboarding.read_bytes(), str(onboarding), "exec")
    except (OSError, ValueError, SyntaxError, RecursionError) as error:
        raise SkillError("soul-guardian") from error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skills-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        check_soul_guardian(args.skills_root)
        for name in TAVILY_SKILLS:
            check_tavily(args.skills_root, name)
    except SkillError as error:
        print(f"Required skill failed verification: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
