import pytest
from pants.core.util_rules import external_tool
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.platform import Platform
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner

from pants_backend_k8s.util.kubeconfig import (
    HostKubeconfigFieldSet,
    HostKubeconfigRequest,
    KubeconfigResponse,
)
from pants_backend_k8s.util.kubeconfig import rules as kubeconfig_rules


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[*kubeconfig_rules(), QueryRule(KubeconfigResponse, [HostKubeconfigRequest])]
    )


def test_host_k8s_request(rule_runner):
    request = HostKubeconfigRequest(HostKubeconfigFieldSet(""))

    response = rule_runner.request(KubeconfigResponse, [request])

    assert response.path != None
