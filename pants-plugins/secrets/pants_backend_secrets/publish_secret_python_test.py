from pants.backend.python import register as python_register
from pants.backend.python.target_types import PythonDistributionOutputPathField
from pants.core.goals.package import BuiltPackage, BuiltPackageArtifact
from pants.core.goals.publish import PublishProcesses
from pants.core.register import rules as core_rules
from pants.engine.addresses import Address
from pants.engine.fs import EMPTY_DIGEST
from pants.testutil.rule_runner import QueryRule, RuleRunner

from pants_backend_secrets import register
from pants_backend_secrets.publish_secret_python import (
    PublishPythonWithSecretPackageFieldSet,
    PublishPythonWithSecretPackageRequest,
)


def test_publish_constructs_process_without_uploading() -> None:
    runner = RuleRunner(
        rules=[
            *core_rules(),
            *python_register.rules(),
            *register.rules(),
            QueryRule(PublishProcesses, [PublishPythonWithSecretPackageRequest]),
        ],
        target_types=register.target_types(),
        objects=python_register.build_file_aliases().objects,
    )
    runner.set_options(
        [],
        env={"TEST_PUBLISH_TOKEN": "dummy-token"},
        env_inherit={"PATH", "HOME", "PYENV_ROOT"},
    )
    runner.write_files(
        {
            "BUILD": """
env_secret(name="token", key="TEST_PUBLISH_TOKEN")
python_distribution_with_secret(
    name="package",
    provides=python_artifact(name="test-package", version="0.0.1"),
    output_path="custom",
    repo_secrets={"@pypi": ":token"},
)
python_distribution_with_secret(
    name="skipped",
    provides=python_artifact(name="skipped-package", version="0.0.1"),
    skip_twine=True,
    repo_secrets={"@pypi": ":missing-secret"},
)
""",
        }
    )
    target = runner.get_target(Address("", target_name="package"))
    assert target[PythonDistributionOutputPathField].value == "custom"
    result = runner.request(
        PublishProcesses,
        [
            PublishPythonWithSecretPackageRequest(
                PublishPythonWithSecretPackageFieldSet.create(target),
                packages=(
                    BuiltPackage(
                        EMPTY_DIGEST,
                        (BuiltPackageArtifact("test_package-0.0.1.tar.gz"),),
                    ),
                ),
            )
        ],
    )
    assert len(result) == 1
    interactive_process = result[0].process
    assert interactive_process is not None
    process = interactive_process.process
    assert "upload" in process.argv
    assert "--repository=pypi" in process.argv
    assert process.env["TWINE_USERNAME"] == "__token__"
    assert process.env["TWINE_PASSWORD"] == "dummy-token"

    skipped_target = runner.get_target(Address("", target_name="skipped"))
    skipped = runner.request(
        PublishProcesses,
        [
            PublishPythonWithSecretPackageRequest(
                PublishPythonWithSecretPackageFieldSet.create(skipped_target),
                packages=(
                    BuiltPackage(
                        EMPTY_DIGEST,
                        (BuiltPackageArtifact("skipped_package-0.0.1.tar.gz"),),
                    ),
                ),
            )
        ],
    )
    assert len(skipped) == 1
    assert skipped[0].process is None
    assert "skip_twine" in skipped[0].description
