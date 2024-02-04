from __future__ import annotations

import os.path
from abc import ABC, ABCMeta, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Generic, Type, TypeVar

from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.environment import EnvironmentName
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import Dependencies, FieldSet, SourcesField, Target, Targets
from pants.engine.unions import UnionMembership, UnionRule, union

from pants_backend_k8s.target_types import (
    KUBECONFIG_COMMON_FIELDS,
    KubeconfigClusterField,
    KubeconfigContextField,
    KubeconfigHostMarker,
    KubeconfigNamespaceField,
    KubeconfigSourceField,
    KubeconfigUserField,
)


@union(in_scope_types=[EnvironmentName])
@dataclass(frozen=True)
class KubeconfigRequest:
    target: Target

    field_set_type: ClassVar[FieldSet]


_T = TypeVar("_T", bound=KubeconfigRequest)


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

    context: str | None = None
    namespace: str | None = None
    cluster: str | None = None
    user: str | None = None


@rule
def kubeconfig_request_request(
    request: KubeconfigRequestRequest, union_membership: UnionMembership
) -> KubeconfigRequestWrap:
    tgt = request.target

    concrete_requests = [
        request_type(request_type.field_set_type.create(tgt))
        for request_type in union_membership[KubeconfigRequest]
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

        if candidate.exists():
            return ConfigurationFileResponse(
                found=True,
                path=str(candidate.resolve()),
            )

    return ConfigurationFileResponse(
        found=False,
    )


@union(in_scope_types=[EnvironmentName])
@dataclass(frozen=True)
class KubeconfigFieldSet(Generic[_T], FieldSet, metaclass=ABCMeta):
    """FieldSet for KubeconfigRequest.

    Union members may list any fields required to fulfill the
    instantiation of the `KubeconfigResponse` result of the kubeconfig
    rule.

    """

    # Subclasses must provide this, to a union member (subclass) of `KubeconfigRequest`.
    kubeconfig_request_type: ClassVar[Type[_T]]  # type: ignore[misc]

    def _request(self) -> _T:
        """Internal helper for the core kubeconfig goal."""
        return self.kubeconfig_request_type(field_set=self)

    @classmethod
    def rules(cls) -> tuple[UnionRule, ...]:
        """Helper method for registering the union members."""
        return (
            UnionRule(KubeconfigFieldSet, cls),
            UnionRule(KubeconfigRequest, cls.kubeconfig_request_type),
        )


@dataclass(frozen=True)
class HostKubeconfigRequest(KubeconfigRequest):
    pass


@dataclass(frozen=True)
class HostKubeconfigFieldSet(KubeconfigFieldSet):
    kubeconfig_request_type = HostKubeconfigRequest
    required_fields = (KubeconfigHostMarker, *KUBECONFIG_COMMON_FIELDS)

    context: KubeconfigContextField
    namespace: KubeconfigNamespaceField
    cluster: KubeconfigClusterField
    user: KubeconfigUserField


HostKubeconfigRequest.field_set_type = HostKubeconfigFieldSet


@rule(desc="Locating host kubeconfig file")
async def get_kubeconfig_file(request: HostKubeconfigRequest) -> KubeconfigResponse:
    result = await Get(ConfigurationFileResponse, ConfigurationFileRequest(("~/.kube",), "config"))

    if not result.found:
        raise ValueError("Failed to locate kubeconfig file on the host.")

    return KubeconfigResponse(path=result.path)


@dataclass(frozen=True)
class FileKubeconfigRequest(KubeconfigRequest):
    pass


@dataclass(frozen=True)
class FileKubeconfigFieldSet(KubeconfigFieldSet):
    kubeconfig_request_type = FileKubeconfigRequest
    required_fields = (KubeconfigSourceField, *KUBECONFIG_COMMON_FIELDS)

    source: KubeconfigSourceField
    context: KubeconfigContextField
    namespace: KubeconfigNamespaceField
    cluster: KubeconfigClusterField
    user: KubeconfigUserField


FileKubeconfigRequest.field_set_type = FileKubeconfigFieldSet


@rule(desc="Loading kubeconfig file")
async def load_kubconfig_file(request: FileKubeconfigRequest) -> KubeconfigResponse:
    targets = await Get(Targets, Dependencies, request.target.from_generator)

    sources = await Get(
        SourceFiles,
        SourceFilesRequest(
            [request.target.source]
            + [tgt[SourcesField] for tgt in targets if tgt.has_field(SourcesField)]
        ),
    )

    if len(sources.snapshot.files) > 1:
        raise ValueError("Kubeconfig source must be a single file")

    return KubeconfigResponse(path=sources.snapshot.files[0], digest=sources.snapshot.digest)


def rules():
    return [
        *collect_rules(),
        *FileKubeconfigFieldSet.rules(),
        *HostKubeconfigFieldSet.rules(),
    ]
