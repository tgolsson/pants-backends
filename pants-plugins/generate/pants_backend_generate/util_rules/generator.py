"""The codegen rule body. Output directories are captured whole so that undeclared and overwritten
files are seen; outputs at the build root therefore get neither check."""

from __future__ import annotations

import os

from pants.core.environments.rules import (
    EnvironmentNameRequest,
    get_target_for_environment_name,
    resolve_environment_name,
)
from pants.core.util_rules import adhoc_process_support
from pants.core.util_rules.adhoc_process_support import (
    AdhocProcessRequest,
    ToolRunnerRequest,
    convert_fallible_adhoc_process_result,
    create_tool_runner,
    prepare_env_vars,
)
from pants.engine.environment import EnvironmentName
from pants.engine.fs import (
    CreateDigest,
    DigestSubset,
    Directory,
    FileEntry,
    MergeDigests,
    PathGlobs,
)
from pants.engine.intrinsics import (
    create_digest,
    digest_subset_to_digest,
    digest_to_snapshot,
    get_digest_entries,
    merge_digests,
)
from pants.engine.rules import concurrently, implicitly
from pants.engine.target import GeneratedSources, GenerateSourcesRequest
from pants.util.frozendict import FrozenDict
from pants.util.strutil import bullet_list

from pants_backend_generate.target_types import (
    GenerateArgsField,
    GenerateExecutionDependenciesField,
    GenerateExtraEnvVarsField,
    GenerateOutputsField,
    GenerateRunnableDependenciesField,
    GenerateRunnableField,
    GenerateTimeoutField,
)
from pants_backend_generate.util_rules.formatting import FormatOutputsRequest, format_outputs


class GenerateOutputsError(Exception):
    pass


async def run_generator(request: GenerateSourcesRequest) -> GeneratedSources:
    target = request.protocol_target
    address = target.address
    outputs = target[GenerateOutputsField].value
    description = f"the `{target.alias}` at {address}"

    environment_name = await resolve_environment_name(
        EnvironmentNameRequest.from_target(target), **implicitly()
    )
    environment_target = await get_target_for_environment_name(environment_name, **implicitly())
    tool_runner = await create_tool_runner(
        ToolRunnerRequest(
            runnable_address_str=target[GenerateRunnableField].value,
            args=target[GenerateArgsField].value or (),
            execution_dependencies=target[GenerateExecutionDependenciesField].value or (),
            runnable_dependencies=target[GenerateRunnableDependenciesField].value or (),
            target=target,
            runnable_address_field_alias=GenerateRunnableField.alias,
        )
    )

    output_directories = tuple(sorted({os.path.dirname(o) for o in outputs} - {""}))
    directories = await create_digest(CreateDigest(Directory(d) for d in output_directories))
    input_digest = await merge_digests(
        MergeDigests([tool_runner.digest, request.protocol_sources.digest, directories])
    )
    input_snapshot = await digest_to_snapshot(input_digest)
    overlap = sorted(set(outputs) & set(input_snapshot.files))
    if overlap:
        raise GenerateOutputsError(
            f"{description} declares outputs that are also inputs (in `sources`, or from "
            f"`execution_dependencies` or the runnable):\n\n{bullet_list(overlap)}\n\n"
            "Exclude the committed copy from those inputs, for example with a `!` glob."
        )

    env_vars = await prepare_env_vars(
        tool_runner.extra_env,
        target[GenerateExtraEnvVarsField].value or (),
        extra_paths=tool_runner.extra_paths,
        description_of_origin=f"`{GenerateExtraEnvVarsField.alias}` of {description}",
    )
    process_request = AdhocProcessRequest(
        description=description,
        address=address,
        working_directory="",
        root_output_directory="",
        argv=tool_runner.args,
        timeout=target[GenerateTimeoutField].value,
        input_digest=input_digest,
        immutable_input_digests=FrozenDict.frozen(tool_runner.immutable_input_digests),
        append_only_caches=FrozenDict(tool_runner.append_only_caches),
        output_files=outputs,
        output_directories=output_directories,
        env_vars=env_vars,
        log_on_process_errors=None,
        log_output=False,
        capture_stdout_file=None,
        capture_stderr_file=None,
        workspace_invalidation_globs=None,
        cache_scope=environment_target.default_cache_scope,
        # The exact-set check below replaces adhoc_tool's glob check.
        use_working_directory_as_base_for_output_captures=False,
        outputs_match_conjunction=None,
    )
    result = await convert_fallible_adhoc_process_result(
        **implicitly({environment_name: EnvironmentName, process_request: AdhocProcessRequest})
    )

    input_entries, produced_entries = await concurrently(
        get_digest_entries(input_digest), get_digest_entries(result.adjusted_digest)
    )
    inputs = {e.path: e.file_digest for e in input_entries if isinstance(e, FileEntry)}
    produced = {e.path: e.file_digest for e in produced_entries if isinstance(e, FileEntry)}
    missing = sorted(set(outputs) - set(produced))
    # Unchanged inputs inside a captured output directory are not extras.
    extra = sorted(set(produced) - set(outputs) - set(inputs))
    overwritten = sorted(
        path for path, digest in produced.items() if path in inputs and inputs[path] != digest
    )
    if missing or extra or overwritten:
        problems = []
        if missing:
            problems.append(f"Declared in `outputs` but not written:\n\n{bullet_list(missing)}")
        if extra:
            problems.append(f"Written but not declared in `outputs`:\n\n{bullet_list(extra)}")
        if overwritten:
            problems.append(
                "Written but not declared in `outputs` (these were inputs, which the generator "
                f"overwrote):\n\n{bullet_list(overwritten)}"
            )
        raise GenerateOutputsError(
            f"{description} did not write exactly its `outputs`.\n\n" + "\n\n".join(problems)
        )

    exact_digest = await digest_subset_to_digest(DigestSubset(result.adjusted_digest, PathGlobs(outputs)))
    exact = await digest_to_snapshot(exact_digest)

    formatter_type = request.input.formatter_type
    if formatter_type is None:
        return GeneratedSources(exact)

    formatted = await format_outputs(
        **implicitly(
            {
                formatter_type(snapshot=exact, address=address): FormatOutputsRequest,
                environment_name: EnvironmentName,
            }
        )
    )
    if set(formatted.snapshot.files) != set(exact.files):
        raise GenerateOutputsError(
            f"The formatter {formatter_type.__name__} changed the files of {description}: "
            f"it was given {sorted(exact.files)} and returned {sorted(formatted.snapshot.files)}."
        )
    return GeneratedSources(formatted.snapshot)


def rules():
    return [*adhoc_process_support.rules()]
