from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pants_backend_cc.coverage.gcov import (
    compile_directory,
    gcov_wrapper,
    major_version,
    patch_directory,
    recorded_directory,
    sibling_tool,
    smart_shim,
    smart_wrapper,
)


@pytest.mark.parametrize(
    "compiler, tool, path",
    [
        ("/usr/bin/gcc", "gcov", "/usr/bin/gcov"),
        ("/usr/bin/gcc-13", "gcov-tool", "/usr/bin/gcov-tool-13"),
        ("/opt/x/bin/x86_64-linux-gnu-gcc-14", "gcov", "/opt/x/bin/x86_64-linux-gnu-gcov-14"),
        ("/usr/bin/clang", "gcov", None),
    ],
)
def test_sibling_tool(compiler: str, tool: str, path: str | None) -> None:
    assert sibling_tool(compiler, tool) == path


@pytest.mark.parametrize(
    "output, major",
    [
        ("13\n", "13"),
        ("14.2.0\n", "14"),
        ("gcov (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0\nCopyright (C) 2023\n", "13"),
        ("gcov-tool (Homebrew GCC 14.2.0) 14.2.0\n", "14"),
        ("", None),
        ("no version here\n", None),
    ],
)
def test_major_version(output: str, major: str | None) -> None:
    assert major_version(output) == major


def test_wrapper_drops_the_json_format_options(tmp_path: Path) -> None:
    # A gcov in a directory with a space, which rejects the options of the JSON format.
    tools = tmp_path / "my tools"
    tools.mkdir()
    gcov, argv = tools / "gcov", tmp_path / "argv"
    gcov.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do case "$a" in --json-format|--conditions) exit 9 ;; esac; done\n'
        f'printf "%s\\n" "$@" > "{argv}"\n'
    )
    gcov.chmod(0o755)
    wrapper = tmp_path / "wrapper"
    wrapper.write_text(gcov_wrapper(shutil.which("bash") or "/bin/bash", str(gcov)))
    wrapper.chmod(0o755)
    args = ["--branch-counts", "--json-format", "a b.gcda", "--conditions", "--stdout"]
    subprocess.run([str(wrapper), *args], check=True, env={"PATH": os.defpath})
    assert argv.read_text().splitlines() == ["--branch-counts", "a b.gcda", "--stdout"]


def _note(directory: bytes) -> bytes:
    encoded = directory + b"\0"
    encoded += b"\0" * (-len(encoded) % 4)
    return (
        b"oncg"
        + b"B42*"
        + b"\x01\x02\x03\x04"
        + b"\0\0\0\0"
        + len(encoded).to_bytes(4, "little")
        + encoded
        + b"REST"
    )


def test_recorded_and_patched_directory() -> None:
    assert recorded_directory(_note(b"/tmp/pants-sandbox-x/build")) == "/tmp/pants-sandbox-x/build"
    assert patch_directory(_note(b"/tmp/pants-sandbox-x/build"), "/here/build") == _note(b"/here/build")
    # The padding changes with the length.
    assert patch_directory(_note(b"/a"), "/longer/dir") == _note(b"/longer/dir")
    with pytest.raises(ValueError):
        recorded_directory(b"ELF\0" + _note(b"/a")[4:])


@pytest.mark.parametrize(
    "recorded, note, directory",
    [
        ("/tmp/pants-sandbox-x/build", "build/CMakeFiles/a.dir/src/x.cpp.gcno", "build"),
        ("/tmp/pants-sandbox-x/build", "build/__pants_tests/t/obj/t.c.gcno", "build"),
        ("/tmp/pants-sandbox-x/out/build", "out/build/x.gcno", "out/build"),
        ("/tmp/pants-sandbox-x", "toybuild/x.c.gcno", ""),
        ("/tmp/pants-sandbox-x", "x.c.gcno", ""),
    ],
)
def test_compile_directory(recorded: str, note: str, directory: str) -> None:
    assert compile_directory(recorded, note) == directory


def _sandbox(tmp_path: Path, gcov: str) -> tuple[Path, Path, Path]:
    """A sandbox root with an object compiled in `build` from `../x.c`, and the shim over `gcov`."""
    root = tmp_path / "sandbox"
    (root / "__gcov").mkdir(parents=True)
    (root / "build" / "obj").mkdir(parents=True)
    note, data = root / "build" / "obj" / "a.c.gcno", root / "build" / "obj" / "a.c.gcda"
    note.write_bytes(_note(b"/tmp/pants-sandbox-x/build"))
    data.write_bytes(b"adcg")
    note.chmod(0o444)
    (root / "__gcov" / "gcov.py").write_text(smart_wrapper())
    shim = root / "__gcov" / "gcov"
    shim.write_text(smart_shim(shutil.which("bash") or "/bin/bash", sys.executable, gcov))
    shim.chmod(0o755)
    return root, shim, note


def test_smart_wrapper_runs_json_gcov_on_a_patched_copy(tmp_path: Path) -> None:
    gcov, argv = tmp_path / "gcov", tmp_path / "argv"
    gcov.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{argv}"\n')
    gcov.chmod(0o755)
    root, shim, note = _sandbox(tmp_path, str(gcov))
    data = root / "build" / "obj" / "a.c.gcda"
    subprocess.run(
        [str(shim), str(data), "--json-format", "--object-directory", "build/obj"],
        check=True,
        cwd=root,
        env={"PATH": os.defpath},
    )
    scratch = root / "__gcov" / "notes" / "build__obj__a.c"
    assert argv.read_text().splitlines() == [
        str(scratch / "a.c.gcda"),
        "--json-format",
        "--object-directory",
        str(scratch),
    ]
    assert (scratch / "a.c.gcno").read_bytes() == _note(str(root / "build").encode())
    assert (scratch / "a.c.gcda").read_bytes() == b"adcg"
    # The sandbox's own files are not touched: Pants may hardlink them from its store.
    assert note.read_bytes() == _note(b"/tmp/pants-sandbox-x/build")
    # gcovr probes gcov's options first.
    subprocess.run([str(shim), "--help"], check=True, cwd=root, env={"PATH": os.defpath})
    assert argv.read_text().splitlines() == ["--help"]


def test_smart_wrapper_runs_text_gcov_in_the_compile_directory(tmp_path: Path) -> None:
    # A gcov that writes its output file where it runs and says so, as gcov does.
    gcov, argv = tmp_path / "gcov", tmp_path / "argv"
    gcov.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$PWD" "$@" > "{argv}"\n'
        "echo \"File '../x.c'\"\n"
        "printf '%s\\n' '        -:    0:Source:../x.c' '        1:    1:int x;' > 'x.c##1234.gcov'\n"
        "echo \"Creating 'x.c##1234.gcov'\"\n"
        "exit 3\n"
    )
    gcov.chmod(0o755)
    root, shim, _ = _sandbox(tmp_path, str(gcov))
    data = root / "build" / "obj" / "a.c.gcda"
    run = subprocess.run(
        [str(shim), str(data), "--branch-counts", "--object-directory", "build/obj"],
        cwd=root,
        env={"PATH": os.defpath},
        capture_output=True,
        text=True,
    )
    assert run.returncode == 3
    assert run.stdout == "File '../x.c'\nCreating 'x.c##1234.gcov'\n"
    # gcov ran in `build`, with the object directory made absolute, and its output moved to the root.
    assert argv.read_text().splitlines() == [
        str(root / "build"),
        str(data),
        "--branch-counts",
        "--object-directory",
        str(root / "build" / "obj"),
    ]
    # Its `Source:` line now names the file from where gcovr ran gcov.
    assert (root / "x.c##1234.gcov").read_text() == "        -:    0:Source:x.c\n        1:    1:int x;\n"
    assert not (root / "build" / "x.c##1234.gcov").exists()
