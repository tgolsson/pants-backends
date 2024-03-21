from __future__ import annotations

from dataclasses import dataclass

from pants.backend.shell.target_types import ShellCommandCommandField, ShellCommandRunTarget
from pants.engine.rules import collect_rules, rule
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    GeneratedTargets,
    GenerateTargetsRequest,
    Target,
    TargetGenerator,
)
from pants.engine.unions import UnionMembership, UnionRule
from pants.util.strutil import softwrap

from pants_plugin_deb.targets import (
    DebianArchitectureField,
    DebianPackageField,
    DebianPackagesField,
    DebianPoolsField,
    DebianReleaseField,
    DebianResolveField,
    DebianSnapshotField,
    DebPackage,
)


class DebianPackagesTargetGenerator(TargetGenerator):
    alias = "deb_packages"
    help = softwrap(
        """
        Generate `deb_package` targets that can be packaged for use in containers.
        """
    )
    generated_target_cls = DebPackage
    core_fields = (
        *COMMON_TARGET_FIELDS,
        DebianArchitectureField,
        DebianPackagesField,
        DebianPoolsField,
        DebianReleaseField,
        DebianResolveField,
        DebianSnapshotField,
    )
    copied_fields = (
        *COMMON_TARGET_FIELDS,
        DebianResolveField,
    )
    moved_fields = ()


class GenerateFromDebianPackagesRequest(GenerateTargetsRequest):
    generate_from = DebianPackagesTargetGenerator


@rule
def generate_from_debian_packages(
    request: GenerateFromDebianPackagesRequest, union_membership: UnionMembership
) -> GeneratedTargets:
    generator = request.generator

    print("xx")

    def create_tgt(package: str) -> DebPackage:
        print(package)
        return DebPackage(
            {
                DebianPackageField.alias: package,
                **request.template,
            },
            request.template_address.create_generated(package),
            union_membership,
        )

    result = [create_tgt(p) for p in generator[DebianPackagesField].value]
    return GeneratedTargets(generator, result)


def targets():
    return [
        DebianPackagesTargetGenerator,
    ]


def rules():
    return [
        *collect_rules(),
        UnionRule(GenerateTargetsRequest, GenerateFromDebianPackagesRequest),
    ]
