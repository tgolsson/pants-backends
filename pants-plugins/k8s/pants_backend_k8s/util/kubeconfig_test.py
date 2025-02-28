from textwrap import dedent

import pytest
from pants.core.util_rules.source_files import rules as source_rules
from pants.engine.rules import QueryRule
from pants.engine.target import Address
from pants.testutil.rule_runner import RuleRunner

from pants_backend_k8s.target_types import HostKubeConfig, KubeConfig
from pants_backend_k8s.util.kubeconfig import (
    FileKubeconfigFieldSet,
    FileKubeconfigRequest,
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
        target_types=[HostKubeConfig, KubeConfig],
    )


# def test_host_k8s_request(rule_runner):
#     rule_runner.write_files(
#         {
#             "BUILD": dedent(
#                 """
#             host_kubeconfig(name="config"),
#             """
#             )
#         }
#     )
#     target = rule_runner.get_target(Address("", target_name="config"))
#     request = HostKubeconfigRequest(HostKubeconfigFieldSet.create(target))

#     response = rule_runner.request(KubeconfigResponse, [request])

#     assert response.path is not None


def test_file_k8s_request(rule_runner):
    rule_runner.write_files(
        {
            "BUILD": dedent("""
            kubeconfig(name="config", from_source="foo.yaml"),
            """),
            "foo.yaml": "some_text\n",
        }
    )

    target = rule_runner.get_target(Address("", target_name="config"))
    request = FileKubeconfigRequest(FileKubeconfigFieldSet.create(target))

    response = rule_runner.request(KubeconfigResponse, [request])

    assert response.path is not None
