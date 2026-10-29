from __future__ import annotations

from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    BoolField,
    Dependencies,
    MultipleSourcesField,
    StringField,
    StringSequenceField,
    Target,
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
        drop it from one of them.
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
        PlatformsField,
    )
    help = softwrap(
        """
        One CMake library or executable target of a `cmake_project`, addressable for `package`.
        Object libraries are internal to the project and are not addressed.
        """
    )


def targets():
    return [CMakeProjectTarget, CMakeTargetTarget]
