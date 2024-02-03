from __future__ import annotations

import os.path
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pants.engine.environment import EnvironmentName
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import FieldSet, Target
from pants.engine.unions import UnionMembership, UnionRule, union

from pants_backend_k8s.target_types import KubeconfigHostMarker, KubeconfigSourceField


@union(in_scope_types=[EnvironmentName])
@dataclass(frozen=True)
class KubeconfigRequest:
    target: Target

    field_set_type: ClassVar[FieldSet]


@dataclass(frozen=True)
class KubeconfigRequestRequest:
    target: Target


@dataclass(frozen=True)
class KubeconfigRequestWrap:
    request: KubeconfigRequest | None


@dataclass(frozen=True)
class KubeconfigResponse:
    path: str
    digest: Digest | None = None


@rule
def kubeconfig_request_request(
    request: KubeconfigRequestRequest, union_membership: UnionMembership
) -> KubeconfigRequestWrap:
    tgt = request.target

    concrete_requests = [
        request_type(request_type.field_set_type.create(tgt))
        for request_type in union_membership[FallibleKubeconfigRequest]
        if request_type.field_set_type.is_applicable(tgt)
    ]

    if len(concrete_requests) != 1:
        raise ValueError(
            f"Multiple or zero registered decrypters from {KubeconfigRequestRequest.__name__} can "
            f"build target {tgt.name}. It is ambiguous which implementation to use.\n\n"
            "Possible implementations:\n\n"
            f"{bullet_list(sorted(generator.__name__ for generator in concrete_requests))}"
        )

    if len(concrete_requests) == 0:
        return KubeconfigRequestWrap(None)

    first_concrete = concrete_requests[0]
    return KubeconfigRequestWrap(first_concrete)


@dataclass(frozen=True)
class ConfigurationFileRequest:
    search_paths: tuple[str, ...]
    """'Which paths to search for the configuration file."""

    file_name: str
    """The filename to locate"""


@dataclass(frozen=True)
class ConfigurationFileResponse:
    found: bool

    path: str | None = None


@rule(desc="Locate configuration file")
async def _locate_configuration_file(
    request: ConfigurationFileRequest,
) -> ConfigurationFileResponse:
    for path in request.search_paths:
        path = Path(path).expanduser()
        candidate = path / request.file_name
        print(f"Testing {candidate}")
        if candidate.exists():
            return ConfigurationFileResponse(
                found=True,
                path=str(candidate.resolve()),
            )

    return ConfigurationFileResponse(
        found=False,
    )


@dataclass(frozen=True)
class HostKubeconfigRequest(KubeconfigRequest):
    pass


class HostKubeconfigFieldSet(FieldSet):
    required_fields = (KubeconfigHostMarker,)


@rule(desc="Locating host kubeconfig file")
async def locate_kubconfig_file(request: HostKubeconfigRequest) -> KubeconfigResponse:
    result = await Get(ConfigurationFileResponse, ConfigurationFileRequest(("~/.kube",), "config"))

    if not result.found:
        raise ValueError("Failed to locate kubeconfig file on the host.")

    return KubeconfigResponse(path=result.path)


def rules():
    return [
        *collect_rules(),
        UnionRule(KubeconfigRequest, HostKubeconfigRequest),
    ]
