"""What a cargo sandbox holds: packageable dependencies staged but not walked through, built
for the sandbox's platform when their backend builds for platforms, and the merged `env`."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pants.build_graph.address import Address
from pants.core.goals.package import BuiltPackage, BuiltPackageArtifact, PackageFieldSet
from pants.engine.fs import EMPTY_DIGEST, CreateDigest, Digest, DigestContents, FileContent
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.intrinsics import create_digest
from pants.engine.platform import Platform
from pants.engine.rules import QueryRule, rule
from pants.engine.target import COMMON_TARGET_FIELDS, Dependencies, Target
from pants.engine.unions import UnionRule
from pants.testutil.rule_runner import RuleRunner
from pants.util.frozendict import FrozenDict
from pants_backend_cargo.testing import HOST_PLATFORM, make_rule_runner, package_fixture
from pants_backend_cargo.util_rules.platform import platform_triple, toolchain_env_names
from pants_backend_cargo.util_rules.sandbox import CargoSources, CargoSourcesRequest

from pants_backend_cc.testutil import toolchains_build
from pants_backend_platforms import testutil
from pants_backend_platforms.target_types import PlatformsField
from pants_backend_platforms.testutil import HOST, OTHER, platforms_build
from pants_backend_platforms.util_rules.contract import PlatformBuild, PlatformBuildFieldSet
from pants_backend_platforms.util_rules.platforms import TargetPlatform

LIBRARY = "build/libfake.a"


class FakeLibDependencies(Dependencies):
    pass


class FakeLibTarget(Target):
    """Stands in for another backend's library: packageable, with inputs."""

    alias = "fake_lib"
    core_fields = (*COMMON_TARGET_FIELDS, FakeLibDependencies)


@dataclass(frozen=True)
class FakeLibFieldSet(PackageFieldSet):
    required_fields = (FakeLibDependencies,)

    dependencies: FakeLibDependencies


@rule
async def package_fake_lib(field_set: FakeLibFieldSet) -> BuiltPackage:
    digest = await create_digest(CreateDigest([FileContent(LIBRARY, b"fake")]))
    return BuiltPackage(digest, (BuiltPackageArtifact(LIBRARY),))


class LyingLibTarget(Target):
    """A platform build that declares a file it does not produce."""

    alias = "lying_lib"
    core_fields = (*COMMON_TARGET_FIELDS, PlatformsField)


@dataclass(frozen=True)
class LyingLibFieldSet(PlatformBuildFieldSet):
    required_fields = (PlatformsField,)

    @classmethod
    def opt_out(cls, tgt: Target) -> bool:
        return not isinstance(tgt, LyingLibTarget)


@rule
async def build_lying_lib(field_set: LyingLibFieldSet, platform: TargetPlatform) -> PlatformBuild:
    return PlatformBuild(EMPTY_DIGEST, ("lie/libmissing.a",))


@pytest.fixture
def rule_runner() -> RuleRunner:
    return make_rule_runner(
        [QueryRule(CargoSources, [CargoSourcesRequest]), QueryRule(DigestContents, [Digest])],
        rules=[
            package_fake_lib,
            UnionRule(PackageFieldSet, FakeLibFieldSet),
            testutil.toy_write,
            testutil.build_toy,
            testutil.package_toy,
            UnionRule(PackageFieldSet, testutil.ToyFieldSet),
            UnionRule(PlatformBuildFieldSet, testutil.ToyFieldSet),
            build_lying_lib,
            UnionRule(PlatformBuildFieldSet, LyingLibFieldSet),
        ],
        target_types=[FakeLibTarget, testutil.ToyArtifact, LyingLibTarget],
    )


def _sources(
    rule_runner: RuleRunner,
    include_packages: bool,
    address: Address = Address("hello", generated_name="package"),
    include_cargo_sources: bool = True,
) -> tuple[CargoSources, list[str]]:
    sources = rule_runner.request(
        CargoSources,
        [
            CargoSourcesRequest(
                frozenset([address]),
                include_packages,
                HOST_PLATFORM,
                address,
                include_cargo_sources,
            )
        ],
    )
    contents = rule_runner.request(DigestContents, [sources.digest])
    return sources, [fc.path for fc in contents]


def test_packageable_dependency_is_staged_not_walked(rule_runner: RuleRunner) -> None:
    files = package_fixture("hello")
    files["hello/BUILD"] = 'cargo_package(dependencies=["lib", "data"], env=["LIB_DIR={chroot}/build"])'
    files["lib/BUILD"] = 'fake_lib(dependencies=["third"])'
    files["third/BUILD"] = 'files(sources=["*.h"])'
    files["third/header.h"] = ""
    files["data/BUILD"] = 'files(sources=["*.txt"])'
    files["data/greeting.txt"] = "hello\n"
    rule_runner.write_files(files)

    sources, paths = _sources(rule_runner, include_packages=True)
    assert "data/greeting.txt" in paths
    assert LIBRARY in paths
    # The library's own inputs stay its backend's business.
    assert "third/header.h" not in paths
    assert sources.env == FrozenDict({"LIB_DIR": "{chroot}/build"})

    sources, paths = _sources(rule_runner, include_packages=False)
    assert "data/greeting.txt" in paths
    assert LIBRARY not in paths
    assert "third/header.h" not in paths
    assert sources.env == FrozenDict({"LIB_DIR": "{chroot}/build"})


def test_other_members_bring_only_their_cargo_sources(rule_runner: RuleRunner) -> None:
    files = {
        "ws/Cargo.toml": '[workspace]\nresolver = "2"\nmembers = ["a", "b", "c"]\n',
        "ws/BUILD": 'cargo_workspace(name="workspace")',
        "lib/BUILD": "fake_lib()",
    }
    for name in ("a", "b", "c"):
        files.update({f"ws/{k}": v for k, v in package_fixture(name).items()})
    files["ws/a/Cargo.toml"] += '\n[dependencies]\nb = { path = "../b" }\n'
    files["ws/c/BUILD"] = 'cargo_package(dependencies=["//lib"], env=["LIB_DIR={chroot}/build"])'
    rule_runner.write_files(files)

    a = Address("ws/a", generated_name="package")
    sources, paths = _sources(rule_runner, True, a)
    assert {"ws/Cargo.toml", "ws/b/src/lib.rs", "ws/c/Cargo.toml", "ws/c/src/lib.rs"} <= set(paths)
    assert LIBRARY not in paths
    assert sources.env == FrozenDict()
    assert _sources(rule_runner, True, a, include_cargo_sources=False)[1] == []

    for root in (Address("ws", target_name="workspace"), Address("ws/c", generated_name="package")):
        sources, paths = _sources(rule_runner, True, root)
        assert LIBRARY in paths
        assert sources.env == FrozenDict({"LIB_DIR": "{chroot}/build"})


def _contents(rule_runner: RuleRunner, platform: TargetPlatform) -> dict[str, bytes]:
    address = Address("hello", generated_name="package")
    sources = rule_runner.request(
        CargoSources, [CargoSourcesRequest(frozenset([address]), True, platform, address)]
    )
    return {fc.path: fc.content for fc in rule_runner.request(DigestContents, [sources.digest])}


def test_platform_dependency_is_built_for_the_platform(rule_runner: RuleRunner) -> None:
    files = package_fixture("hello")
    files["hello/BUILD"] = 'cargo_package(dependencies=["//lib", "//fake"])'
    files["BUILD"] = platforms_build() + toolchains_build()
    files["lib/BUILD"] = f'toy_artifact(text="lib", platforms=["{HOST}", "{OTHER}"])'
    files["fake/BUILD"] = "fake_lib()"
    rule_runner.write_files(files)

    contents = _contents(rule_runner, TargetPlatform(OTHER, "windows", "x86_64"))
    assert contents["lib/lib.exe.txt"].startswith(f"lib for {OTHER}\n".encode())
    assert "lib/lib.txt" not in contents
    # A backend without platforms is packaged for the host.
    assert LIBRARY in contents

    contents = _contents(rule_runner, HOST_PLATFORM)
    assert contents["lib/lib.txt"].startswith(f"lib for {HOST}\n".encode())
    assert "lib/lib.exe.txt" not in contents


def test_platform_dependency_must_list_the_platform(rule_runner: RuleRunner) -> None:
    files = package_fixture("hello")
    files["hello/BUILD"] = 'cargo_package(dependencies=["//lib"])'
    files["BUILD"] = platforms_build() + toolchains_build()
    files["lib/BUILD"] = f'toy_artifact(text="lib", platforms=["{HOST}"])'
    rule_runner.write_files(files)
    with pytest.raises(
        ExecutionError,
        match=rf"hello#package is being built for {OTHER}, but its dependency lib:lib lists only",
    ):
        _contents(rule_runner, TargetPlatform(OTHER, "windows", "x86_64"))


def test_missing_declared_file_names_its_build(rule_runner: RuleRunner) -> None:
    files = package_fixture("hello")
    files["hello/BUILD"] = 'cargo_package(dependencies=["//lie"])'
    files["lie/BUILD"] = "lying_lib()"
    rule_runner.write_files(files)
    missing = rf"the build of lie:lie for {HOST}: \"lie/libmissing\.a\""
    with pytest.raises(ExecutionError, match=missing):
        _contents(rule_runner, HOST_PLATFORM)


MACHINE = platform_triple(Platform.create_for_localhost())
WINDOWS = TargetPlatform(OTHER, "windows", "x86_64")


@pytest.mark.parametrize(
    "key",
    [
        "PATH",
        "RUSTFLAGS",
        "RUSTUP_TOOLCHAIN",
        "KACHE_BASE_DIR",
        "__PANTS_CC_TOOLCHAIN",
        *toolchain_env_names(MACHINE),
    ],
)
def test_reserved_env_is_rejected(rule_runner: RuleRunner, key: str) -> None:
    files = package_fixture("hello")
    files["hello/BUILD"] = f'cargo_package(env=["{key}=x"])'
    rule_runner.write_files(files)
    with pytest.raises(ExecutionError, match=rf"hello.* sets `{key}` in `env`, but"):
        _sources(rule_runner, include_packages=False)


@pytest.mark.parametrize("key", ["CC_x86_64-pc-windows-gnu", "CC_x86_64_pc_windows_gnu"])
def test_target_toolchain_env_is_reserved_when_building_for_it(rule_runner: RuleRunner, key: str) -> None:
    files = package_fixture("hello")
    files["hello/BUILD"] = f'cargo_package(env=["{key}=x"])'
    rule_runner.write_files(files)
    assert _contents(rule_runner, HOST_PLATFORM)
    with pytest.raises(ExecutionError, match=rf"hello.* sets `{key}` in `env`, but"):
        _contents(rule_runner, WINDOWS)


@pytest.mark.parametrize("key", ["CC", "CXX", "AR"])
def test_plain_compiler_env_is_rejected(rule_runner: RuleRunner, key: str) -> None:
    files = package_fixture("hello")
    files["hello/BUILD"] = f'cargo_package(env=["{key}=x"])'
    rule_runner.write_files(files)
    with pytest.raises(ExecutionError, match=rf"sets `{key}` in `env`, but .*`cc_toolchain`"):
        _sources(rule_runner, include_packages=False)


def test_unrelated_compiler_like_env_is_allowed(rule_runner: RuleRunner) -> None:
    files = package_fixture("hello")
    files["hello/BUILD"] = 'cargo_package(env=["CC_foo=x", "AR_FLAGS=y"])'
    rule_runner.write_files(files)
    sources, _ = _sources(rule_runner, include_packages=False)
    assert sources.env == FrozenDict({"CC_foo": "x", "AR_FLAGS": "y"})
