import pytest
from pants.core.util_rules import external_tool
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.platform import Platform
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner

from pants_backend_k8s.subsystem import KubernetesTool


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *external_tool.rules(),
            *KubernetesTool.rules(),
            QueryRule(DownloadedExternalTool, [ExternalToolRequest]),
            QueryRule(KubernetesTool, []),
        ]
    )


@pytest.mark.parametrize(
    "version,platform",
    [
        (row.split("|")[0], Platform(row.split("|")[1].strip()))
        for row in KubernetesTool.default_known_versions
    ],
)
def test_platform_download(rule_runner, version, platform):
    rule_runner.set_options([f"--{KubernetesTool.options_scope}-version={version}"])
    tool = rule_runner.request(KubernetesTool, [])
    rule_runner.request(DownloadedExternalTool, [tool.get_request(platform)])
