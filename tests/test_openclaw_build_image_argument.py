from pathlib import Path

import pytest

from test_openclaw_build_script import (
    build_env,
    install_fake_docker,
    read_docker_argv,
    run_build_script,
    write_env_file,
)


@pytest.mark.parametrize(
    ("image_argument", "expected_image"),
    [
        ("openclaw:custom", "arm64-openclaw:custom"),
        ("", "arm64-openclaw:local"),
        ("openclaw:with spaces; $HOME 'quoted'", "arm64-openclaw:with spaces; $HOME 'quoted'"),
    ],
)
def test_image_argument_uses_default_architecture_as_one_tag(
    tmp_path: Path, image_argument: str, expected_image: str
) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    env = build_env(bin_dir, log_path, tmp_path / "missing.env")

    # When
    result = run_build_script(env, image_argument)

    # Then
    assert result.returncode == 0, result.stderr
    argv = read_docker_argv(log_path)
    assert argv[argv.index("-t") + 1] == expected_image
    assert argv[argv.index("--platform") + 1] == "linux/arm64"


@pytest.mark.parametrize("architecture", ["arm64", "amd64"])
@pytest.mark.parametrize("process_image", [None, "", "process/openclaw:override"])
def test_nonempty_process_image_overrides_image_argument(
    tmp_path: Path, architecture: str, process_image: str | None
) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    env = build_env(bin_dir, log_path, tmp_path / "missing.env")
    env["DOCKER_PLATFORM"] = f"linux/{architecture}"
    if process_image is not None:
        env["MARACLAW_OPENCLAW_IMAGE"] = process_image

    # When
    result = run_build_script(env, "openclaw:custom")

    # Then
    assert result.returncode == 0, result.stderr
    argv = read_docker_argv(log_path)
    assert argv[argv.index("-t") + 1] == f"{architecture}-{process_image or 'openclaw:custom'}"


@pytest.mark.parametrize("architecture", ["arm64", "amd64"])
@pytest.mark.parametrize(
    "image_selection",
    [
        ("file/openclaw:override", "openclaw:custom", "file/openclaw:override"),
        ("", "openclaw:custom", "openclaw:custom"),
        ("", "", "openclaw:local"),
    ],
)
def test_env_file_image_overrides_process_then_falls_through_to_argument_and_default(
    tmp_path: Path, architecture: str, image_selection: tuple[str, str, str]
) -> None:
    # Given
    file_image, image_argument, expected_image = image_selection
    bin_dir, log_path = install_fake_docker(tmp_path)
    env_file = tmp_path / "build.env"
    write_env_file(
        env_file,
        {
            "MARACLAW_OPENCLAW_IMAGE": file_image,
            "DOCKER_PLATFORM": f"linux/{architecture}",
        },
    )
    env = build_env(bin_dir, log_path, env_file)
    env["MARACLAW_OPENCLAW_IMAGE"] = "process/openclaw:override"
    env["DOCKER_PLATFORM"] = "linux/amd64" if architecture == "arm64" else "linux/arm64"

    # When
    result = run_build_script(env, image_argument)

    # Then
    assert result.returncode == 0, result.stderr
    argv = read_docker_argv(log_path)
    assert argv[argv.index("-t") + 1] == f"{architecture}-{expected_image}"
    assert argv[argv.index("--platform") + 1] == f"linux/{architecture}"


@pytest.mark.parametrize(
    "arguments",
    [
        ("-x",),
        ("--image=openclaw:custom",),
        ("--",),
        ("-",),
        ("openclaw:custom", "openclaw:extra"),
        ("openclaw:custom", "--help"),
        ("--help", "openclaw:custom"),
        ("-h", "openclaw:custom"),
    ],
)
def test_invalid_image_arguments_exit_before_sourcing_env_or_running_docker(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    # Given
    bin_dir, log_path = install_fake_docker(tmp_path)
    env_file = tmp_path / "build.env"
    env_file.write_text("exit 99\n", encoding="utf-8")
    env = build_env(bin_dir, log_path, env_file)

    # When
    result = run_build_script(env, *arguments)

    # Then
    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert not log_path.exists()
