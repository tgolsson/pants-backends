from pants_backend_cargo import subsystems, target_generator
from pants_backend_cargo import target_types as tt
from pants_backend_cargo.goals import fmt, generate_lockfiles, package, run, tailor, test
from pants_backend_cargo.internal import build
from pants_backend_cargo.util_rules import (
    cargo,
    dependency_inference,
    platform,
    rustup,
    sandbox,
    workspace,
)

from pants_backend_cc.util_rules import toolchain as cc_toolchain
from pants_backend_platforms import register as platforms


def rules():
    return [
        *platforms.rules(),
        *cc_toolchain.rules(),
        *subsystems.rules(),
        *platform.rules(),
        *rustup.rules(),
        *cargo.rules(),
        *sandbox.rules(),
        *workspace.rules(),
        *dependency_inference.rules(),
        *target_generator.rules(),
        *build.rules(),
        *package.rules(),
        *run.rules(),
        *test.rules(),
        *fmt.rules(),
        *tailor.rules(),
        *generate_lockfiles.rules(),
    ]


def target_types():
    return tt.target_types()
