"""Host-side launcher for local, rootless OCI execution (stdlib only)."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path


def command(
    podman: str, cache: Path, archive: str, *, runtime: Path, interactive: bool, tty: bool
) -> list[str]:
    argv = [
        podman,
        "--remote=false",
        "--root",
        str(cache / "storage"),
        "--runroot",
        str(runtime / "runroot"),
        "--tmpdir",
        str(runtime / "tmp"),
        "--storage-driver=vfs",
        "run",
        "--rm",
        "--quiet",
    ]
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
    if sys.platform != "linux" or os.environ.get("CONTAINER_HOST") or os.environ.get("CONTAINER_CONNECTION"):
        print(
            "OCI run/repl requires local Linux podman; "
            "remote podman and podman machine are not supported yet.",
            file=sys.stderr,
        )
        return 1
    if os.geteuid() == 0:
        print("OCI run/repl requires rootless podman: run Pants as an unprivileged user.", file=sys.stderr)
        return 1
    cache = args.cache.resolve()
    # Podman rejects runroots longer than 50 characters. Named caches can live under very
    # long paths, so namespace the short-lived runtime separately using the store's identity.
    identity = hashlib.sha256(os.fsencode(cache)).hexdigest()[:12]
    runtime = Path(f"/tmp/oci-podman-{os.geteuid()}-{identity}")
    runtime.mkdir(mode=0o700, exist_ok=True)
    if runtime.is_symlink() or runtime.stat().st_uid != os.geteuid():
        print(f"Unsafe podman runtime directory: {runtime}", file=sys.stderr)
        return 1
    runtime.chmod(0o700)
    for directory in (cache, runtime / "runroot", runtime / "tmp"):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = {**os.environ, "XDG_RUNTIME_DIR": str(runtime), "TMPDIR": str(runtime / "tmp")}
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
