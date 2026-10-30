from __future__ import annotations

from pants.core.goals.package import OutputPathField
from pants.core.goals.test import (
    RuntimePackageDependenciesField,
    TestExtraEnvVarsField,
    TestTimeoutField,
)
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    Dependencies,
    MultipleSourcesField,
    OverridesField,
    StringField,
    StringSequenceField,
    Target,
    TargetGenerator,
)
from pants.util.strutil import softwrap


class CcToolchainHostField(StringField):
    alias = "host"
    required = True
    help = "The platform this toolchain runs on, by name."


class CcToolchainTargetField(StringField):
    alias = "target"
    required = True
    help = "The platform this toolchain builds for, by name."


class CcToolchainCcField(StringField):
    alias = "cc"
    required = True
    value: str
    help = "The C compiler: a name looked up on the system search path, or an absolute path."


class CcToolchainCxxField(StringField):
    alias = "cxx"
    required = True
    value: str
    help = "The C++ compiler: a name looked up on the system search path, or an absolute path."


class CcToolchainArField(StringField):
    alias = "ar"
    required = True
    value: str
    help = "The archiver: a name looked up on the system search path, or an absolute path."


class CcToolchainTarget(Target):
    alias = "cc_toolchain"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        CcToolchainHostField,
        CcToolchainTargetField,
        CcToolchainCcField,
        CcToolchainCxxField,
        CcToolchainArField,
    )
    help = "The C and C++ tools that build for `target` on `host`, native pairs included."


TRANSLATION_UNIT_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".c++")


def is_translation_unit(path: str) -> bool:
    return path.endswith(TRANSLATION_UNIT_SUFFIXES)


class CcTestSourcesField(MultipleSourcesField):
    alias = "sources"
    help = softwrap(
        f"""
        The test's translation units ({", ".join(f"`{s}`" for s in TRANSLATION_UNIT_SUFFIXES)})
        and the headers they include; optional when `dependencies` provide them. Every
        translation unit is compiled and linked into one executable.
        """
    )


class CcTestDependenciesField(Dependencies):
    help = softwrap(
        """
        The libraries the executable links against, in the order written here (their flags and
        link arguments follow that order; each library brings everything it links against
        itself), plus
        `files`, `resources` and `cc_sources` targets, or targets that generate them
        (`relocated_files`, `cpp_generate`). A file with a translation-unit suffix in those is
        compiled into the executable (a vendored test framework, a generated runner); every
        other file is staged for the run at its own path. Not a library's own `cc_sources`:
        those would be compiled into the test.
        """
    )


class CcTestIncludeDirsField(StringSequenceField):
    alias = "include_dirs"
    default = ()
    help = softwrap(
        """
        Include directories for the executable's translation units, relative to this BUILD file.
        They come before the source roots of its own and its dependencies' files, which are added
        too.
        """
    )


class CcTestDefinesField(StringSequenceField):
    alias = "defines"
    default = ()
    help = "Preprocessor definitions for the executable's translation units, as `NAME` or `NAME=VALUE`."


class CcTestCompilerArgsField(StringSequenceField):
    alias = "compiler_args"
    default = ()
    help = softwrap(
        """
        Extra arguments for every compile, after the flags the libraries in `dependencies` give
        a consumer in the translation unit's language.
        """
    )


class CcTestLinkerArgsField(StringSequenceField):
    alias = "linker_args"
    default = ()
    help = softwrap(
        """
        Extra arguments for the link, after the objects and the libraries' link arguments (the
        libraries themselves, everything they link against, system libraries such as `-lm`).
        The link runs the C++ compiler when the executable has C++ translation units or a
        library links C consumers with it, else the C compiler.
        """
    )


class CcTestArgsField(StringSequenceField):
    alias = "args"
    default = ()
    help = "Arguments the test executable is run with."


class CcTestTimeoutField(TestTimeoutField):
    pass


class CcTestExtraEnvVarsField(TestExtraEnvVarsField):
    pass


class CcTestRuntimePackageDependenciesField(RuntimePackageDependenciesField):
    pass


_TEST_SETTINGS_FIELDS = (
    CcTestDependenciesField,
    CcTestIncludeDirsField,
    CcTestDefinesField,
    CcTestCompilerArgsField,
    CcTestLinkerArgsField,
    CcTestArgsField,
    CcTestTimeoutField,
    CcTestExtraEnvVarsField,
    CcTestRuntimePackageDependenciesField,
)


class CcTestTarget(Target):
    alias = "cc_test"
    core_fields = (*COMMON_TARGET_FIELDS, CcTestSourcesField, *_TEST_SETTINGS_FIELDS)
    help = softwrap(
        """
        A test executable Pants compiles from `sources` and links against the libraries in
        `dependencies`, then runs with `args` for `pants test`.
        """
    )


class CcTestsSourcesField(MultipleSourcesField):
    alias = "sources"
    required = True
    help = softwrap(
        """
        Globs for the translation units and headers of every test, relative to this BUILD file.
        Each directory with translation units becomes one `cc_test` named after the directory
        (`.` for this one), with that directory's translation units and all the headers.
        """
    )


class CcTestsOverridesField(OverridesField):
    help = softwrap(
        """
        Field values for individual generated tests, keyed by directory (the generated name):
        `overrides={"smoke": {"compiler_args": ["-std=c11"]}}`. A value replaces the field
        for that test (an overridden `dependencies` must list the libraries again). A key
        naming no generated test is an error.
        """
    )


class CcTestsGeneratorTarget(TargetGenerator):
    alias = "cc_tests"
    core_fields = (*COMMON_TARGET_FIELDS, CcTestsSourcesField, CcTestsOverridesField)
    generated_target_cls = CcTestTarget
    copied_fields = COMMON_TARGET_FIELDS
    moved_fields = _TEST_SETTINGS_FIELDS
    help = softwrap(
        """
        Generates one `cc_test` per directory of translation units under `sources`. The other
        fields apply to every generated test; `overrides` adjusts single ones.
        """
    )


class CcBinarySourcesField(CcTestSourcesField):
    help = softwrap(
        f"""
        The executable's translation units ({", ".join(f"`{s}`" for s in TRANSLATION_UNIT_SUFFIXES)})
        and the headers they include; optional when `dependencies` provide them. Every
        translation unit is compiled and linked into one executable.
        """
    )


class CcBinaryDependenciesField(CcTestDependenciesField):
    help = softwrap(
        """
        The libraries the executable links against, in the order written here (their flags and
        link arguments follow that order; each library brings everything it links against
        itself), plus `cc_sources` targets, or targets that generate them (`cpp_generate`),
        whose translation units are compiled into the executable. Not a library's own
        `cc_sources`: those would be compiled in twice.
        """
    )


class CcBinaryTarget(Target):
    alias = "cc_binary"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        CcBinarySourcesField,
        CcBinaryDependenciesField,
        CcTestIncludeDirsField,
        CcTestDefinesField,
        CcTestCompilerArgsField,
        CcTestLinkerArgsField,
        OutputPathField,
    )
    help = softwrap(
        """
        An executable Pants compiles from `sources` and links against the libraries in
        `dependencies`, for the host; `pants package` writes it to `output_path`.
        """
    )
