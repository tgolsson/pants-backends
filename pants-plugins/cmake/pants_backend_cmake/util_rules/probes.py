"""The consumer probes: a CMake script configure injects through `CMAKE_PROJECT_INCLUDE`.

At the end of the top-level directory it adds, for every library target and every enabled
language, a hidden `EXCLUDE_FROM_ALL` executable with one empty source that links the
library. CMake then writes into `build.ninja` exactly what a consumer of that library is
compiled and linked with: its PUBLIC and INTERFACE defines, include directories and options,
transitively, with the directory's global flags for that language; and the full transitive
link line (`libfoo.a -lm`, the static libraries behind it, system libraries). PRIVATE
requirements of the library are not in it, because CMake never gives them to a consumer.
`link.py` reads these edges instead of guessing from the library's own compile lines. The
probes are never built.
"""

from __future__ import annotations

PROBE_PREFIX = "__pants_probe_"
PROBES_DIR = "__pants"
PROBES_SCRIPT_NAME = "probes.cmake"

_LANGUAGE_SUFFIX = {"c": "C", "cxx": "CXX"}


def probe_name(library: str, lang: str) -> str:
    return f"{PROBE_PREFIX}{library}_{_LANGUAGE_SUFFIX[lang]}"


PROBES_SCRIPT = f"""\
# Injected by pants_backend_cmake through CMAKE_PROJECT_INCLUDE: one hidden executable per
# library target and enabled language, linking the library, so build.ninja records what a
# consumer of that library is compiled and linked with. Never built.
get_property(_pants_probes_deferred GLOBAL PROPERTY PANTS_PROBES_DEFERRED)
if(_pants_probes_deferred)
    return()
endif()
set_property(GLOBAL PROPERTY PANTS_PROBES_DEFERRED TRUE)

function(_pants_collect_targets dir out)
    get_property(targets DIRECTORY "${{dir}}" PROPERTY BUILDSYSTEM_TARGETS)
    get_property(subdirs DIRECTORY "${{dir}}" PROPERTY SUBDIRECTORIES)
    foreach(sub IN LISTS subdirs)
        _pants_collect_targets("${{sub}}" sub_targets)
        list(APPEND targets ${{sub_targets}})
    endforeach()
    set(${{out}} "${{targets}}" PARENT_SCOPE)
endfunction()

function(_pants_add_probes)
    _pants_collect_targets("${{CMAKE_SOURCE_DIR}}" targets)
    get_property(languages GLOBAL PROPERTY ENABLED_LANGUAGES)
    set(dir "${{CMAKE_BINARY_DIR}}/{PROBES_DIR}")
    foreach(lang IN ITEMS C CXX)
        if(NOT lang IN_LIST languages)
            continue()
        endif()
        if(lang STREQUAL "C")
            set(source "${{dir}}/probe.c")
        else()
            set(source "${{dir}}/probe.cpp")
        endif()
        file(WRITE "${{source}}" "int main(void) {{ return 0; }}\\n")
        foreach(target IN LISTS targets)
            get_target_property(type "${{target}}" TYPE)
            if(NOT type MATCHES "^(STATIC_LIBRARY|SHARED_LIBRARY)$")
                continue()
            endif()
            set(probe "{PROBE_PREFIX}${{target}}_${{lang}}")
            add_executable("${{probe}}" EXCLUDE_FROM_ALL "${{source}}")
            target_link_libraries("${{probe}}" PRIVATE "${{target}}")
        endforeach()
    endforeach()
endfunction()

cmake_language(DEFER DIRECTORY "${{CMAKE_SOURCE_DIR}}" CALL _pants_add_probes)
"""
