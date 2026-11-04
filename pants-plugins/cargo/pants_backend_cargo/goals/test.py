"""`pants test` for cargo targets: one `cargo test --no-run` per workspace (or loose package)
builds every test executable, then each `cargo_library`, `cargo_binary` and `cargo_test` target
runs its own executable as its own cached process. Doctests are not run."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from pants.core.environments.target_types import EnvironmentField
from pants.core.goals.test import TestFieldSet, TestRequest, TestResult, TestSubsystem
from pants.engine.addresses import Address
from pants.engine.fs import Digest, DigestSubset, MergeDigests, PathGlobs
from pants.engine.intrinsics import (
    digest_subset_to_digest,
    execute_process,
    get_digest_contents,
    merge_digests,
)
from pants.engine.process import Process, ProcessCacheScope, fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import Target
from pants.util.frozendict import FrozenDict
from pants.util.logging import LogLevel
from pants_backend_cargo.subsystems import CargoTestSubsystem
from pants_backend_cargo.target_generator import PACKAGE_GENERATED_NAME, artifact_kind
from pants_backend_cargo.target_types import (
    CargoArtifactKindField,
    CargoArtifactNameField,
    CargoPackageNameField,
    SkipCargoTestsField,
)
from pants_backend_cargo.util_rules.cargo import CargoProcessRequest, manifest_path
from pants_backend_cargo.util_rules.platform import CargoPlatformRequest, cargo_platform
from pants_backend_cargo.util_rules.rustup import install_rust_toolchain
from pants_backend_cargo.util_rules.sandbox import CargoSourcesRequest, cargo_sources
from pants_backend_cargo.util_rules.workspace import CargoPackageMapping

from pants_backend_platforms.util_rules.platforms import KnownPlatforms

logger = logging.getLogger(__name__)

REPORT = "report.json"
ROOT_FILE = "sandbox-root"
BIN_DIR = "test-bins"

# Builds every test executable and copies them next to cargo's JSON report, which names
# them, so the outputs are known without knowing cargo's hashed file names in advance. A root
# may build none (every target `test = false` or missing its `required-features`).
_BUILD_SCRIPT = f"""\
set -euo pipefail
pwd -P > {ROOT_FILE}
"$CARGO" test --no-run --locked --message-format=json "$@" > {REPORT}
mkdir -p {BIN_DIR}
{{ grep -o '"executable":"[^"]*"' {REPORT} || true; }} | cut -d'"' -f4 | while read -r exe; do
    cp "$exe" {BIN_DIR}/
done
"""


@dataclass(frozen=True)
class CargoTestFieldSet(TestFieldSet):
    required_fields = (CargoArtifactKindField, CargoArtifactNameField, CargoPackageNameField)

    kind: CargoArtifactKindField
    artifact_name: CargoArtifactNameField
    environment: EnvironmentField

    @classmethod
    def opt_out(cls, tgt: Target) -> bool:
        return bool(tgt.get(SkipCargoTestsField).value)

    @property
    def package_address(self) -> Address:
        return Address(
            self.address.spec_path,
            target_name=self.address.target_name,
            generated_name=PACKAGE_GENERATED_NAME,
            parameters=self.address.parameters,
        )


class CargoTestRequest(TestRequest):
    field_set_type = CargoTestFieldSet
    tool_subsystem = CargoTestSubsystem


@dataclass(frozen=True)
class CargoTestBuildRequest:
    """The test build of one workspace (`root` is the `cargo_workspace`) or of one package
    outside any workspace (`root` is its `cargo_package_impl`)."""

    root: Address
    workspace: bool


@dataclass(frozen=True)
class TestExecutable:
    manifest_path: str  # build-root relative
    kind: str
    name: str
    path: str


@dataclass(frozen=True)
class CargoTestBuild:
    """The executables, the report naming them, the sources they ran against, and the `env`
    they were built with."""

    digest: Digest
    executables: tuple[TestExecutable, ...]
    env: FrozenDict[str, str]

    def find(self, spec_path: str, kind: str, name: str) -> TestExecutable | None:
        wanted = manifest_path(spec_path)
        for exe in self.executables:
            if exe.kind == kind and exe.name == name and exe.manifest_path == wanted:
                return exe
        return None


@rule(desc="Build the cargo test executables")
async def build_test_executables(request: CargoTestBuildRequest, known: KnownPlatforms) -> CargoTestBuild:
    toolchain, cargo, sources = await concurrently(
        install_rust_toolchain(**implicitly()),
        cargo_platform(CargoPlatformRequest(known.host)),
        cargo_sources(
            CargoSourcesRequest(
                frozenset([request.root]),
                include_packages=True,
                platform=known.host,
                dependent=request.root,
            )
        ),
    )
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            CargoProcessRequest(
                toolchain,
                (
                    f"--manifest-path={manifest_path(request.root.spec_path)}",
                    *(("--workspace",) if request.workspace else ()),
                ),
                description=f"cargo test --no-run for {request.root}",
                digest=sources.digest,
                script=_BUILD_SCRIPT,
                extra_shims=("grep", "cut", "cp", "mkdir"),
                output_files=(REPORT, ROOT_FILE),
                output_directories=(BIN_DIR,),
                env=FrozenDict({**sources.env, **cargo.env}),
                level=LogLevel.INFO,
            )
        )
    )

    outputs = await get_digest_contents(
        await digest_subset_to_digest(DigestSubset(result.output_digest, PathGlobs([REPORT, ROOT_FILE])))
    )
    by_path = {fc.path: fc.content.decode() for fc in outputs}
    sandbox_root = by_path[ROOT_FILE].strip()
    executables = []
    for line in by_path[REPORT].splitlines():
        message = json.loads(line)
        if message.get("reason") != "compiler-artifact" or not message.get("executable"):
            continue
        if not message.get("profile", {}).get("test"):
            continue
        kind = artifact_kind(message["target"]["kind"])
        if kind is None:
            continue
        executables.append(
            TestExecutable(
                manifest_path=os.path.relpath(message["manifest_path"], sandbox_root),
                kind=kind,
                name=message["target"]["name"],
                path=f"{BIN_DIR}/{message['executable'].rsplit('/', 1)[-1]}",
            )
        )

    digest = await merge_digests(MergeDigests([result.output_digest, sources.digest]))
    return CargoTestBuild(digest, tuple(executables), sources.env)


@rule(desc="Run a cargo test executable", level=LogLevel.DEBUG)
async def run_cargo_test(
    batch: CargoTestRequest.Batch[CargoTestFieldSet, Any],
    test_subsystem: TestSubsystem,
    mapping: CargoPackageMapping,
) -> TestResult:
    field_set = batch.single_element
    package = field_set.package_address
    workspace = mapping.workspace_for(package)
    build = await build_test_executables(
        CargoTestBuildRequest(workspace or package, workspace is not None), **implicitly()
    )
    spec_path = field_set.address.spec_path
    executable = build.find(spec_path, field_set.kind.value, field_set.artifact_name.value)
    if executable is None:
        # cargo builds no executable for a target whose `required-features` are not enabled.
        logger.warning(
            f"Skipping {field_set.address}: `cargo test --no-run` built no "
            f"{field_set.kind.value} test executable named `{field_set.artifact_name.value}` "
            "(are its `required-features` enabled?)"
        )
        return TestResult.no_tests_found(field_set.address, test_subsystem.output)

    # As `cargo test` would: from the package directory, with the sources and the build's
    # `env` at hand.
    result = await execute_process(
        Process(
            argv=(f"{{chroot}}/{executable.path}",),
            input_digest=build.digest,
            working_directory=spec_path,
            env={
                **build.env,
                "CARGO_MANIFEST_DIR": os.path.join("{chroot}", spec_path).rstrip("/"),
            },
            description=f"Run {field_set.address}",
            level=LogLevel.DEBUG,
            cache_scope=(
                ProcessCacheScope.PER_SESSION if test_subsystem.force else ProcessCacheScope.SUCCESSFUL
            ),
        ),
        **implicitly(),
    )
    return TestResult.from_fallible_process_result((result,), field_set.address, test_subsystem.output)


def rules():
    return [*collect_rules(), *CargoTestRequest.rules()]
