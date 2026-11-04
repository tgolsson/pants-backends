"""The `cargo build` command and output for the host and for another platform."""

from __future__ import annotations

from pants.build_graph.address import Address
from pants_backend_cargo.internal.build import CargoBinaryRequest, cargo_build_invocation
from pants_backend_cargo.util_rules.platform import RustTarget

from pants_backend_platforms.util_rules.platforms import TargetPlatform

ADDRESS = Address("tools/hello", generated_name="hello")
LINUX = RustTarget(TargetPlatform("linux-x86_64", "linux", "x86_64"), "x86_64-unknown-linux-gnu")
WINDOWS = RustTarget(TargetPlatform("windows-x86_64", "windows", "x86_64"), "x86_64-pc-windows-gnu")


def test_host_build_is_a_plain_cargo_build() -> None:
    request = CargoBinaryRequest(ADDRESS, "hello", False, LINUX.platform)
    invocation = cargo_build_invocation(request, LINUX, "x86_64-unknown-linux-gnu")
    assert invocation.command == (
        "build",
        "--locked",
        "--manifest-path=tools/hello/Cargo.toml",
        "--bin=hello",
    )
    assert (invocation.output_dir, invocation.output_name) == ("target/debug", "hello")


def test_other_platform_builds_with_target() -> None:
    request = CargoBinaryRequest(ADDRESS, "hello", True, WINDOWS.platform)
    invocation = cargo_build_invocation(request, WINDOWS, "x86_64-unknown-linux-gnu")
    assert invocation.command == (
        "build",
        "--locked",
        "--manifest-path=tools/hello/Cargo.toml",
        "--bin=hello",
        "--release",
        "--target=x86_64-pc-windows-gnu",
    )
    assert invocation.output_dir == "target/x86_64-pc-windows-gnu/release"
    assert invocation.output_name == "hello.exe"
