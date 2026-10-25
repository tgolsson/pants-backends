"""Host-side launcher for local, rootless OCI execution (stdlib only).

Also holds the podman store setup that `podman_builder` shares."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

_LAUNCHER_ENV = frozenset({"PEX", "SCIE", "SCIE_ARGV0", "VIRTUAL_ENV"})
# Podman always runs locally (`--remote=false`), so a remote connection is dropped, not followed.
_REMOTE_ENV = frozenset({"CONTAINER_HOST", "CONTAINER_CONNECTION"})


class PodmanUnavailable(Exception):
    pass


def check_local(what: str) -> None:
    if sys.platform != "linux":
        raise PodmanUnavailable(f"{what} requires local Linux podman; podman machine is not supported yet.")
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
    # Drop what launching `oci-builder` set, which names host paths.
    dropped = _LAUNCHER_ENV | _REMOTE_ENV
    env = {k: v for k, v in os.environ.items() if k not in dropped and not k.startswith("__PEX_")}
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
    argv = [podman, *storage_args(cache, runtime), "run", "--rm", "--quiet"]
    if interactive:
        argv.append("--interactive")
    if tty:
        argv.append("--tty")
    argv.append(f"oci-archive:{archive}")
    return argv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--podman", required=True)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--tty", action="store_true")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        check_local("OCI run/repl")
        cache, runtime, env = prepare_store(args.cache)
    except PodmanUnavailable as e:
        print(e, file=sys.stderr)
        return 1
    argv = command(
        args.podman, cache, args.archive, runtime=runtime, interactive=args.interactive, tty=args.tty
    )
    argv.extend(args.args[1:] if args.args[:1] == ["--"] else args.args)
    # Exec keeps podman's signal handling and exit status, including interactive repl.
    try:
        os.execve(args.podman, argv, env)
    except FileNotFoundError:
        print("podman is missing. Install it: https://podman.io/docs/installation", file=sys.stderr)
        return 127


if __name__ == "__main__":
    sys.exit(main())
