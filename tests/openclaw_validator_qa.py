from pathlib import Path
import os
import sys
from uuid import uuid4

from pywhales import docker

SUPPORTED_PLATFORMS = ("linux/amd64", "linux/arm64")


def selected_platforms(value: str | None) -> list[str]:
    if value is None:
        return list(SUPPORTED_PLATFORMS)
    platforms = [item.strip() for item in value.split(",")]
    unsupported = [item for item in platforms if item not in SUPPORTED_PLATFORMS]
    if unsupported:
        raise RuntimeError(
            f"unsupported OPENCLAW_QA_PLATFORMS: {', '.join(repr(item) for item in unsupported)}; "
            f"expected a comma list of {', '.join(SUPPORTED_PLATFORMS)}"
        )
    if len(set(platforms)) != len(platforms):
        raise RuntimeError(f"duplicate OPENCLAW_QA_PLATFORMS: {value}")
    return platforms


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    run_id = uuid4().hex
    mode = os.environ.get("OPENCLAW_ASSET_MODE", "online")
    platforms = selected_platforms(os.environ.get("OPENCLAW_QA_PLATFORMS"))
    cache_from = os.environ.get("OPENCLAW_QA_CACHE_FROM") or None
    cache_to = os.environ.get("OPENCLAW_QA_CACHE_TO") or None
    contexts: dict[str, Path] = {}
    if mode == "staged":
        asset_dir = os.environ.get("OPENCLAW_ASSET_DIR", "")
        if not asset_dir:
            raise RuntimeError("OPENCLAW_ASSET_DIR is required in staged mode")
        asset_root = Path(asset_dir).expanduser().resolve()
        architectures = [platform.removeprefix("linux/") for platform in platforms]
        required = [
            *(asset_root / f"linux-{architecture}" / name for architecture in architectures for name in ("gogcli.tar.gz", "officecli")),
            *(asset_root / "common" / name for name in ("officecli-SKILL.md", "gogcli-skills.tar.gz", "soul-guardian.tar.gz", "humanizer.tar.gz", "tracelattice-skill.tar.gz")),
        ]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise RuntimeError(f"missing staged assets: {', '.join(missing)}")
        contexts["openclaw_assets"] = asset_root
    elif mode != "online":
        raise RuntimeError(f"unsupported OPENCLAW_ASSET_MODE: {mode}")

    for platform in platforms:
        architecture = platform.removeprefix("linux/")
        image = f"openclaw:validator-qa-{run_id}-{architecture}"
        container = f"openclaw-validator-qa-{run_id}-{architecture}"

        if docker.image.exists(image) or docker.container.exists(container):
            raise RuntimeError(f"QA resource already exists for {platform}")

        try:
            print(f"Building {platform}: {image}", flush=True)
            docker.buildx.build(
                root,
                platforms=[platform],
                load=True,
                file=root / "Dockerfile.openclaw",
                tags=image,
                progress="plain",
                cache_from=cache_from,
                cache_to=cache_to,
                build_args={"OPENCLAW_ASSET_MODE": mode},
                build_contexts=contexts,
            )
            print(f"Running validator for {platform}: {container}", flush=True)
            print(
                docker.run(
                    image,
                    ["sh", "-c", 'test "$(id -u)" != 0 && test "$HOME" = /home/node && test -w "$HOME/.openclaw" && test ! -w /usr/local/share/openclaw-integrity && openclaw --version'],
                    name=container,
                    remove=True,
                    networks=["none"],
                    platform=platform,
                ),
                end="",
            )
            print(f"Running direct validator QA for {platform}: {container}", flush=True)
            print(
                docker.run(
                    image,
                    ["sh", "-c", 'for validator in gogcli officecli soul-guardian tavily humanizer tracelattice; do "/usr/local/bin/validate-$validator.sh" true || exit; done'],
                    name=container,
                    remove=True,
                    networks=["none"],
                    platform=platform,
                    timeout=600,
                ),
                end="",
            )
            print(f"Running built-in MCP server QA for {platform}: {container}", flush=True)
            print(
                docker.run(
                    image,
                    [
                        "sh", "-c",
                        'state=$(mktemp -d) && export OPENCLAW_STATE_DIR="$state" OPENCLAW_CONFIG_PATH="$state/openclaw.json" && printf "{}" > "$OPENCLAW_CONFIG_PATH" '
                        '&& python3 -B -c "import sys; sys.path.insert(0, \\"/usr/local/bin\\"); import builtin_mcp; builtin_mcp.register(sys.exit)" '
                        "&& openclaw mcp probe tracelattice --json | grep -F tracelattice__sequentialthinking_tools",
                    ],
                    name=container,
                    remove=True,
                    networks=["none"],
                    platform=platform,
                    timeout=600,
                ),
                end="",
            )
            print(f"Running installed-asset tamper QA for {platform}: {container}", flush=True)
            print(
                docker.run(
                    image,
                    [
                        "-c",
                        'su -s /bin/sh node -c "/usr/local/bin/validate-officecli.sh true" || exit 1; '
                        'printf "\\n" >> /home/node/.openclaw/skills/officecli/SKILL.md || exit 1; '
                        'if su -s /bin/sh node -c "/usr/local/bin/validate-officecli.sh true"; then exit 1; fi',
                    ],
                    entrypoint="/bin/sh",
                    user="root",
                    name=container,
                    remove=True,
                    networks=["none"],
                    platform=platform,
                    timeout=600,
                ),
                end="",
            )
            print(f"Running Linux lifecycle QA for {platform}: {container}", flush=True)
            print(
                docker.run(
                    image,
                    ["python3", "-B", "/tmp/openclaw-probe-image-qa.py", "space value", "", "$(literal);*"],
                    name=container,
                    remove=True,
                    networks=["none"],
                    platform=platform,
                    envs={"OPENCLAW_PROBE_TIMEOUT_SECONDS": "10"},
                    volumes=[(root / "tests/openclaw_probe_image_qa.py", "/tmp/openclaw-probe-image-qa.py", "ro")],
                    timeout=600,
                ),
                end="",
            )
            print(f"Running cron context QA for {platform}: {container}", flush=True)
            print(
                docker.run(
                    image,
                    ["/tmp/openclaw-cron-context-qa.mjs"],
                    entrypoint="node",
                    name=container,
                    remove=True,
                    networks=["none"],
                    platform=platform,
                    volumes=[(root / "tests/openclaw_cron_context_qa.mjs", "/tmp/openclaw-cron-context-qa.mjs", "ro")],
                    timeout=120,
                ),
                end="",
            )
        finally:
            original_error = sys.exception()
            cleanup_errors: list[Exception] = []
            try:
                if docker.container.exists(container):
                    docker.container.remove(container, force=True, volumes=True)
            except Exception as error:
                cleanup_errors.append(error)
            try:
                if docker.image.exists(image):
                    docker.image.remove(image, prune=False)
            except Exception as error:
                cleanup_errors.append(error)
            if cleanup_errors:
                if original_error is None:
                    raise ExceptionGroup("QA cleanup failed", cleanup_errors)
                for error in cleanup_errors:
                    print(f"QA cleanup failed: {error}", file=sys.stderr)


if __name__ == "__main__":
    main()
