"""Two toy backends for the tests. `toy_artifact` writes a file naming the platform and compiler
it was built for and with, then its dependencies' files and its `tool`'s, built for the host.
`toy_bundle` concatenates its dependencies."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, PackageFieldSet
from pants.core.util_rules.system_binaries import BashBinary
from pants.engine.addresses import Address
from pants.engine.fs import Digest, MergeDigests
from pants.engine.internals.graph import resolve_target, resolve_targets
from pants.engine.intrinsics import merge_digests
from pants.engine.platform import Platform
from pants.engine.process import Process, fallible_to_exec_result_or_raise
from pants.engine.rules import QueryRule, implicitly, rule
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    Dependencies,
    DependenciesRequest,
    StringField,
    Target,
    WrappedTargetRequest,
)
from pants.engine.unions import UnionRule
from pants.testutil.rule_runner import RuleRunner
from pants.util.frozendict import FrozenDict

from pants_backend_platforms import register
from pants_backend_platforms.target_types import PlatformsField
from pants_backend_platforms.util_rules.contract import (
    PackageForPlatformsRequest,
    PlatformBuild,
    PlatformBuildFieldSet,
    PlatformDependenciesRequest,
    build_for_platform,
    build_platform_dependencies,
    package_for_platforms,
)
from pants_backend_platforms.util_rules.platforms import KnownPlatforms, TargetPlatform
from pants_backend_platforms.util_rules.toolchain import (
    CcToolchain,
    CcToolchainRequest,
    resolve_cc_toolchain,
)

HOST_OS, HOST_ARCH = {
    Platform.linux_x86_64: ("linux", "x86_64"),
    Platform.linux_arm64: ("linux", "aarch64"),
    Platform.macos_x86_64: ("macos", "x86_64"),
    Platform.macos_arm64: ("macos", "aarch64"),
}[Platform.create_for_localhost()]
HOST = f"{HOST_OS}-{HOST_ARCH}"
OTHER = "windows-x86_64"


class ToyTextField(StringField):
    alias = "text"
    required = True
    help = "What the artifact says."


class ToyToolField(StringField):
    alias = "tool"
    help = "A `toy_artifact` in the same directory, built for the host and read by this build."


class ToyDependencies(Dependencies):
    pass


class ToyArtifact(Target):
    alias = "toy_artifact"
    core_fields = (*COMMON_TARGET_FIELDS, ToyTextField, ToyToolField, ToyDependencies, PlatformsField)
    help = "A test artifact."


class ToyBundle(Target):
    alias = "toy_bundle"
    core_fields = (*COMMON_TARGET_FIELDS, ToyDependencies, PlatformsField)
    help = "A test bundle of its dependencies."


@dataclass(frozen=True)
class ToyFieldSet(PackageFieldSet, PlatformBuildFieldSet):
    required_fields = (ToyTextField,)

    text: ToyTextField
    tool: ToyToolField
    dependencies: ToyDependencies


@dataclass(frozen=True)
class ToyBundleFieldSet(PackageFieldSet, PlatformBuildFieldSet):
    required_fields = (ToyDependencies, PlatformsField)

    dependencies: ToyDependencies

    @classmethod
    def opt_out(cls, tgt: Target) -> bool:
        return not isinstance(tgt, ToyBundle)


@dataclass(frozen=True)
class ToyShadowFieldSet(PlatformBuildFieldSet):
    """A second member for `toy_artifact`, which the contract rejects."""

    required_fields = (ToyTextField,)


@dataclass(frozen=True)
class ToyWrite:
    output: str
    header: str
    reads: tuple[str, ...]
    digests: tuple[Digest, ...]
    env: FrozenDict[str, str]
    description: str


@rule
async def toy_write(request: ToyWrite, bash: BashBinary) -> PlatformBuild:
    cat = shlex.join(request.reads) or "/dev/null"
    script = f"{{ echo {shlex.quote(request.header)}; cat {cat}; }} > {shlex.quote(request.output)}"
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            Process(
                argv=(bash.path, "-c", script),
                input_digest=await merge_digests(MergeDigests(request.digests)),
                output_files=(request.output,),
                env=request.env,
                description=request.description,
            )
        )
    )
    return PlatformBuild(result.output_digest, (request.output,))


@rule
async def build_toy(field_set: ToyFieldSet, platform: TargetPlatform, known: KnownPlatforms) -> PlatformBuild:
    toolchain = await resolve_cc_toolchain(CcToolchainRequest(platform), **implicitly())
    targets = await resolve_targets(**implicitly(DependenciesRequest(field_set.dependencies)))
    deps = await build_platform_dependencies(
        PlatformDependenciesRequest(field_set.address, tuple(targets), platform), **implicitly()
    )
    digests = [b.digest for b in deps.builds.values()]
    reads = [f for b in deps.builds.values() for f in b.files]
    if field_set.tool.value:
        tool_address = Address(field_set.address.spec_path, target_name=field_set.tool.value)
        wrapped = await resolve_target(
            WrappedTargetRequest(tool_address, description_of_origin="toy tool"), **implicitly()
        )
        tool = await build_for_platform(
            **implicitly(
                {ToyFieldSet.create(wrapped.target): PlatformBuildFieldSet, known.host: TargetPlatform}
            )
        )
        digests.append(tool.digest)
        reads.extend(tool.files)
    suffix = ".exe" if platform.os == "windows" else ""
    output = os.path.join(field_set.address.spec_path, f"{field_set.address.target_name}{suffix}.txt")
    header = f"{field_set.text.value} for {platform.name} with {os.path.basename(toolchain.cc.path)}"
    write = ToyWrite(output, header, tuple(reads), tuple(digests), toolchain.env, f"toy {field_set.address}")
    return await toy_write(write, **implicitly())


@rule
async def build_toy_bundle(field_set: ToyBundleFieldSet, platform: TargetPlatform) -> PlatformBuild:
    targets = await resolve_targets(**implicitly(DependenciesRequest(field_set.dependencies)))
    deps = await build_platform_dependencies(
        PlatformDependenciesRequest(field_set.address, tuple(targets), platform), **implicitly()
    )
    output = os.path.join(field_set.address.spec_path, f"{field_set.address.target_name}.bundle")
    write = ToyWrite(
        output,
        f"bundle for {platform.name}",
        tuple(f for b in deps.builds.values() for f in b.files),
        tuple(b.digest for b in deps.builds.values()),
        FrozenDict({"PATH": os.defpath}),
        f"toy {field_set.address}",
    )
    return await toy_write(write, **implicitly())


@rule
async def build_toy_shadow(field_set: ToyShadowFieldSet, platform: TargetPlatform) -> PlatformBuild:
    raise AssertionError("never dispatched: the contract rejects two members for one target")


@rule
async def package_toy(field_set: ToyFieldSet) -> BuiltPackage:
    return await package_for_platforms(PackageForPlatformsRequest(field_set), **implicitly())


@rule
async def package_toy_bundle(field_set: ToyBundleFieldSet) -> BuiltPackage:
    return await package_for_platforms(PackageForPlatformsRequest(field_set), **implicitly())


SHADOW_RULES = (build_toy_shadow, UnionRule(PlatformBuildFieldSet, ToyShadowFieldSet))


def rule_runner(*extra) -> RuleRunner:
    return RuleRunner(
        rules=[
            *register.rules(),
            toy_write,
            build_toy,
            build_toy_bundle,
            package_toy,
            package_toy_bundle,
            UnionRule(PackageFieldSet, ToyFieldSet),
            UnionRule(PackageFieldSet, ToyBundleFieldSet),
            UnionRule(PlatformBuildFieldSet, ToyFieldSet),
            UnionRule(PlatformBuildFieldSet, ToyBundleFieldSet),
            QueryRule(BuiltPackage, [ToyFieldSet]),
            QueryRule(BuiltPackage, [ToyBundleFieldSet]),
            QueryRule(CcToolchain, [CcToolchainRequest]),
            *extra,
        ],
        target_types=[*register.target_types(), ToyArtifact, ToyBundle],
    )


def platforms_build(*, toolchains: bool = True) -> str:
    """The host and Windows, with (`toolchains`) a `cc_toolchain` for each whose tools are all
    `bash`, which answers `--version` everywhere."""
    build = (
        f'platform(name="{HOST}", os="{HOST_OS}", arch="{HOST_ARCH}")\n'
        f'platform(name="{OTHER}", os="windows", arch="x86_64")\n'
    )
    if toolchains:
        for target in (HOST, OTHER):
            build += (
                f'cc_toolchain(name="to-{target}", host="{HOST}", target="{target}", '
                'cc="bash", cxx="bash", ar="bash")\n'
            )
    return build
