from textwrap import dedent

import pytest
from pants.core.util_rules import external_tool
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.source_files import rules as source_rules
from pants.engine.platform import Platform
from pants.engine.rules import QueryRule
from pants.engine.target import Address
from pants.testutil.rule_runner import RuleRunner

from pants_backend_k8s.target_types import KubeConfig
from pants_backend_k8s.util.kubeconfig import (
    FileKubeconfigFieldSet,
    FileKubeconfigRequest,
    HostKubeconfigFieldSet,
    HostKubeconfigRequest,
    KubeconfigResponse,
)
from pants_backend_k8s.util.kubeconfig import rules as kubeconfig_rules


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *kubeconfig_rules(),
            *source_rules(),
            QueryRule(KubeconfigResponse, [HostKubeconfigRequest]),
            QueryRule(KubeconfigResponse, [FileKubeconfigRequest]),
        ],
        target_types=[KubeConfig],
    )


def test_host_k8s_request(rule_runner):
    request = HostKubeconfigRequest(HostKubeconfigFieldSet(""))

    response = rule_runner.request(KubeconfigResponse, [request])

    assert response.path != None


def test_file_k8s_request(rule_runner):
    rule_runner.write_files(
        {
            "BUILD": dedent(
                """
            kubeconfig(name="config", config_file="foo.yaml"),
            """
            ),
            "foo.yaml": "some_text\n",
        }
    )

    target = rule_runner.get_target(Address("", target_name="config"))
    request = FileKubeconfigRequest(FileKubeconfigFieldSet.create(target))

    response = rule_runner.request(KubeconfigResponse, [request])

    assert response.path != None
