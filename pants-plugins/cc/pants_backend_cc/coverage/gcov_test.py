from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pants_backend_cc.coverage.gcov import gcov_wrapper, major_version, sibling_tool


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
