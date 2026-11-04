"""`cargo_package` generates its targets from `cargo metadata`: the package itself, its
library, and one target per binary and integration test. A target with `test = false` gets
`skip_tests`."""

from __future__ import annotations

import json

from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.platform import Platform
from pants.engine.process import fallible_to_exec_result_or_raise
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import GeneratedTargets, GenerateTargetsRequest
from pants.engine.unions import UnionMembership, UnionRule
from pants_backend_cargo.target_types import (
    CargoArtifactKindField,
    CargoArtifactNameField,
    CargoBinaryTarget,
    CargoLibraryTarget,
    CargoPackageDependenciesField,
    CargoPackageNameField,
    CargoPackageSourcesField,
    CargoPackageTarget,
    CargoPackageTargetImpl,
    CargoTestTarget,
    SkipCargoTestsField,
    _CargoPackageMarker,
)
from pants_backend_cargo.util_rules.cargo import CargoProcessRequest, manifest_path
from pants_backend_cargo.util_rules.rustup import (
    RustVersion,
    install_rust_toolchain,
    machine_toolchain_request,
)

PACKAGE_GENERATED_NAME = "package"
LIBRARY_GENERATED_NAME = "library"

_LIBRARY_KINDS = frozenset({"lib", "rlib", "dylib", "cdylib", "staticlib", "proc-macro"})


def artifact_kind(cargo_kinds: list[str]) -> str | None:
    """cargo's `target.kind` list as this backend's `kind`, or None for what it does not
    model (examples, benches, custom builds)."""
    if "bin" in cargo_kinds:
        return "bin"
    if "test" in cargo_kinds:
        return "test"
    if _LIBRARY_KINDS.intersection(cargo_kinds):
        return "lib"
    return None


class GenerateCargoTargetsRequest(GenerateTargetsRequest):
    generate_from = CargoPackageTarget


@rule(desc="Generate the targets of a cargo package")
async def generate_cargo_targets(
    request: GenerateCargoTargetsRequest,
    version: RustVersion,
    platform: Platform,
    union_membership: UnionMembership,
) -> GeneratedTargets:
    generator = request.generator
    source_files = await determine_source_files(SourceFilesRequest([generator.get(CargoPackageSourcesField)]))
    toolchain = await install_rust_toolchain(machine_toolchain_request(version, platform))
    result = await fallible_to_exec_result_or_raise(
        **implicitly(
            CargoProcessRequest(
                toolchain,
                (
                    "metadata",
                    f"--manifest-path={manifest_path(generator.address.spec_path)}",
                    "--format-version=1",
                    "--no-deps",
                ),
                description=f"cargo metadata for {generator.address}",
                digest=source_files.snapshot.digest,
            )
        )
    )
    metadata = json.loads(result.stdout)
    # At a workspace root that is also a package, the members are listed too: the root's
    # manifest is the shortest path ending in it.
    own = [
        p
        for p in metadata["packages"]
        if p["manifest_path"].endswith(f"/{manifest_path(generator.address.spec_path)}")
    ]
    if not own:
        raise ValueError(
            f"{generator.address}: its Cargo.toml has no `[package]`; a workspace-only manifest "
            "is a `cargo_workspace`"
        )
    package = min(own, key=lambda p: len(p["manifest_path"]))

    package_address = generator.address.create_generated(PACKAGE_GENERATED_NAME)
    generated = [
        CargoPackageTargetImpl(
            {
                **request.template,
                CargoPackageSourcesField.alias: generator[CargoPackageSourcesField].value,
                CargoPackageNameField.alias: package["name"],
                # `dependencies` is a moved field: not on the generator, in the template.
                _CargoPackageMarker.alias: "yes",
            },
            package_address,
            union_membership=union_membership,
        )
    ]

    taken: dict[str, str] = {}
    for target in package["targets"]:
        kind = artifact_kind(target["kind"])
        if kind is None:
            continue
        target_type = {
            "lib": CargoLibraryTarget,
            "bin": CargoBinaryTarget,
            "test": CargoTestTarget,
        }[kind]
        generated_name = LIBRARY_GENERATED_NAME if kind == "lib" else target["name"]
        if kind != "lib" and generated_name in (PACKAGE_GENERATED_NAME, LIBRARY_GENERATED_NAME):
            raise ValueError(
                f"{generator.address}: the {kind} target `{generated_name}` would be generated "
                f"as `#{generated_name}`, which is reserved for the package's own "
                f"`{generated_name}` target; rename it in Cargo.toml"
            )
        if generated_name in taken:
            raise ValueError(
                f"{generator.address}: the {taken[generated_name]} target and the {kind} target "
                f"`{generated_name}` would both be generated as `#{generated_name}`; rename one "
                "in Cargo.toml"
            )
        taken[generated_name] = kind
        generated.append(
            target_type(
                {
                    **request.template,
                    CargoPackageDependenciesField.alias: [str(package_address)],
                    CargoPackageNameField.alias: package["name"],
                    CargoArtifactKindField.alias: kind,
                    CargoArtifactNameField.alias: target["name"],
                    # `cargo test` builds no test executable for it.
                    **({} if target.get("test", True) else {SkipCargoTestsField.alias: True}),
                },
                generator.address.create_generated(generated_name),
                union_membership=union_membership,
            )
        )

    return GeneratedTargets(generator, generated)


def rules():
    return [
        *collect_rules(),
        UnionRule(GenerateTargetsRequest, GenerateCargoTargetsRequest),
    ]
