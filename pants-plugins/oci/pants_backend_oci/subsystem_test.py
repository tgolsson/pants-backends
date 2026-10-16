import pytest
from pants.core.util_rules import external_tool
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.platform import Platform
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner

from pants_backend_oci.subsystem import RuncTool, SkopeoTool, UmociTool
from pants_backend_oci.subsystem import rules as subsystem_rules


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *external_tool.rules(),
            *subsystem_rules(),
            QueryRule(DownloadedExternalTool, [ExternalToolRequest]),
            QueryRule(SkopeoTool, []),
            QueryRule(UmociTool, []),
            QueryRule(RuncTool, []),
        ]
    )


@pytest.mark.parametrize(
    "tool_type,version,platform",
    [
        (tool_type, row.split("|")[0], Platform(row.split("|")[1].strip()))
        for tool_type in (SkopeoTool, UmociTool, RuncTool)
        for row in tool_type.default_known_versions
    ],
)
def test_platform_download(rule_runner, tool_type, version, platform):
    rule_runner.set_options([f"--{tool_type.options_scope}-version={version}"])
    tool = rule_runner.request(tool_type, [])
    rule_runner.request(DownloadedExternalTool, [tool.get_request(platform)])
