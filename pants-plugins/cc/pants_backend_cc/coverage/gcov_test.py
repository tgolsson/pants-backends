from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pants_backend_cc.coverage.data import compile_directory_file
from pants_backend_cc.coverage.gcov import major_version, shim, sibling_tool, wrapper


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


def _sandbox(tmp_path: Path, gcov: str, directory: str | None = "build") -> tuple[Path, Path, Path]:
    """A sandbox root with an object at `build/obj/a.c.o` compiled in `directory` (no file naming
    it when `None`), and the shim over `gcov`."""
    root = tmp_path / "sandbox"
    (root / "__gcov").mkdir(parents=True)
    (root / "build" / "obj").mkdir(parents=True)
    note, data = root / "build" / "obj" / "a.c.gcno", root / "build" / "obj" / "a.c.gcda"
    note.write_bytes(b"oncg")
    data.write_bytes(b"adcg")
    if directory is not None:
        (root / compile_directory_file("build/obj/a.c.gcno")).write_text(directory)
    (root / "__gcov" / "gcov.py").write_text(wrapper())
    gcov_shim = root / "__gcov" / "gcov"
    gcov_shim.write_text(shim(shutil.which("bash") or "/bin/bash", sys.executable, gcov))
    gcov_shim.chmod(0o755)
    return root, gcov_shim, data


def _recording_gcov(tmp_path: Path, script: str = "") -> tuple[Path, Path]:
    """A gcov in a directory with a space that records its directory and arguments, then runs
    `script`."""
    tools = tmp_path / "my tools"
    tools.mkdir()
    gcov, argv = tools / "gcov", tmp_path / "argv"
    gcov.write_text(f'#!/bin/sh\nprintf "%s\\n" "$PWD" "$@" > "{argv}"\n{script}')
    gcov.chmod(0o755)
    return gcov, argv


def test_wrapper_runs_text_gcov_in_the_compile_directory(tmp_path: Path) -> None:
    # A gcov that writes its output file where it runs and says so, as gcov does.
    gcov, argv = _recording_gcov(
        tmp_path,
        "echo \"File '../x.c'\"\n"
        "printf '%s\\n' '        -:    0:Source:../x.c' '        1:    1:int x;' > 'x.c##1234.gcov'\n"
        "echo \"Creating 'x.c##1234.gcov'\"\n"
        "exit 3\n",
    )
    root, gcov_shim, data = _sandbox(tmp_path, str(gcov))
    run = subprocess.run(
        [str(gcov_shim), str(data), "--branch-counts", "--object-directory", "build/obj"],
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


def test_wrapper_drops_the_json_format_options_and_passes_probes_through(tmp_path: Path) -> None:
    gcov, argv = _recording_gcov(tmp_path)
    root, gcov_shim, data = _sandbox(tmp_path, str(gcov), directory="")
    subprocess.run(
        [str(gcov_shim), "--branch-counts", "--json-format", str(data), "--conditions", "--stdout"],
        check=True,
        cwd=root,
        env={"PATH": os.defpath},
    )
    assert argv.read_text().splitlines() == [str(root), "--branch-counts", str(data), "--stdout"]
    # gcovr probes gcov's options and version first.
    subprocess.run([str(gcov_shim), "--help"], check=True, cwd=root, env={"PATH": os.defpath})
    assert argv.read_text().splitlines() == [str(root), "--help"]


def test_wrapper_fails_without_the_compile_directory(tmp_path: Path) -> None:
    gcov, argv = _recording_gcov(tmp_path)
    root, gcov_shim, data = _sandbox(tmp_path, str(gcov), directory=None)
    run = subprocess.run(
        [str(gcov_shim), str(data)], cwd=root, env={"PATH": os.defpath}, capture_output=True, text=True
    )
    assert run.returncode == 1
    stem = str(data)[: -len(".gcda")]
    assert run.stderr == f"{stem}.gcno.cwd is missing: whoever compiled {stem}.o did not name its directory\n"
    assert not argv.exists()
