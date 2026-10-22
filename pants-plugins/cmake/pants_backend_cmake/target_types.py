from __future__ import annotations

from pants.core.goals.test import (
    RuntimePackageDependenciesField,
    TestExtraEnvVarsField,
    TestTimeoutField,
)
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    BoolField,
    Dependencies,
    MultipleSourcesField,
    OverridesField,
    StringField,
    StringSequenceField,
    Target,
    TargetGenerator,
)
from pants.util.strutil import softwrap

TRANSLATION_UNIT_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".c++")


def is_translation_unit(path: str) -> bool:
    return path.endswith(TRANSLATION_UNIT_SUFFIXES)


class CMakeProjectSourcesField(MultipleSourcesField):
    alias = "sources"
    required = True
    help = softwrap(
        """
        Everything the build reads: CMake files, sources and headers. Configure sees the files
        matching `configure_placeholders` as zero-byte placeholders (CMake only checks that
        sources exist), so editing one never reruns configure; adding or removing one does.
        Every other file is given to configure as is.
        """
    )


class CMakeProjectPlaceholdersField(StringSequenceField):
    alias = "configure_placeholders"
    default = (
        "**/*.c",
        "**/*.cc",
        "**/*.cpp",
        "**/*.cxx",
        "**/*.c++",
        "**/*.m",
        "**/*.mm",
        "**/*.S",
        "**/*.s",
        "**/*.asm",
        "**/*.h",
        "**/*.hh",
        "**/*.hpp",
        "**/*.hxx",
        "**/*.inc",
        "**/*.inl",
        "**/*.ipp",
        "**/*.tcc",
    )
    help = softwrap(
        """
        Patterns (relative to this BUILD file, `*` also matches `/`) selecting the `sources`
        configure sees as zero-byte placeholders. Anything CMake reads at configure time
        (`CMakeLists.txt`, `*.cmake`, `configure_file` inputs, `file(READ)` inputs) must not
        match.
        """
    )


class CMakeProjectDependenciesField(Dependencies):
    help = softwrap(
        """
        `files` targets mounted next to the sources for the compile and link steps, for example
        a vendored header-only library CMake points an include directory at. They are not
        visible to configure.
        """
    )


class CMakeArgsField(StringSequenceField):
    alias = "cmake_args"
    default = ()
    help = "Extra arguments for the configure command, such as `-DFOO=ON`."


class CMakeBuildDirField(StringField):
    alias = "build_dir"
    default = "build"
    help = softwrap(
        """
        The build directory, relative to this BUILD file. Outputs are packaged under it, so a
        consumer that expects `build/libfoo.so` keeps working.
        """
    )


class CMakeBuildTypeField(StringField):
    alias = "build_type"
    help = softwrap(
        """
        `CMAKE_BUILD_TYPE`. Unset means CMake's default. Use `parametrize` to build several
        configurations; a `cmake_target` with the same `build_type` parametrization resolves
        to the matching project.
        """
    )


class CMakeCoverageField(BoolField):
    alias = "coverage"
    default = False
    help = softwrap(
        """
        The compile commands write gcov `.gcno` files next to their objects (pass the
        instrumentation flags through `cmake_args`); the replay captures them as outputs.
        """
    )


class CMakeProjectTarget(Target):
    alias = "cmake_project"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        CMakeProjectSourcesField,
        CMakeProjectPlaceholdersField,
        CMakeProjectDependenciesField,
        CMakeArgsField,
        CMakeBuildDirField,
        CMakeBuildTypeField,
        CMakeCoverageField,
    )
    help = softwrap(
        """
        A CMake project configured once with the Ninja generator. Its `cmake_target`s are built
        by replaying the generated Ninja edges as individual cached processes.
        """
    )


class CMakeTargetProjectField(StringField):
    alias = "project"
    required = True
    help = softwrap(
        """
        The address of the `cmake_project` this target belongs to. Inferred as a dependency, so
        a change to any project source marks this target as changed for
        `--changed-dependents`.
        """
    )


class CMakeTargetNameField(StringField):
    alias = "cmake_name"
    help = "The CMake target name. Defaults to this target's name."


class CMakeTargetDependenciesField(Dependencies):
    help = "The project is inferred from `project`, not listed here."


class CMakeTargetTarget(Target):
    alias = "cmake_target"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        CMakeTargetProjectField,
        CMakeTargetNameField,
        CMakeTargetDependenciesField,
        CMakeBuildTypeField,
    )
    help = softwrap(
        """
        One CMake library or executable target of a `cmake_project`, addressable for `package`.
        Object libraries are internal to the project and are not addressed.
        """
    )


# ---------------------------------------------------------------------------------------------
# Tests: compiled and linked by Pants against `cmake_target` libraries; CMake is not involved.
# ---------------------------------------------------------------------------------------------


class CMakeTestSourcesField(MultipleSourcesField):
    alias = "sources"
    required = True
    help = softwrap(
        f"""
        The test's translation units ({", ".join(f"`{s}`" for s in TRANSLATION_UNIT_SUFFIXES)})
        and the headers they include. Every translation unit is compiled and linked into one
        executable.
        """
    )


class CMakeTestDependenciesField(Dependencies):
    help = softwrap(
        """
        The `cmake_target` libraries the executable links against, in the order written here
        (the first one's flags come first; each library brings everything it links against
        itself, as CMake resolves it), plus `files` and `resources` targets. A file with a
        translation-unit suffix in those is compiled into the executable (a vendored test
        framework, for example); every other file is staged next to the build directory when
        the test runs.
        """
    )


class CMakeTestIncludeDirsField(StringSequenceField):
    alias = "include_dirs"
    default = ()
    help = "Include directories for the test's translation units, relative to this BUILD file."


class CMakeTestDefinesField(StringSequenceField):
    alias = "defines"
    default = ()
    help = "Preprocessor definitions for the test's translation units, as `NAME` or `NAME=VALUE`."


class CMakeTestCompilerArgsField(StringSequenceField):
    alias = "compiler_args"
    default = ()
    help = softwrap(
        """
        Extra arguments for every compile, after the flags CMake gives a consumer of the
        libraries in `dependencies`: their PUBLIC and INTERFACE defines, include directories
        and options, and the project's global flags for the translation unit's language. The
        libraries' PRIVATE settings are not included.
        """
    )


class CMakeTestLinkerArgsField(StringSequenceField):
    alias = "linker_args"
    default = ()
    help = softwrap(
        """
        Extra arguments for the link, after the objects and the link line CMake gives a
        consumer of the libraries (the libraries themselves, everything they link against,
        system libraries such as `-lm`). The link runs the C++ compiler when the test has C++
        translation units or CMake links a C consumer of one of the libraries with it, else
        the C compiler, with CMake's link flags for that language.
        """
    )


class CMakeTestArgsField(StringSequenceField):
    alias = "args"
    default = ()
    help = "Arguments the test executable is run with."


class CMakeTestTimeoutField(TestTimeoutField):
    pass


class CMakeTestExtraEnvVarsField(TestExtraEnvVarsField):
    pass


class CMakeTestRuntimePackageDependenciesField(RuntimePackageDependenciesField):
    pass


_TEST_SETTINGS_FIELDS = (
    CMakeTestDependenciesField,
    CMakeTestIncludeDirsField,
    CMakeTestDefinesField,
    CMakeTestCompilerArgsField,
    CMakeTestLinkerArgsField,
    CMakeTestArgsField,
    CMakeTestTimeoutField,
    CMakeTestExtraEnvVarsField,
    CMakeTestRuntimePackageDependenciesField,
)


class CMakeTestTarget(Target):
    alias = "cmake_test"
    core_fields = (*COMMON_TARGET_FIELDS, CMakeTestSourcesField, *_TEST_SETTINGS_FIELDS)
    help = softwrap(
        """
        A test executable Pants compiles from `sources` and links against the `cmake_target`
        libraries in `dependencies`, then runs with `args` from the libraries' build directory
        for `pants test`. CMake only builds the libraries.
        """
    )


class CMakeTestsSourcesField(MultipleSourcesField):
    alias = "sources"
    required = True
    help = softwrap(
        """
        Globs for the translation units and headers of every test, relative to this BUILD file.
        Each directory with translation units becomes one `cmake_test` named after the directory
        (`.` for this one), with that directory's translation units and all the headers.
        """
    )


class CMakeTestsOverridesField(OverridesField):
    help = softwrap(
        """
        Field values for individual generated tests, keyed by directory (the generated name):
        `overrides={"c_abi": {"compiler_args": ["-std=c11"]}}`. A value replaces the field
        for that test (an overridden `dependencies` must list the libraries again). A key
        naming no generated test is an error.
        """
    )


class CMakeTestsGeneratorTarget(TargetGenerator):
    alias = "cmake_tests"
    core_fields = (*COMMON_TARGET_FIELDS, CMakeTestsSourcesField, CMakeTestsOverridesField)
    generated_target_cls = CMakeTestTarget
    copied_fields = COMMON_TARGET_FIELDS
    moved_fields = _TEST_SETTINGS_FIELDS
    help = softwrap(
        """
        Generates one `cmake_test` per directory of translation units under `sources`. The
        other fields apply to every generated test; `overrides` adjusts single ones.
        """
    )


def targets():
    return [CMakeProjectTarget, CMakeTargetTarget, CMakeTestTarget, CMakeTestsGeneratorTarget]
