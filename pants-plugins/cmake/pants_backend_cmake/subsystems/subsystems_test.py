from __future__ import annotations

import pytest
from pants.core.util_rules import external_tool
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.fs import Digest, DigestEntries
from pants.engine.platform import Platform
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner

from pants_backend_cmake.subsystems import cmake, ninja
from pants_backend_cmake.subsystems.cmake import CMakeTool
from pants_backend_cmake.subsystems.ninja import NinjaTool


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *external_tool.rules(),
            *cmake.rules(),
            *ninja.rules(),
            QueryRule(DownloadedExternalTool, [ExternalToolRequest]),
            QueryRule(DigestEntries, [Digest]),
            QueryRule(CMakeTool, []),
            QueryRule(NinjaTool, []),
        ]
    )


@pytest.mark.parametrize(
    "tool_type,version,platform",
    [
        (tool_type, row.split("|")[0], Platform(row.split("|")[1].strip()))
        for tool_type in (CMakeTool, NinjaTool)
        for row in tool_type.default_known_versions
    ],
)
def test_platform_download(rule_runner, tool_type, version, platform):
    rule_runner.set_options([f"--{tool_type.options_scope}-version={version}"])
    tool = rule_runner.request(tool_type, [])
    downloaded = rule_runner.request(DownloadedExternalTool, [tool.get_request(platform)])
    entries = rule_runner.request(DigestEntries, [downloaded.digest])
    assert downloaded.exe.removeprefix("./") in {entry.path for entry in entries}
