"""

"""

from dataclasses import dataclass

from pants.core.goals.package import BuiltPackage, PackageFieldSet
from pants.engine.internals.selectors import Get
from pants.engine.rules import collect_rules, rule
from pants.engine.unions import UnionRule

from pants_backend_mdbook.targets import MdBookSources
from pants_backend_mdbook.util_rules.build import FallibleMdBookBuildOutput, MdbookBuildRequest


@dataclass(frozen=True)
class MdBookFieldSet(PackageFieldSet):
    required_fields = (MdBookSources,)

    sources: MdBookSources


@rule(desc="Package MDBOOK Image")
async def package_mdbook_image(field_set: MdBookFieldSet) -> BuiltPackage:
    build = await Get(FallibleMdBookBuildOutput, MdbookBuildRequest(field_set.address))
    if not build.success:
        raise Exception("Failed to build mdbook")

    assert build.output.digest is not None, "Expected a digest"
    return BuiltPackage(build.output.digest, tuple())


def rules():
    return [*collect_rules(), UnionRule(PackageFieldSet, MdBookFieldSet)]
