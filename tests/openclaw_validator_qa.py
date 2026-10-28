from pathlib import Path
import sys
from uuid import uuid4

from pywhales import docker


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    run_id = uuid4().hex

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
            )
            print(f"Running validator for {platform}: {container}", flush=True)
            print(
                docker.run(
                    image,
                    ["openclaw", "--version"],
                    name=container,
                    remove=True,
                    networks=["none"],
                    platform=platform,
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
