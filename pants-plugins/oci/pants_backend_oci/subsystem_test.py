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
    "platform",
    (
        Platform.linux_arm64,
        Platform.linux_x86_64,
        Platform.macos_arm64,
        Platform.macos_x86_64,
    ),
)
def test_platform_download_skopeo(
    rule_runner,
    platform,
):
    tool = rule_runner.request(SkopeoTool, [])
    rule_runner.request(DownloadedExternalTool, [tool.get_request(platform)])


@pytest.mark.parametrize(
    "platform",
    (Platform.linux_x86_64,),
)
def test_platform_download_umoci(
    rule_runner,
    platform,
):
    tool = rule_runner.request(UmociTool, [])
    rule_runner.request(DownloadedExternalTool, [tool.get_request(platform)])


@pytest.mark.parametrize(
    "platform",
    (
        Platform.linux_arm64,
        Platform.linux_x86_64,
        Platform.macos_arm64,
        Platform.macos_x86_64,
    ),
)
def test_platform_download_umoci_nightly(
    rule_runner,
    platform,
):
    tool = rule_runner.request(UmociTool, [])
    tool.version = "nightly"

    rule_runner.request(DownloadedExternalTool, [tool.get_request(platform)])


@pytest.mark.parametrize(
    "platform",
    (
        Platform.linux_arm64,
        Platform.linux_x86_64,
    ),
)
def test_platform_download_runc(
    rule_runner,
    platform,
):
    tool = rule_runner.request(RuncTool, [])

    rule_runner.request(DownloadedExternalTool, [tool.get_request(platform)])
