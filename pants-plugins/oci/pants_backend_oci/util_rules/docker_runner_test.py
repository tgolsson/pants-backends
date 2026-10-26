import json
import os
import sys

import pytest

from pants_backend_oci.util_rules import docker_runner

FAKE_DOCKER = """\
import json, sys
with open({log!r}, "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
print("Loaded image: pants-abc:latest")
sys.exit({status})
"""


def fake_docker(tmp_path, status=0):
    docker = tmp_path / "docker"
    docker.write_text(f"#!{sys.executable}\n" + FAKE_DOCKER.format(log=str(tmp_path / "log"), status=status))
    docker.chmod(0o755)
    return str(docker)


def launch_args(docker):
    run = [docker, "run", "--rm", "--tty", "-p", "8080:80", "pants-abc:latest"]
    return ["--archive", "/image with spaces.tar", "--run", json.dumps(run)]


def test_loads_then_execs_the_run_with_the_users_args(tmp_path, monkeypatch, capfd):
    docker = fake_docker(tmp_path)
    captured = []
    monkeypatch.setattr(os, "execve", lambda *args: captured.append(args))
    monkeypatch.setenv("SCIE", "/launcher")
    command_args = ["sh", "-c", "a b", "", "--tty"]
    docker_runner.main([*launch_args(docker), "--", *command_args])
    load = json.loads((tmp_path / "log").read_text())
    assert load == ["load", "--quiet", "--input", "/image with spaces.tar"]
    executable, argv, env = captured[0]
    assert executable == docker
    assert argv == [docker, "run", "--rm", "--tty", "-p", "8080:80", "pants-abc:latest", *command_args]
    assert "SCIE" not in env
    # The load's output isn't the run's.
    assert capfd.readouterr().out == ""


def test_failed_load_stops_the_run(tmp_path, monkeypatch, capfd):
    docker = fake_docker(tmp_path, status=5)
    monkeypatch.setattr(os, "execve", lambda *args: pytest.fail("ran after a failed load"))
    assert docker_runner.main(launch_args(docker)) == 5
    assert "Loaded image" in capfd.readouterr().err


def test_missing_docker(tmp_path, capsys):
    assert docker_runner.main(launch_args(str(tmp_path / "nope"))) == 127
    assert "docker is missing" in capsys.readouterr().err
