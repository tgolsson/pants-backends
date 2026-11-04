"""`package` of a binary, with the sandbox env and a staged `files` dependency, and for
Windows with MinGW-w64."""

from __future__ import annotations

import shutil

import pytest
from pants.build_graph.address import Address
from pants.core.goals.package import BuiltPackage
from pants.engine.fs import Digest, DigestContents
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner
from pants_backend_cargo.goals.package import CargoBinaryFieldSet
from pants_backend_cargo.target_types import CargoBinaryTarget, CargoLibraryTarget
from pants_backend_cargo.testing import (
    HOST_PLATFORM,
    RUST_VERSION,
    make_rule_runner,
    package_fixture,
)
from pants_backend_cargo.util_rules.sandbox import CargoSources, CargoSourcesRequest

from pants_backend_platforms.testutil import HOST, HOST_ARCH, HOST_OS, OTHER


@pytest.fixture
def rule_runner() -> RuleRunner:
    return make_rule_runner(
        [
            QueryRule(BuiltPackage, [CargoBinaryFieldSet]),
            QueryRule(DigestContents, [Digest]),
            QueryRule(CargoSources, [CargoSourcesRequest]),
        ]
    )


def test_package_binary(rule_runner: RuleRunner) -> None:
    files = package_fixture(
        "hello",
        main=(
            'fn main() { println!("{} {}", include_str!("../../data/greeting.txt").trim(), env!("WHO")); }'
        ),
    )
    files["hello/BUILD"] = 'cargo_package(dependencies=["data:data"], env=["WHO=world"])'
    files["data/BUILD"] = 'files(name="data", sources=["*.txt"])'
    files["data/greeting.txt"] = "hello\n"
    rule_runner.write_files(files)

    assert isinstance(rule_runner.get_target(Address("hello", generated_name="hello")), CargoBinaryTarget)
    assert isinstance(rule_runner.get_target(Address("hello", generated_name="library")), CargoLibraryTarget)

    tgt = rule_runner.get_target(Address("hello", generated_name="hello"))
    assert CargoBinaryFieldSet.is_applicable(tgt)

    # The `files` dependency is staged next to the package's own sources.
    sources = rule_runner.request(
        CargoSources,
        [
            CargoSourcesRequest(
                frozenset([tgt.address]),
                include_packages=True,
                platform=HOST_PLATFORM,
                dependent=tgt.address,
            )
        ],
    )
    assert "data/greeting.txt" in sources.files, sources.files
    assert not CargoBinaryFieldSet.is_applicable(
        rule_runner.get_target(Address("hello", generated_name="library"))
    )

    built = rule_runner.request(BuiltPackage, [CargoBinaryFieldSet.create(tgt)])
    assert [a.relpath for a in built.artifacts] == ["hello/hello"]
    contents = rule_runner.request(DigestContents, [built.digest])
    assert [fc.path for fc in contents] == ["hello/hello"]
    assert contents[0].is_executable


def test_output_path_is_the_directory(rule_runner: RuleRunner) -> None:
    files = package_fixture("hello", main="fn main() {}")
    files["hello/BUILD"] = 'cargo_package(output_path="tools/hello")'
    rule_runner.write_files(files)
    tgt = rule_runner.get_target(Address("hello", generated_name="hello"))
    built = rule_runner.request(BuiltPackage, [CargoBinaryFieldSet.create(tgt)])
    assert [a.relpath for a in built.artifacts] == ["tools/hello/hello"]


# Debian and Ubuntu install MinGW-w64's win32-thread drivers next to the posix ones; either
# links Rust's windows-gnu std.
MINGW = next(
    (name for name in ("x86_64-w64-mingw32-gcc-win32", "x86_64-w64-mingw32-gcc") if shutil.which(name)),
    None,
)


@pytest.mark.skipif(MINGW is None, reason="needs MinGW-w64 (x86_64-w64-mingw32-gcc) on PATH")
def test_package_for_windows(rule_runner: RuleRunner) -> None:
    assert MINGW is not None
    files = package_fixture("hello", main='fn main() { println!("hello"); }')
    files["hello/BUILD"] = f'cargo_package(platforms=["{HOST}", "{OTHER}"])'
    files["BUILD"] = (
        f'platform(name="{HOST}", os="{HOST_OS}", arch="{HOST_ARCH}")\n'
        f'platform(name="{OTHER}", os="windows", arch="x86_64")\n'
        f'cc_toolchain(name="host", host="{HOST}", target="{HOST}", cc="gcc", cxx="g++", '
        'ar="ar")\n'
        f'cc_toolchain(name="windows", host="{HOST}", target="{OTHER}", cc="{MINGW}", '
        f'cxx="{MINGW.replace("-gcc", "-g++")}", ar="x86_64-w64-mingw32-ar")\n'
    )
    rule_runner.write_files(files)
    rule_runner.set_options(
        [f"--rustup-rust-version={RUST_VERSION}", f"--platforms-enabled=['{OTHER}']"],
        env_inherit={"PATH"},
    )
    tgt = rule_runner.get_target(Address("hello", generated_name="hello"))
    built = rule_runner.request(BuiltPackage, [CargoBinaryFieldSet.create(tgt)])
    assert sorted(a.relpath for a in built.artifacts) == [
        "hello/hello",
        f"{OTHER}/hello/hello.exe",
    ]
    contents = rule_runner.request(DigestContents, [built.digest])
    exe = next(fc for fc in contents if fc.path == f"{OTHER}/hello/hello.exe")
    assert exe.content.startswith(b"MZ")
