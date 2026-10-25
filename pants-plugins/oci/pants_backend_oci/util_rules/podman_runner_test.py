import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pants_backend_oci.util_rules import podman_runner


@pytest.fixture
def local_user(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "xdg"))
    uid = os.getuid()
    if uid == 0:
        pytest.skip("requires an unprivileged user for runtime directory ownership")
    monkeypatch.setattr(os, "geteuid", lambda: uid)


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
    runroot = Path(options[options.index("--runroot") + 1])
    assert runroot.parent.parent == tmp_path / "xdg"
    assert runroot.stat().st_mode & 0o777 == 0o700
    assert env["XDG_RUNTIME_DIR"] == str(tmp_path / "xdg")
    assert env["PATH"] == "/host/bin:/usr/bin"


def test_runroot_is_the_same_for_every_run_on_a_store(tmp_path, local_user):
    first = podman_runner.prepare_store(tmp_path / "cache")[1]
    assert podman_runner.prepare_store(tmp_path / "cache")[1] == first
    assert podman_runner.prepare_store(tmp_path / "other")[1] != first
    # Podman rejects runroots over 50 characters, so the runroot adds a fixed 21 to the runtime
    # directory, not the store's path.
    assert len(str(first / "run")) == len(str(tmp_path / "xdg")) + 21


def test_runtime_directory_is_required(tmp_path, monkeypatch, local_user, capsys):
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    monkeypatch.setattr(os, "geteuid", lambda: 2**31)
    assert podman_runner.main(launch_args(tmp_path)) == 1
    assert "set XDG_RUNTIME_DIR" in capsys.readouterr().err


def test_remote_connection_is_ignored(tmp_path, monkeypatch, local_user):
    captured = []
    monkeypatch.setattr(os, "execve", lambda *args: captured.append(args))
    monkeypatch.setenv("CONTAINER_HOST", "ssh://remote/run/podman.sock")
    monkeypatch.setenv("CONTAINER_CONNECTION", "remote")
    podman_runner.main(launch_args(tmp_path))
    _, argv, env = captured[0]
    assert "--remote=false" in argv
    assert "CONTAINER_HOST" not in env and "CONTAINER_CONNECTION" not in env


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
        env={**os.environ, "XDG_RUNTIME_DIR": str(tmp_path / "xdg")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 23
    assert json.loads(result.stdout)[-3:] == ["a b", "", "$HOME"]
