# pants-backend-generate

A [Pants](https://www.pantsbuild.org) backend for code generators whose outputs are committed
because something outside Pants reads them from git. Inside Pants a generator is ordinary
codegen, so consumers get fresh outputs in their sandboxes. `pants generate` writes the
committed copies and `pants lint` fails when one is stale. Both use the same cached codegen
rule, so the committed bytes are the bytes Pants consumers see.

**Status: alpha.** Works with Pants 2.31 and 2.33.

## `file_generate`

Generated files (`FileSourceField`), kept as written. Language backends ship their own
specializations with a formatter, for example `cpp_generate` in pants-backend-cmake. Every
`*_generate` target has these fields:

- `runnable` (required): the generator, a target `pants run` can run in a sandbox (such as a
  `pex_binary`), with `args`.
- `sources`: files the generator reads, relative to the BUILD file. Optional.
- `outputs` (required): every file the generator writes, as exact, normalised paths relative
  to the build root.
- `execution_dependencies`: targets whose files are present in the sandbox.
- `runnable_dependencies`: runnable targets put on `PATH`.
- `extra_env_vars`, `timeout`, `environment`.

The generator runs at the build root with its `sources` and `execution_dependencies` at their
repo-relative paths, and the parent directory of every output already exists. The outputs are
exact. Each of these is an error:

- a declared file that is not written
- an undeclared file written next to a declared one
- an input in an output directory that the generator overwrites
- an output that is also an input (exclude the committed copy with a `!` glob)

Put outputs in a directory: files at the build root are not checked for extras.

The host environment is not inherited, `PATH` included. A generator that runs host tools needs
`extra_env_vars=["PATH"]` or the tools as `runnable_dependencies`.

The committed outputs belong to no target:

- If another target's `sources` glob matches a committed output, a consumer of both targets
  gets two copies. Once the copy is stale, that fails with an opaque `MergeDigests` error.
  Exclude the output from that glob with `!`.
- File-address specs and `--changed-since` do not reach the generating target, so run the
  check as `pants lint ::`.

## `pants generate`

Runs the generators of the given `*_generate` targets and writes their outputs into the
workspace. It prints one `<address>: wrote <files>` line per target. Two targets writing the
same file is an error, and then nothing is written.

## `pants lint`

The `generate` linter reports each committed output that differs from the generated one
(`stale`) or does not exist (`missing`), followed by the `pants generate` command that
refreshes them. `--generate-skip` turns it off.

## Specializing

Codegen dispatches on the class of the target's sources field, so each output type needs its
own target type. `GenerateSpecialization` makes one. In the sketch below, `MySourceField` is the
sources field class your consumers ask for:

```python
from pants.engine.rules import collect_rules, rule
from pants.engine.unions import UnionRule

from pants_backend_generate.specialization import GenerateSpecialization
from pants_backend_generate.util_rules.formatting import FormatOutputsRequest, FormattedOutputs


class MyFormatOutputsRequest(FormatOutputsRequest):
    pass


@rule
async def format_my_outputs(request: MyFormatOutputsRequest) -> FormattedOutputs:
    ...  # format request.snapshot; return exactly the same files


my_generate = GenerateSpecialization(
    "my_generate",
    output=MySourceField,
    formatter=MyFormatOutputsRequest,  # or None
    help="Sources written by a generator.",
)


def target_types():
    return my_generate.target_types()


def rules():
    return [
        *collect_rules(),
        *my_generate.rules(),
        UnionRule(FormatOutputsRequest, MyFormatOutputsRequest),
    ]
```

A specialization's `rules()` carries everything codegen needs, so another backend can register
it without `pants_backend_generate` in `backend_packages`. Codegen then works, and a one-time
warning says that `pants generate` and the linter need the backend enabled.

A formatter runs inside the codegen rule. It receives `snapshot` and `address` with
`EnvironmentName` in scope, and it must return the same set of files. Aliases must be unique.
Several specializations may share an `output`.
