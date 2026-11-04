"""`cmake_project` as a target generator: a `cmake_target` handle (`<project>#<name>`) for every
library and executable CMake defines.

The names come from a placeholder-only configure per platform the project targets and this host
can build for, fed by the project's own `sources` and the paths its explicit `dependencies`
declare, so no codegen runs and nothing a build uses comes from it. Generation must not await
dependency inference or `AllTargets`, which would wait on generation itself.

A broken project must not break every goal in the repository, since any directory spec
generates targets: a setup mistake the build would raise, or a platform whose configure fails,
is logged and contributes no handles. Building a hand-written `cmake_target` of the project
raises the error.
"""

from __future__ import annotations

import logging

from pants.backend.cc.target_types import CCGeneratorSourcesField, CCSourceField
from pants.build_graph.address import (
    AddressParseException,
    InvalidParametersError,
    InvalidSpecPathError,
    InvalidTargetNameError,
    ResolveError,
    UnsupportedWildcardError,
)
from pants.core.target_types import (
    FilesGeneratingSourcesField,
    FileSourceField,
    TargetGeneratorSourcesHelperSourcesField,
    TargetGeneratorSourcesHelperTarget,
)
from pants.core.util_rules.external_tool import ExternalToolError, UnknownVersion
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.addresses import Address
from pants.engine.internals.graph import find_valid_field_sets, resolve_source_paths, resolve_target
from pants.engine.internals.native_engine import IntrinsicError
from pants.engine.intrinsics import execute_process
from pants.engine.process import ProcessCacheScope, ProcessExecutionFailure
from pants.engine.rules import collect_rules, concurrently, implicitly, rule
from pants.engine.target import (
    Dependencies,
    FieldSetsPerTargetRequest,
    GeneratedTargets,
    GenerateTargetsRequest,
    SourcesField,
    SourcesPathsRequest,
    Target,
    WrappedTargetRequest,
)
from pants.engine.unions import UnionMembership, UnionRule
from pants.util.logging import LogLevel

from pants_backend_cc.util_rules.fields import overlap_error, parse_address
from pants_backend_cc.util_rules.outputs import DeclaredOutputsFieldSet, declared_outputs
from pants_backend_cc.util_rules.toolchain import CcToolchainRequest, find_cc_toolchain
from pants_backend_cmake.ninja.graph import discover_targets
from pants_backend_cmake.ninja.parser import NinjaParseError
from pants_backend_cmake.target_types import (
    CMakeArgsField,
    CMakeBuildDirField,
    CMakeBuildTypeField,
    CMakeProjectDependenciesField,
    CMakeProjectInputsDependenciesField,
    CMakeProjectInputsTarget,
    CMakeProjectPlaceholdersField,
    CMakeProjectSourcesField,
    CMakeProjectTarget,
    CMakeTargetNameField,
    CMakeTargetProjectField,
    CMakeTargetTarget,
)
from pants_backend_cmake.util_rules.canonicalize import CanonicalizeError
from pants_backend_cmake.util_rules.configure import (
    build_dir_path,
    canonical_tree,
    configure_inputs,
    configure_process,
    split_placeholders,
)
from pants_backend_cmake.util_rules.toolchain import (
    CMakeToolchain,
    CMakeToolchainRequest,
    resolve_cmake_toolchain,
)
from pants_backend_platforms.target_types import PlatformsField
from pants_backend_platforms.util_rules.platforms import KnownPlatforms, TargetPlatform

logger = logging.getLogger(__name__)

INPUTS = "_inputs"

# Fields every handle needs as the project sets them.
_RESERVED = (Dependencies.alias, CMakeTargetProjectField.alias, CMakeTargetNameField.alias)

# What a malformed or missing address in `dependencies` raises.
_ADDRESS_ERRORS = (
    AddressParseException,
    InvalidParametersError,
    InvalidSpecPathError,
    InvalidTargetNameError,
    ResolveError,
    UnsupportedWildcardError,
)

# Sources whose paths a dependency contributes without being built.
_PATH_FIELDS = (FileSourceField, FilesGeneratingSourcesField, CCSourceField, CCGeneratorSourcesField)


async def _dependency_paths(address: Address, raw: tuple[str, ...]) -> tuple[str, ...]:
    """The paths the explicit dependencies will provide: a generator's declared outputs, a `files`
    or `cc_sources` target's sources, nothing for anything else."""
    # A `!` entry drops the same address, as `DependenciesRequest` does at build time; it does not
    # reach into a listed target generator there either.
    origin = f"the `dependencies` field of {address}"
    parsed = await concurrently(parse_address(v.lstrip("!"), address.spec_path, origin) for v in raw)
    excluded = {a for v, a in zip(raw, parsed) if v.startswith("!")}
    wrapped = await concurrently(
        resolve_target(WrappedTargetRequest(a, description_of_origin=origin), **implicitly())
        for v, a in zip(raw, parsed)
        if not v.startswith("!") and a not in excluded
    )
    targets = [w.target for w in wrapped]
    per_target = await find_valid_field_sets(
        FieldSetsPerTargetRequest(DeclaredOutputsFieldSet, targets), **implicitly()
    )
    declared = await concurrently(
        declared_outputs(**implicitly({field_sets[0]: DeclaredOutputsFieldSet}))
        for field_sets in per_target.collection
        if field_sets
    )
    source_fields = [
        t.get(SourcesField)
        for t, field_sets in zip(targets, per_target.collection)
        if not field_sets and isinstance(t.get(SourcesField), _PATH_FIELDS)
    ]
    sources = await concurrently(
        resolve_source_paths(SourcesPathsRequest(f), **implicitly()) for f in source_fields
    )
    return tuple(sorted({*(p for d in declared for p in d.paths), *(p for s in sources for p in s.files)}))


async def _cmake_toolchain(address: Address, platform: TargetPlatform) -> CMakeToolchain | None:
    """The platform's toolchain, or `None` (logged) when this host cannot configure for it."""
    found = await find_cc_toolchain(CcToolchainRequest(platform), **implicitly())
    skipped = f"{address}: not configured for {platform.name}, so it generates no targets for it"
    if found.error is not None:
        # No toolchain for the pair is how a host says it does not build a platform.
        (logger.debug if found.unmatched else logger.warning)(f"{skipped}: {found.error}")
        return None
    try:
        return await resolve_cmake_toolchain(CMakeToolchainRequest(platform), **implicitly())
    except (IntrinsicError, ExternalToolError, UnknownVersion, ProcessExecutionFailure) as e:
        # A failed CMake or Ninja download, a `[cmake]`/`[ninja]` version or platform it cannot
        # download, or the compilers' failed binutils fingerprint.
        logger.warning(f"{skipped}: {e}")
        return None


def _aliases(target_type: type[Target], union_membership: UnionMembership) -> set[str]:
    return {f.alias for f in target_type.class_field_types(union_membership)}


class GenerateCMakeTargetsRequest(GenerateTargetsRequest):
    generate_from = CMakeProjectTarget


@rule(desc="Generate CMake targets", level=LogLevel.DEBUG)
async def generate_cmake_targets(
    request: GenerateCMakeTargetsRequest, union_membership: UnionMembership, known: KnownPlatforms
) -> GeneratedTargets:
    generator = request.generator
    address = generator.address
    project_dir = address.spec_path

    overrides = request.require_unparametrized_overrides()
    reserved = sorted({key for fields in overrides.values() for key in fields} & set(_RESERVED))
    if reserved:
        raise ValueError(
            f"{address}: `overrides` cannot set {reserved}: the project sets them on every target "
            f"it generates, and they keep each one depending on `#{INPUTS}`"
        )

    sources = await determine_source_files(SourceFilesRequest([generator[CMakeProjectSourcesField]]))
    # Explicit dependencies only: inference would generate targets, this one among them.
    raw = tuple(generator[CMakeProjectDependenciesField].value or ())

    # One file target per project source, so `--changed-dependents` reaches the handles.
    prefix = f"{project_dir}/" if project_dir else ""
    helpers = [
        TargetGeneratorSourcesHelperTarget(
            {TargetGeneratorSourcesHelperSourcesField.alias: path[len(prefix) :]},
            request.template_address.create_file(path[len(prefix) :]),
            union_membership,
        )
        for path in sources.files
    ]
    # The project's own fields its inputs target also has (such as `clang_tidy_paths`) go with
    # it, since goals that expand generators see the inputs target, not the project.
    inputs_fields = _aliases(CMakeProjectInputsTarget, union_membership)
    shared = {
        f.alias: generator[f].value
        for f in CMakeProjectInputsTarget.class_field_types(union_membership)
        if generator.has_field(f) and not issubclass(f, Dependencies) and f.alias not in request.template
    }
    inputs = CMakeProjectInputsTarget(
        {
            **shared,
            **{k: v for k, v in request.template.items() if k in inputs_fields},
            CMakeTargetProjectField.alias: address.spec,
            CMakeProjectInputsDependenciesField.alias: [*raw, *(h.address.spec for h in helpers)],
        },
        request.template_address.create_generated(INPUTS),
        union_membership,
        residence_dir=project_dir,
    )
    degraded = GeneratedTargets(generator, [inputs, *helpers])

    # The build raises these setup mistakes; here they would stop every goal in the repository.
    try:
        extra_files = await _dependency_paths(address, raw)
        error = overlap_error(address, sources.files, extra_files)
        if error:
            raise ValueError(error)
        patterns = tuple(generator[CMakeProjectPlaceholdersField].value or ())
        real_files, placeholders = split_placeholders(address, sources.files, extra_files, patterns)
        build_dir = build_dir_path(address, generator[CMakeBuildDirField].value)
        # The platforms the project and its overrides name, the host when unset.
        targeted = set(known.listed(PlatformsField(request.template.get(PlatformsField.alias), address)))
        for fields in overrides.values():
            if PlatformsField.alias in fields:
                targeted.update(known.listed(PlatformsField(fields[PlatformsField.alias], address)))
    except (ValueError, *_ADDRESS_ERRORS) as e:
        logger.error(f"{address}: generates no targets: {e}")
        return degraded

    platforms = sorted(targeted)
    found = await concurrently(_cmake_toolchain(address, p) for p in platforms)
    buildable = [(p, t) for p, t in zip(platforms, found) if t is not None]

    configure_inputs_ = await concurrently(
        configure_inputs(sources.snapshot.digest, real_files, placeholders, build_dir, t)
        for _, t in buildable
    )
    processes = [
        configure_process(
            address,
            toolchain,
            project_dir,
            build_dir,
            probes_path,
            input_digest,
            request.template.get(CMakeBuildTypeField.alias),
            tuple(generator[CMakeArgsField].value or ()),
            description=f"Discover CMake targets of {address} for {platform.name}",
            # A failure may come from the host (what the probes find is not in the cache key), so
            # only success is cached.
            cache_scope=ProcessCacheScope.SUCCESSFUL,
        )
        for (platform, toolchain), (input_digest, _, probes_path) in zip(buildable, configure_inputs_)
    ]
    results = await concurrently(execute_process(p, **implicitly()) for p in processes)

    names: set[str] = set()
    configured = 0
    for (platform, toolchain), result in zip(buildable, results):
        failed = f"{address}: configure for {platform.name} failed, so it generates no targets for it"
        if result.exit_code != 0:
            logger.error(
                f"{failed} (exit {result.exit_code}):\n{result.stdout.decode(errors='replace')}"
                f"{result.stderr.decode(errors='replace')}"
            )
            continue
        try:
            canonical = await canonical_tree(address, build_dir, result.output_digest, toolchain)
        except (ValueError, CanonicalizeError, NinjaParseError) as e:
            logger.error(f"{failed}: {e}")
            continue
        names.update(name for name, info in discover_targets(canonical.graph).items() if info.addressable)
        configured += 1

    if not configured:
        return degraded
    if INPUTS in names:
        raise ValueError(f"{address}: a CMake target named `{INPUTS}` collides with the project's inputs")
    unknown = sorted(set(overrides) - names)
    if unknown:
        message = (
            f"{address}: `overrides` names targets the project does not define: {unknown}. "
            f"Defined: {sorted(names)}"
        )
        # A platform that did not configure may define them.
        if configured == len(platforms):
            raise ValueError(message)
        logger.warning(f"{message}, on the platforms that configured")

    # Plugin fields of the project (such as `clang_tidy_paths`) are not fields of the handles.
    handle_fields = _aliases(CMakeTargetTarget, union_membership)
    template = {k: v for k, v in request.template.items() if k in handle_fields}
    handles = [
        CMakeTargetTarget(
            {
                **template,
                CMakeTargetProjectField.alias: address.spec,
                CMakeTargetNameField.alias: name,
                Dependencies.alias: [inputs.address.spec],
                **overrides.get(name, {}),
            },
            request.template_address.create_generated(name),
            union_membership,
            residence_dir=project_dir,
        )
        for name in sorted(names)
    ]
    return GeneratedTargets(generator, [*handles, inputs, *helpers])


def rules():
    return [*collect_rules(), UnionRule(GenerateTargetsRequest, GenerateCMakeTargetsRequest)]
