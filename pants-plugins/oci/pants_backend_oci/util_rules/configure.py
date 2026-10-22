from __future__ import annotations

import shlex
from dataclasses import dataclass

from pants.core.util_rules.adhoc_binaries import PythonBuildStandaloneBinary
from pants.engine.intrinsics import create_digest
from pants.engine.process import Process
from pants.engine.rules import collect_rules, rule

from pants_backend_oci.util_rules import oci_layout_builder
from pants_backend_oci.util_rules.sandboxed import module_main, sandboxed_modules


@dataclass(frozen=True)
class SetCmdProcessRequest:
    pass


@rule
async def set_args(request: SetCmdProcessRequest, python: PythonBuildStandaloneBinary) -> Process:
    builder = await create_digest(sandboxed_modules(oci_layout_builder))
    python_argv = shlex.join((python.path, "-c", module_main(oci_layout_builder)))

    # The args are only known when the fused script runs, so the builder replaces the image's cmd
    # with them in the sandbox, before the image is unpacked. Without args it leaves the cmd alone,
    # and if it fails the run stops rather than starting the image with its old cmd.
    command = [
        rf"""
        # nosplit
        {python_argv} set-cmd -- "$@" > /dev/null || exit $?
        """
    ]

    return Process(
        tuple(command),
        input_digest=builder,
        description="Setting arguments",
    )


def rules():
    return collect_rules()
