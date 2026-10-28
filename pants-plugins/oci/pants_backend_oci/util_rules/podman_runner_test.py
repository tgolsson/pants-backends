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
    if os.getuid() == 0:
        pytest.skip("requires an unprivileged user for runtime directory ownership")
    monkeypatch.setattr(os, "geteuid", os.getuid)
    (tmp_path / "xdg").mkdir()
    monkeypatch.setattr(podman_runner, "runtime_dir", lambda uid: tmp_path / "xdg")


def launch_args(tmp_path):
    return [
        "--podman",
        "/usr/bin/podman",
        "--cache",
        str(tmp_path / "cache"),
        "--layout",
        "/image with spaces/build",
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
    image_index = argv.index("oci:/image with spaces/build:build")
    assert argv[image_index + 1 :] == command_args
    options = argv[:image_index]
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


def test_runtime_directory_is_the_session_one_when_it_is_yours(tmp_path):
    (tmp_path / "run" / str(os.getuid())).mkdir(parents=True)
    found = podman_runner.runtime_dir(os.getuid(), tmp_path / "run", tmp_path / "tmp")
    assert found == tmp_path / "run" / str(os.getuid())
    assert not (tmp_path / "tmp").exists()


def test_runtime_directory_falls_back_like_podman(tmp_path):
    (tmp_path / "tmp").mkdir()
    found = podman_runner.runtime_dir(os.getuid(), tmp_path / "run", tmp_path / "tmp")
    assert found == tmp_path / "tmp" / f"podman-run-{os.getuid()}"
    assert found.stat().st_mode & 0o777 == 0o700
    assert podman_runner.runtime_dir(os.getuid(), tmp_path / "run", tmp_path / "tmp") == found


def test_runtime_directory_rejects_a_fallback_that_is_not_yours(tmp_path):
    (tmp_path / "tmp").mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "tmp" / f"podman-run-{os.getuid()}").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(podman_runner.PodmanUnavailable):
        podman_runner.runtime_dir(os.getuid(), tmp_path / "run", tmp_path / "tmp")


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
            "-c",
            # The real runtime directory is left alone.
            "import sys, pathlib; from pants_backend_oci.util_rules import podman_runner as r;"
            " d = pathlib.Path(sys.argv.pop(1)); r.runtime_dir = lambda uid: d; sys.exit(r.main())",
            str(tmp_path),
            "--podman",
            str(fake),
            "--cache",
            str(tmp_path / "cache"),
            "--layout",
            "/image/build",
            "--",
            "a b",
            "",
            "$HOME",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 23
    assert json.loads(result.stdout)[-3:] == ["a b", "", "$HOME"]


def fake_podman(tmp_path, *, exists: int = 1, load: int = 0, machines: str = "[]", stderr: str = ""):
    """A podman that logs each call's arguments, answering `image exists` with `exists`."""
    log = tmp_path / "calls.jsonl"
    fake = tmp_path / "podman"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        f"open({str(log)!r}, 'a').write(json.dumps(args) + '\\n')\n"
        "if 'exists' in args:\n"
        f"    sys.stderr.write({stderr!r})\n"
        f"    sys.exit({exists})\n"
        "if 'load' in args:\n"
        "    print('Loaded image: localhost/pants-oci-run:abc')\n"
        f"    sys.exit({load})\n"
        "if 'machine' in args:\n"
        f"    print({machines!r})\n"
    )
    fake.chmod(0o755)
    return str(fake), log


def calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def remote_args(podman: str, connection: str, *extra: str) -> list[str]:
    return [
        "--podman",
        podman,
        "--cache",
        "/unused",
        "--archive",
        "/sandbox/image.tar",
        "--connection",
        connection,
        "--digest",
        "sha256:abc",
        *extra,
    ]


@pytest.mark.parametrize("connection,options", [("", ["--remote"]), ("vm", ["--connection", "vm"])])
@pytest.mark.parametrize("exists", [0, 1])
def test_remote_loads_the_image_once_then_runs_it(tmp_path, monkeypatch, connection, options, exists):
    # Neither a Linux host, a runtime directory nor an unprivileged user is needed remotely.
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setenv("CONTAINER_HOST", "ssh://elsewhere/run/podman.sock")
    captured = []
    monkeypatch.setattr(os, "execve", lambda *args: captured.append(args))
    podman, log = fake_podman(tmp_path, exists=exists)
    command_args = ["-c", "exit 3", "a b", "", "--tty"]
    podman_runner.main(remote_args(podman, connection, "--interactive", "--tty", "--", *command_args))

    ref = "localhost/pants-oci-run:abc"
    expected = [[*options, "image", "exists", ref]]
    if exists:
        expected.append([*options, "load", "--quiet", "--input", "/sandbox/image.tar"])
    assert calls(log) == expected
    executable, argv, env = captured[0]
    assert executable == podman
    assert argv == [
        podman,
        *options,
        "run",
        "--rm",
        "--quiet",
        "--interactive",
        "--tty",
        "--pull=never",
        ref,
        *command_args,
    ]
    # The plugin's own store and runtime options are for local podman only.
    assert not {"--remote=false", "--root", "--runroot", "--storage-driver=vfs"} & set(argv)
    assert "CONTAINER_HOST" not in env


def test_remote_load_failure_is_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(os, "execve", lambda *args: pytest.fail("ran after a failed load"))
    podman, _ = fake_podman(tmp_path, load=125)
    assert podman_runner.main(remote_args(podman, "")) == 125
    assert "Loaded image" in capsys.readouterr().err


@pytest.mark.parametrize(
    "machines,expected",
    [
        ("[]", "If you use podman machine, there is none"),
        (
            json.dumps(
                [{"Name": "other", "Running": False}, {"Name": "dev", "Default": True, "Running": False}]
            ),
            "`podman machine start dev`",
        ),
        (
            json.dumps([{"Name": "dev", "Default": True, "Running": True}]),
            "Could not reach podman's default connection",
        ),
    ],
)
def test_unreachable_machine_is_diagnosed(tmp_path, monkeypatch, capsys, machines, expected):
    monkeypatch.setattr(os, "execve", lambda *args: pytest.fail("ran without a connection"))
    podman, _ = fake_podman(tmp_path, exists=125, machines=machines, stderr="Cannot connect to Podman")
    assert podman_runner.main(remote_args(podman, "")) == 125
    err = capsys.readouterr().err
    assert expected in err
    # The default connection needn't be a machine, so podman's own error is always kept.
    assert "Cannot connect to Podman" in err


def test_unreachable_named_connection_is_diagnosed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(os, "execve", lambda *args: pytest.fail("ran without a connection"))
    podman, log = fake_podman(tmp_path, exists=125, stderr='connection "ci" not found')
    assert podman_runner.main(remote_args(podman, "ci")) == 125
    err = capsys.readouterr().err
    assert "Could not reach podman connection 'ci'" in err
    assert 'connection "ci" not found' in err
    assert "podman system connection list" in err
    # A named connection isn't a machine, so podman machine isn't asked about it.
    assert not any("machine" in call for call in calls(log))


def test_connection_needs_the_digest(tmp_path):
    podman, _ = fake_podman(tmp_path)
    args = remote_args(podman, "")
    with pytest.raises(SystemExit):
        podman_runner.main(args[: args.index("--digest")])


def test_connection_needs_the_archive(tmp_path, capsys):
    podman, _ = fake_podman(tmp_path)
    args = remote_args(podman, "")
    archive = args.index("--archive")
    with pytest.raises(SystemExit):
        podman_runner.main(args[:archive] + ["--layout", "/image/build"] + args[archive + 2 :])
    assert "--connection needs --digest and --archive" in capsys.readouterr().err


@pytest.mark.parametrize(
    "image,error",
    [
        ([], "one of the arguments --layout --archive is required"),
        (["--archive", "/image.tar"], "local podman needs --layout"),
        (["--layout", "/image/build", "--archive", "/image.tar"], "not allowed with argument"),
    ],
)
def test_local_needs_only_the_layout(tmp_path, local_user, capsys, image, error):
    with pytest.raises(SystemExit):
        podman_runner.main(["--podman", "/usr/bin/podman", "--cache", str(tmp_path / "cache"), *image])
    assert error in capsys.readouterr().err


def test_remote_missing_podman(tmp_path, capsys):
    assert podman_runner.main(remote_args(str(tmp_path / "missing"), "")) == 127
    assert "https://podman.io/docs/installation" in capsys.readouterr().err
