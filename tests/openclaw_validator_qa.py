from pathlib import Path
import os
import sys
from uuid import uuid4

from pywhales import docker


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    run_id = uuid4().hex
    mode = os.environ.get("OPENCLAW_ASSET_MODE", "online")
    contexts: dict[str, Path] = {}
    if mode == "staged":
        asset_dir = os.environ.get("OPENCLAW_ASSET_DIR", "")
        if not asset_dir:
            raise RuntimeError("OPENCLAW_ASSET_DIR is required in staged mode")
        asset_root = Path(asset_dir).expanduser().resolve()
        required = [
            *(asset_root / f"linux-{architecture}" / name for architecture in ("amd64", "arm64") for name in ("gogcli.tar.gz", "officecli")),
            *(asset_root / "common" / name for name in ("officecli-SKILL.md", "gogcli-skills.tar.gz", "soul-guardian.tar.gz")),
        ]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise RuntimeError(f"missing staged assets: {', '.join(missing)}")
        contexts["openclaw_assets"] = asset_root
    elif mode != "online":
        raise RuntimeError(f"unsupported OPENCLAW_ASSET_MODE: {mode}")

    for architecture in ("amd64", "arm64"):
        platform = f"linux/{architecture}"
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
                build_args={"OPENCLAW_ASSET_MODE": mode},
                build_contexts=contexts,
            )
            print(f"Running validator for {platform}: {container}", flush=True)
            print(
                docker.run(
                    image,
                    ["sh", "-c", 'test "$(id -u)" != 0 && openclaw --version'],
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
                    ["sh", "-c", 'for validator in gogcli officecli soul-guardian linkup; do "/usr/local/bin/validate-$validator.sh" true || exit; done'],
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
                        'printf "\\n" >> "$(npm root -g)/openclaw/skills/officecli/SKILL.md" || exit 1; '
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
