"""Host-side launcher for running an OCI image with Docker (stdlib only).

Loads the image's OCI archive into the daemon, then execs the `docker run` command the rules built
with the Docker backend, followed by the user's arguments."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from pants_backend_oci.util_rules import container_build

_MISSING = "docker is missing. Install it: https://docs.docker.com/engine/install/"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--run", required=True, help="JSON argv of the `docker run` command.")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    run = json.loads(args.run)
    env = container_build.host_env()
    try:
        # Its "Loaded image" line isn't the run's output.
        load = subprocess.run(
            [run[0], "load", "--quiet", "--input", args.archive],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except FileNotFoundError:
        print(_MISSING, file=sys.stderr)
        return 127
    if load.returncode != 0:
        sys.stderr.write(load.stdout.decode(errors="replace"))
        return load.returncode
    run.extend(args.args[1:] if args.args[:1] == ["--"] else args.args)
    # Exec keeps docker's signal handling and exit status, including interactive repl.
    os.execve(run[0], run, env)


if __name__ == "__main__":
    sys.exit(main())
