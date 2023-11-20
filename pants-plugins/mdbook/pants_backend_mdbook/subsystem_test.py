import pytest
from pants.core.util_rules import external_tool
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.platform import Platform
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner

from pants_backend_mdbook.subsystem import MdBookTool
from pants_backend_mdbook.subsystem import rules as subsystem_rules


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *external_tool.rules(),
            *subsystem_rules(),
            QueryRule(DownloadedExternalTool, [ExternalToolRequest]),
            QueryRule(MdBookTool, []),
        ]
    )


@pytest.mark.parametrize(
    "platform",
    (
        Platform.linux_arm64,
        Platform.linux_x86_64,
        #        Platform.macos_arm64,
        Platform.macos_x86_64,
    ),
)
def test_platform_download_rustup(
    rule_runner,
    platform,
):
    mb = rule_runner.request(MdBookTool, [])
    rule_runner.request(DownloadedExternalTool, [mb.get_request(platform)])
