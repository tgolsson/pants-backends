from dataclasses import dataclass

from pants.backend.python.goals.publish import (
    PublishPythonPackageRequest,
    SkipTwineUploadField,
    twine_upload_args,
)
from pants.backend.python.subsystems.twine import TwineSubsystem
from pants.backend.python.target_types import (
    BuildBackendEnvVarsField,
    GenerateSetupField,
    InterpreterConstraintsField,
    LongDescriptionPathField,
    PythonDistributionDependenciesField,
    PythonDistributionEntryPointsField,
    PythonProvidesField,
    SDistConfigSettingsField,
    SDistField,
    WheelConfigSettingsField,
    WheelField,
)
from pants.backend.python.util_rules.pex import PexRequest, VenvPex, VenvPexProcess
from pants.core.goals.publish import (
    PublishFieldSet,
    PublishOutputData,
    PublishPackages,
    PublishProcesses,
)
from pants.core.util_rules.config_files import ConfigFiles, ConfigFilesRequest
from pants.engine.addresses import Addresses, UnparsedAddressInputs
from pants.engine.fs import CreateDigest, Digest, MergeDigests, Snapshot
from pants.engine.process import InteractiveProcess, Process
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import COMMON_TARGET_FIELDS, Target, WrappedTarget, WrappedTargetRequest
from pants.option.global_options import GlobalOptions
from pants.util.docutil import doc_url
from pants.util.strutil import softwrap

from pants_backend_secrets.exception import FailedDecryptException, NoDecrypterException
from pants_backend_secrets.secret import SecretsField
from pants_backend_secrets.secret_request import (
    FallibleSecretsRequest,
    FallibleSecretsResponse,
    SecretsRequestRequest,
    SecretsRequestWrap,
)


class PublishSecretsField(SecretsField):
    alias = "repo_secrets"

    help = softwrap("Dictionary with all secrets to request. Each key should match a repository name.")


class PublishPythonWithSecretPackageRequest(PublishPythonPackageRequest):
    pass


@dataclass(frozen=True)
class PublishPythonWithSecretPackageFieldSet(PublishFieldSet):
    publish_request_type = PublishPythonWithSecretPackageRequest
    required_fields = (PublishSecretsField,)

    repositories: PublishSecretsField
    skip_twine: SkipTwineUploadField

    def get_output_data(self) -> PublishOutputData:
        return PublishOutputData(
            {
                "publisher": "twine",
                **super().get_output_data(),
            }
        )


class PythonDistributionWithSecret(Target):
    alias = "python_distribution_with_secret"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        InterpreterConstraintsField,
        PythonDistributionDependenciesField,
        PythonDistributionEntryPointsField,
        PythonProvidesField,
        GenerateSetupField,
        WheelField,
        SDistField,
        WheelConfigSettingsField,
        SDistConfigSettingsField,
        BuildBackendEnvVarsField,
        LongDescriptionPathField,
        PublishSecretsField,
    )
    help = softwrap(f"""
        A publishable Python setuptools distribution (e.g. an sdist or wheel).
        See {doc_url('python-distributions')}.
        """)


@rule
async def twine_upload_with_secret(
    request: PublishPythonWithSecretPackageRequest,
    twine_subsystem: TwineSubsystem,
    global_options: GlobalOptions,
) -> PublishProcesses:
    dists = tuple(
        artifact.relpath for pkg in request.packages for artifact in pkg.artifacts if artifact.relpath
    )

    if twine_subsystem.skip or not dists:
        return PublishProcesses()

    # Too verbose to provide feedback as to why some packages were skipped?
    skip = None
    if request.field_set.skip_twine.value:
        skip = f"(by `{request.field_set.skip_twine.alias}` on {request.field_set.address})"
    elif not request.field_set.repositories.value:
        # I'd rather have used the opt_out mechanism on the field set, but that gives no hint as to
        # why the target was not applicable..
        skip = f"(no `{request.field_set.repositories.alias}` specified for {request.field_set.address})"

    if skip:
        return PublishProcesses(
            [
                PublishPackages(
                    names=dists,
                    description=skip,
                ),
            ]
        )

    twine_pex, packages_digest, config_files = await MultiGet(
        Get(VenvPex, PexRequest, twine_subsystem.to_pex_request()),
        Get(Digest, MergeDigests(pkg.digest for pkg in request.packages)),
        Get(ConfigFiles, ConfigFilesRequest, twine_subsystem.config_request()),
    )

    ca_cert_request = twine_subsystem.ca_certs_digest_request(global_options.ca_certs_path)
    ca_cert = await Get(Snapshot, CreateDigest, ca_cert_request) if ca_cert_request else None
    ca_cert_digest = (ca_cert.digest,) if ca_cert else ()

    input_digest = await Get(
        Digest, MergeDigests((packages_digest, config_files.snapshot.digest, *ca_cert_digest))
    )
    pex_proc_requests = []
    secret_requests = []

    repositories = []
    for repo, secret in request.field_set.repositories.value.items():
        repositories.append(repo)
        secret_address = await Get(
            Addresses,
            UnparsedAddressInputs(
                [secret],
                owning_address=request.field_set.address,
                description_of_origin=f"the `{secret}` from the target {request.field_set.repositories}",
            ),
        )
        wrapped_target = await Get(
            WrappedTarget,
            WrappedTargetRequest(secret_address[0], description_of_origin="twine_upload_with_secret"),
        )

        secret_request = await Get(SecretsRequestWrap, SecretsRequestRequest(wrapped_target.target))
        if secret_request.request is None:
            raise NoDecrypterException(
                f"No valid decrypter found for secret: `{secret_address[0]}` of "
                f"type `{wrapped_target.target.alias}`"
            )

        secret_requests.append(Get(FallibleSecretsResponse, FallibleSecretsRequest, secret_request.request))

    fallible_secrets = await MultiGet(*secret_requests)
    secrets = []
    for repo, maybe_secret in zip(repositories, fallible_secrets):
        if maybe_secret.exit_code != 0:
            raise FailedDecryptException(
                f"Failed decrypting secret for repo: {repo}",
                maybe_secret.stdout,
                maybe_secret.stderr,
            )

        assert maybe_secret.response is not None, "cannot be None if exit_code is 0"
        secrets.append(maybe_secret.response.value)

    for repo, secret in zip(repositories, secrets):
        pex_proc_requests.append(
            VenvPexProcess(
                twine_pex,
                argv=twine_upload_args(twine_subsystem, config_files, repo, dists, ca_cert),
                input_digest=input_digest,
                extra_env={
                    "TWINE_USERNAME": "__token__",
                    "TWINE_PASSWORD": secret.value,
                },
                description=repo,
            )
        )

    processes = await MultiGet(Get(Process, VenvPexProcess, request) for request in pex_proc_requests)

    return PublishProcesses(
        PublishPackages(
            names=dists,
            process=InteractiveProcess.from_process(process),
            description=process.description,
            data=PublishOutputData({"repository": process.description}),
        )
        for process in processes
    )


def rules():
    return [
        *collect_rules(),
        *PublishPythonWithSecretPackageFieldSet.rules(),
    ]
