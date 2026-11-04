from __future__ import annotations

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

from pants_backend_platforms.target_types import PlatformsField


class CMakeProjectSourcesField(MultipleSourcesField):
    alias = "sources"
    required = True
    help = softwrap(
        """
        What CMake reads: the CMake files and configure-time inputs, plus any source or header
        not provided by `dependencies` (preferably `cc_sources` targets own those). Configure
        sees the files matching `configure_placeholders` as zero-byte placeholders (CMake only
        checks that sources exist), so editing one never reruns configure; adding or removing one
        does. Every other file is given to configure as is.
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
        Patterns (relative to this BUILD file, `*` also matches `/`) selecting the files of
        `sources` and `dependencies` configure sees as zero-byte placeholders. Anything CMake
        reads at configure time (`CMakeLists.txt`, `*.cmake`, `configure_file` inputs,
        `file(READ)` inputs) must not match: it would read as empty.
        """
    )


class CMakeProjectDependenciesField(Dependencies):
    help = softwrap(
        """
        `files`, `cc_sources` and codegen targets producing either, mounted next to the sources
        for the compile and link steps. Configure sees only their files matching
        `configure_placeholders`, as placeholders. A path `sources` also provides is an error:
        drop it from one of them. Generating the project's targets reads these entries as
        written, without inferred dependencies, less those a `!` entry names: a `!` entry removes
        only an entry naming the same address.
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
        `CMAKE_BUILD_TYPE` of the target's configure; unset means CMake's default. On a
        `cmake_project` it applies to the targets it generates, and to a hand-written
        `cmake_target` naming the project that sets none; `parametrize` it there to build several
        configurations, and name one as `project=":proj@build_type=Release"`.
        """
    )


class CMakeCoverageArgsField(StringSequenceField):
    alias = "coverage_cmake_args"
    default = ()
    help = softwrap(
        """
        Arguments added to `cmake_args` for the build a coverage consumer links, such as a
        `cc_test` under `pants test --use-coverage`: they should make the compiles write gcov
        notes and the links bring the gcov runtime (`--coverage`). That build is a configure of
        its own, whose replay captures each compile's `.gcno`. Unset, coverage consumers get the
        usual build and the project's code is not in the report.
        """
    )


class CMakeTargetProjectField(StringField):
    alias = "project"
    required = True
    help = softwrap(
        """
        The address of the `cmake_project` this target belongs to. Inferred as a dependency (a
        target the project generates depends on its `#_inputs` instead), so a change to any
        project source marks this target as changed for `--changed-dependents`.
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
        PlatformsField,
    )
    help = softwrap(
        """
        One CMake library or executable target of a `cmake_project`, addressable for `package`.
        Object libraries are internal to the project and are not addressed.
        """
    )


class CMakeProjectInputsDependenciesField(Dependencies):
    help = "The project's files and its `dependencies`."


class CMakeProjectInputsTarget(Target):
    alias = "cmake_project_inputs"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        CMakeTargetProjectField,
        CMakeProjectInputsDependenciesField,
        CMakeBuildTypeField,
    )
    help = softwrap(
        """
        A `cmake_project` generates one as `<project>#_inputs`; it is not written by hand. It owns
        the project's files and dependencies, and every target the project generates depends on
        it. It stands in for the project where goals replace a generator with what it generates,
        so project-wide tools such as clang-tidy run on it, configured with its `build_type`.
        """
    )


class CMakeProjectOverridesField(OverridesField):
    help = softwrap(
        """
        Field values for single generated targets, keyed by CMake target name:
        `overrides={"app": {"platforms": ["linux-x86_64", "windows-x86_64"]}}`. A name the
        configure did not find is an error, or a warning when a platform the project targets was
        not configured. `dependencies`, `project` and `cmake_name` cannot be overridden.
        """
    )


class CMakeProjectTarget(TargetGenerator):
    alias = "cmake_project"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        CMakeProjectSourcesField,
        CMakeProjectPlaceholdersField,
        CMakeProjectDependenciesField,
        CMakeArgsField,
        CMakeBuildDirField,
        CMakeBuildTypeField,
        CMakeCoverageArgsField,
        PlatformsField,
        CMakeProjectOverridesField,
    )
    generated_target_cls = CMakeTargetTarget
    copied_fields = COMMON_TARGET_FIELDS
    moved_fields = (PlatformsField, CMakeBuildTypeField)
    help = softwrap(
        """
        A CMake project configured with the Ninja generator. It generates a `cmake_target`
        (`<project>#<name>`) for every library and executable a configure finds on the platforms
        it targets, built by replaying the generated Ninja edges as individual cached processes.
        `platforms` and `build_type` apply to the generated targets. A setup mistake, or a
        platform whose configure fails, is logged and contributes no targets; `pants package` of
        a hand-written `cmake_target` naming the project raises the error.
        """
    )


def targets():
    return [CMakeProjectTarget, CMakeTargetTarget, CMakeProjectInputsTarget]
