"""Host-side launcher for OCI execution with podman (stdlib only).

Local Linux podman runs the archive from the plugin's own rootless store. Through a connection,
such as a `podman machine`, the image is loaded into the connection's store under `REMOTE_REPOSITORY`
first, since the remote client can't run an archive directly.

Also holds the podman store setup that `podman_builder` shares."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from pants_backend_oci.util_rules import container_build

# Whether podman runs locally or through a connection is the plugin's choice, made by
# `[oci].podman_connection`, so a connection in the environment is dropped, not followed.
_REMOTE_ENV = frozenset({"CONTAINER_HOST", "CONTAINER_CONNECTION"})

# Images loaded through a connection are named here, and nothing else in its store is touched.
REMOTE_REPOSITORY = "localhost/pants-oci-run"

_MISSING = "podman is missing. Install it: https://podman.io/docs/installation"


class PodmanUnavailable(Exception):
    pass


def check_local(what: str) -> None:
    if sys.platform != "linux":
        raise PodmanUnavailable(f"{what} requires local Linux podman.")
    if os.geteuid() == 0:
        raise PodmanUnavailable(f"{what} requires rootless podman: run Pants as an unprivileged user.")


def runtime_dir() -> Path:
    """The user's runtime directory, where podman keeps rootless runtime state."""
    if os.environ.get("XDG_RUNTIME_DIR"):
        return Path(os.environ["XDG_RUNTIME_DIR"])
    # Where podman looks without XDG_RUNTIME_DIR, as a systemd user session creates it.
    fallback = Path(f"/run/user/{os.geteuid()}")
    if fallback.is_dir() and fallback.stat().st_uid == os.geteuid():
        return fallback
    raise PodmanUnavailable("OCI containers need a runtime directory: set XDG_RUNTIME_DIR.")


def prepare_store(cache: Path) -> tuple[Path, Path, dict[str, str]]:
    """Create the store and its runtime directory; returns both and podman's environment."""
    cache = cache.resolve()
    # Podman ties a store to the runroot it was first opened with, so every run and build step on
    # it must derive the same one. Podman also rejects runroots longer than 50 characters, so it's
    # named by the store's identity, not its path.
    identity = hashlib.sha256(os.fsencode(cache)).hexdigest()[:12]
    runtime = runtime_dir() / f"oci-{identity}"
    for directory in (cache, runtime, runtime / "run", runtime / "tmp"):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = container_build.host_env(_REMOTE_ENV)
    return cache, runtime, env


def storage_args(cache: Path, runtime: Path) -> list[str]:
    """Global podman options selecting the plugin's own store."""
    return [
        "--remote=false",
        # Build steps run without the user's session environment, which the systemd cgroup manager
        # needs.
        "--cgroup-manager=cgroupfs",
        "--root",
        str(cache / "storage"),
        "--runroot",
        str(runtime / "run"),
        "--tmpdir",
        str(runtime / "tmp"),
        "--storage-driver=vfs",
    ]


def command(
    podman: str, cache: Path, archive: str, *, runtime: Path, interactive: bool, tty: bool
) -> list[str]:
    return [*_run(podman, storage_args(cache, runtime), interactive, tty), f"oci-archive:{archive}"]


def remote_args(connection: str) -> list[str]:
    """Global podman options selecting `connection`, or the default one when it's empty."""
    return ["--connection", connection] if connection else ["--remote"]


def remote_ref(digest: str) -> str:
    return f"{REMOTE_REPOSITORY}:{digest.removeprefix('sha256:')}"


def _run(podman: str, options: list[str], interactive: bool, tty: bool) -> list[str]:
    argv = [podman, *options, "run", "--rm", "--quiet"]
    if interactive:
        argv.append("--interactive")
    if tty:
        argv.append("--tty")
    return argv


def diagnose_remote(podman: str, connection: str, error: str, env: dict[str, str]) -> str:
    """Why podman couldn't reach `connection`, and what to do about it."""
    named = f"podman connection {connection!r}" if connection else "podman's default connection"
    message = f"Could not reach {named}:\n{error.strip()}\n"
    # The default connection may be a podman machine, or any other; podman's own error says which
    # failed, and a machine that is missing or stopped is the usual cause.
    if not connection:
        try:
            listed = subprocess.run(
                [podman, "machine", "list", "--format", "json"], env=env, capture_output=True, text=True
            )
            machines = json.loads(listed.stdout) if listed.returncode == 0 else None
        except (OSError, ValueError):
            machines = None
        if machines == []:
            return message + (
                "If you use podman machine, there is none. Create and start one:"
                " `podman machine init` then `podman machine start`."
            )
        if machines and not any(m.get("Running") for m in machines):
            default = next((m for m in machines if m.get("Default")), machines[0])
            name = default.get("Name", "")
            return message + f"The podman machine is not running. Start it: `podman machine start {name}`."
    return message + "Check `podman system connection list`, or set `[oci].podman_connection`."


def prepare_remote(
    podman: str, options: list[str], connection: str, archive: str, ref: str, env: dict[str, str]
) -> int:
    """Load the archive as `ref` unless the connection's store already has it; 0 on success."""
    # `image exists` exits 1 for a missing image, and 125 when podman can't get an answer.
    exists = subprocess.run(
        [podman, *options, "image", "exists", ref], env=env, capture_output=True, text=True
    )
    if exists.returncode == 0:
        return 0
    if exists.returncode != 1:
        print(diagnose_remote(podman, connection, exists.stderr, env), file=sys.stderr)
        return exists.returncode
    # Its "Loaded image" line isn't the run's output.
    load = subprocess.run(
        [podman, *options, "load", "--quiet", "--input", archive],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if load.returncode != 0:
        sys.stderr.write(load.stdout)
    return load.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--podman", required=True)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--archive", required=True)
    parser.add_argument(
        "--connection", help="Run through this podman connection, or the default one when empty."
    )
    parser.add_argument("--digest", help="The image's digest, which names it in the connection's store.")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--tty", action="store_true")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.connection is not None and not args.digest:
        parser.error("--connection needs --digest")
    if args.connection is None:
        try:
            check_local("OCI run/repl")
            cache, runtime, env = prepare_store(args.cache)
        except PodmanUnavailable as e:
            print(e, file=sys.stderr)
            return 1
        argv = command(
            args.podman, cache, args.archive, runtime=runtime, interactive=args.interactive, tty=args.tty
        )
    else:
        env = container_build.host_env(_REMOTE_ENV)
        options = remote_args(args.connection)
        ref = remote_ref(args.digest)
        try:
            prepared = prepare_remote(args.podman, options, args.connection, args.archive, ref, env)
        except FileNotFoundError:
            print(_MISSING, file=sys.stderr)
            return 127
        if prepared != 0:
            return prepared
        # The image was just found or loaded; it's never pulled from a registry.
        argv = [*_run(args.podman, options, args.interactive, args.tty), "--pull=never", ref]
    argv.extend(args.args[1:] if args.args[:1] == ["--"] else args.args)
    # Exec keeps podman's signal handling and exit status, including interactive repl.
    try:
        os.execve(args.podman, argv, env)
    except FileNotFoundError:
        print(_MISSING, file=sys.stderr)
        return 127


if __name__ == "__main__":
    sys.exit(main())
