import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pants_backend_oci.util_rules import podman_runner


@pytest.fixture
def local_user(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    uid = os.getuid()
    if uid == 0:
        pytest.skip("requires an unprivileged user for runtime directory ownership")
    monkeypatch.setattr(os, "geteuid", lambda: uid)
    monkeypatch.delenv("CONTAINER_HOST", raising=False)
    monkeypatch.delenv("CONTAINER_CONNECTION", raising=False)


def launch_args(tmp_path):
    return [
        "--podman",
        "/usr/bin/podman",
        "--cache",
        str(tmp_path / "cache"),
        "--archive",
        "/image with spaces.tar",
    ]


@pytest.mark.parametrize("interactive,tty", [(False, False), (False, True), (True, True)])
def test_launcher_preserves_args_and_isolates_storage(tmp_path, monkeypatch, local_user, interactive, tty):
    captured = []
    monkeypatch.setattr(os, "execve", lambda *args: captured.append(args))
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/unwritable-host-runtime")
    monkeypatch.setenv("PATH", "/host/bin:/usr/bin")
    flags = (["--interactive"] if interactive else []) + (["--tty"] if tty else [])
    command_args = ["-c", 'printf "%s\\n" "$1"', "shell", "a b", "$(touch bad)", "", "--tty"]
    podman_runner.main(launch_args(tmp_path) + flags + ["--", *command_args])
    executable, argv, env = captured[0]
    assert executable == "/usr/bin/podman"
    archive_index = argv.index("oci-archive:/image with spaces.tar")
    assert argv[archive_index + 1 :] == command_args
    options = argv[:archive_index]
    assert ("--interactive" in options) == interactive
    assert ("--tty" in options) == tty
    assert "--rm" in options and "--quiet" in options
    cache = tmp_path / "cache"
    assert options[options.index("--root") + 1] == str(cache / "storage")
    runtime = Path(env["XDG_RUNTIME_DIR"])
    assert options[options.index("--runroot") + 1] == str(runtime / "runroot")
    assert len(str(runtime / "runroot")) <= 50
    assert env["PATH"] == "/host/bin:/usr/bin"
    assert runtime.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("remote", ["CONTAINER_HOST", "CONTAINER_CONNECTION"])
def test_remote_rejected_before_storage_creation(tmp_path, monkeypatch, local_user, capsys, remote):
    monkeypatch.setenv(remote, "remote")
    assert podman_runner.main(launch_args(tmp_path)) == 1
    assert "local Linux podman" in capsys.readouterr().err
    assert not (tmp_path / "cache").exists()


def test_root_rejected(tmp_path, monkeypatch, local_user, capsys):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert podman_runner.main(launch_args(tmp_path)) == 1
    assert "unprivileged user" in capsys.readouterr().err


def test_missing_podman(tmp_path, monkeypatch, local_user, capsys):
    def missing(*args):
        raise FileNotFoundError

    monkeypatch.setattr(os, "execve", missing)
    assert podman_runner.main(launch_args(tmp_path)) == 127
    assert "https://podman.io/docs/installation" in capsys.readouterr().err


def test_exec_preserves_exit_status_and_argument_boundaries(tmp_path):
    if sys.platform != "linux" or os.geteuid() == 0:
        pytest.skip("requires an unprivileged Linux user")
    fake = tmp_path / "podman"
    fake.write_text(f"#!{sys.executable}\nimport sys,json\nprint(json.dumps(sys.argv[1:]))\nsys.exit(23)\n")
    fake.chmod(0o755)
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("CONTAINER_HOST", "CONTAINER_CONNECTION")
    }
    result = subprocess.run(
        [
            sys.executable,
            podman_runner.__file__,
            "--podman",
            str(fake),
            "--cache",
            str(tmp_path / "cache"),
            "--archive",
            "/image.tar",
            "--",
            "a b",
            "",
            "$HOME",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 23
    assert json.loads(result.stdout)[-3:] == ["a b", "", "$HOME"]
