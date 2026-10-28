import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pants_backend_oci.util_rules import podman_runner


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """The runtime directory podman's state goes to, instead of the user's."""
    if os.getuid() == 0:
        pytest.skip("requires an unprivileged user for runtime directory ownership")
    (tmp_path / "xdg").mkdir()
    monkeypatch.setattr(podman_runner, "runtime_dir", lambda uid: tmp_path / "xdg")
    return tmp_path / "xdg"


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
def test_command_keeps_the_containers_args(interactive, tty):
    args = ["-c", 'printf "%s\\n" "$1"', "shell", "a b", "$(touch bad)", "", "--tty"]
    argv = podman_runner.command(
        "/usr/bin/podman", ["--root", "/store"], ["oci:/image:build"], args, interactive=interactive, tty=tty
    )
    flags = (["--interactive"] if interactive else []) + (["--tty"] if tty else [])
    assert argv == [
        "/usr/bin/podman",
        "--root",
        "/store",
        "run",
        "--rm",
        "--quiet",
        *flags,
        "oci:/image:build",
        *args,
    ]


def test_storage_is_the_plugins_own_store_and_never_remote():
    options = podman_runner.storage_args(Path("/cache"), Path("/xdg/oci-abc"))
    assert options[options.index("--root") + 1] == "/cache/storage"
    assert options[options.index("--runroot") + 1] == "/xdg/oci-abc/run"
    assert options[options.index("--tmpdir") + 1] == "/xdg/oci-abc/tmp"
    assert {"--remote=false", "--storage-driver=vfs"} <= set(options)


def test_store_gets_a_private_runroot_and_the_host_env(tmp_path, monkeypatch, runtime):
    monkeypatch.setenv("PATH", "/host/bin:/usr/bin")
    monkeypatch.setenv("CONTAINER_HOST", "ssh://remote/run/podman.sock")
    monkeypatch.setenv("CONTAINER_CONNECTION", "remote")
    monkeypatch.setenv("SCIE_BASE", "/sandbox/.oci_builder/nce")
    monkeypatch.delenv("HOME", raising=False)
    cache, store_runtime, env = podman_runner.prepare_store(tmp_path / "cache")
    assert cache == (tmp_path / "cache").resolve()
    assert store_runtime.parent == runtime
    assert (store_runtime / "run").stat().st_mode & 0o777 == 0o700
    assert env["XDG_RUNTIME_DIR"] == str(runtime)
    assert env["PATH"] == "/host/bin:/usr/bin"
    # Podman looks the home up itself; a connection or the launcher's variables don't reach it.
    assert not {"HOME", "CONTAINER_HOST", "CONTAINER_CONNECTION", "SCIE_BASE"} & set(env)


def test_runroot_is_the_same_for_every_run_on_a_store(tmp_path, runtime):
    first = podman_runner.prepare_store(tmp_path / "cache")[1]
    assert podman_runner.prepare_store(tmp_path / "cache")[1] == first
    assert podman_runner.prepare_store(tmp_path / "other")[1] != first
    # Podman rejects runroots over 50 characters, so the runroot adds a fixed 21 to the runtime
    # directory, not the store's path.
    assert len(str(first / "run")) == len(str(runtime)) + 21


@pytest.mark.parametrize(
    "platform,euid,error", [("darwin", 1000, "local Linux podman"), ("linux", 0, "unprivileged user")]
)
def test_local_needs_rootless_linux(platform, euid, error):
    with pytest.raises(podman_runner.PodmanUnavailable, match=error):
        podman_runner.check_local("OCI run", platform, euid)


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


REF = "localhost/pants-oci-run:abc"


@pytest.mark.parametrize("options", [["--remote"], ["--connection", "vm"]])
@pytest.mark.parametrize("exists", [0, 1])
def test_remote_loads_the_image_only_when_missing(tmp_path, options, exists):
    podman, log = fake_podman(tmp_path, exists=exists)
    assert podman_runner.prepare_remote(podman, options, "vm", "/sandbox/image.tar", REF, {}) == 0
    expected = [[*options, "image", "exists", REF]]
    if exists:
        expected.append([*options, "load", "--quiet", "--input", "/sandbox/image.tar"])
    assert calls(log) == expected


def test_remote_load_failure_is_reported(tmp_path, capsys):
    podman, _ = fake_podman(tmp_path, load=125)
    assert podman_runner.prepare_remote(podman, ["--remote"], "", "/image.tar", REF, {}) == 125
    assert "Loaded image" in capsys.readouterr().err


def test_remote_runs_the_loaded_image(tmp_path, monkeypatch):
    # Neither a Linux host, a runtime directory nor an unprivileged user is needed remotely.
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setenv("CONTAINER_HOST", "ssh://elsewhere/run/podman.sock")
    captured = []
    monkeypatch.setattr(os, "execve", lambda *args: captured.append(args))
    podman, _ = fake_podman(tmp_path, exists=0)
    podman_runner.main(remote_args(podman, "vm", "--tty", "--", "-c", "exit 3"))

    ((executable, argv, env),) = captured
    assert executable == podman
    options = ["--connection", "vm"]
    assert argv == [podman, *options, "run", "--rm", "--quiet", "--tty", "--pull=never", REF, "-c", "exit 3"]
    assert "CONTAINER_HOST" not in env


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
def test_unreachable_machine_is_diagnosed(tmp_path, machines, expected):
    podman, _ = fake_podman(tmp_path, machines=machines)
    message = podman_runner.diagnose_remote(podman, "", "Cannot connect to Podman", {})
    assert expected in message
    # The default connection needn't be a machine, so podman's own error is always kept.
    assert "Cannot connect to Podman" in message


def test_unreachable_named_connection_is_diagnosed(tmp_path):
    podman, log = fake_podman(tmp_path)
    message = podman_runner.diagnose_remote(podman, "ci", 'connection "ci" not found', {})
    assert "Could not reach podman connection 'ci'" in message
    assert 'connection "ci" not found' in message
    assert "podman system connection list" in message
    # A named connection isn't a machine, so podman machine isn't asked about it.
    assert calls(log) == []


def test_unreachable_connection_fails_the_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(os, "execve", lambda *args: pytest.fail("ran without a connection"))
    podman, _ = fake_podman(tmp_path, exists=125, stderr='connection "ci" not found')
    assert podman_runner.main(remote_args(podman, "ci")) == 125
    assert "Could not reach podman connection 'ci'" in capsys.readouterr().err


@pytest.mark.parametrize(
    "drop,add,error",
    [
        ("--digest", [], "--connection needs --digest and --archive"),
        ("--archive", ["--layout", "/image/build"], "--connection needs --digest and --archive"),
        ("--connection", [], "local podman needs --layout"),
    ],
)
def test_args_must_name_the_image_for_where_it_runs(capsys, drop, add, error):
    args = remote_args("/usr/bin/podman", "")
    i = args.index(drop)
    with pytest.raises(SystemExit):
        podman_runner.parse_args(args[:i] + add + args[i + 2 :])
    assert error in capsys.readouterr().err


def test_args_take_the_layout_or_the_archive(tmp_path, capsys):
    with pytest.raises(SystemExit):
        podman_runner.parse_args([*launch_args(tmp_path), "--archive", "/image.tar"])
    assert "not allowed with argument" in capsys.readouterr().err


def test_args_after_the_separator_are_the_containers(tmp_path):
    args = podman_runner.parse_args([*launch_args(tmp_path), "--tty", "--", "--interactive", ""])
    assert (args.tty, args.interactive, args.args) == (True, False, ["--interactive", ""])


@pytest.mark.parametrize("connection", [None, ""])
def test_missing_podman(tmp_path, monkeypatch, runtime, capsys, connection):
    if sys.platform != "linux":
        pytest.skip("requires Linux")
    podman = str(tmp_path / "missing")
    args = launch_args(tmp_path) if connection is None else remote_args("", connection)
    args[1] = podman
    assert podman_runner.main(args) == 127
    assert "https://podman.io/docs/installation" in capsys.readouterr().err
