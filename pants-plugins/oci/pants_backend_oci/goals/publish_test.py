import json
from dataclasses import dataclass

import pytest
from pants.core.register import rules as core_rules
from pants.core.util_rules import adhoc_binaries
from pants.core.util_rules import archive as pants_archive
from pants.core.util_rules import external_tool, source_files, system_binaries
from pants.engine import process
from pants.engine.addresses import Address
from pants.engine.fs import DigestContents
from pants.engine.internals import graph
from pants.testutil.rule_runner import QueryRule, RuleRunner

from pants_backend_oci import subsystem, synthetic_targets, util_rules
from pants_backend_oci.goals import package, publish
from pants_backend_oci.targets import ImageBuild, ImageEmpty


@dataclass(frozen=True)
class MockMetadata:
    sha: str


@dataclass(frozen=True)
class MockPackage:
    artifacts: tuple[MockMetadata]


@pytest.fixture
def rule_runner() -> RuleRunner:
    rule_runner = RuleRunner(
        target_types=[ImageEmpty, ImageBuild],
        rules=[
            *core_rules(),
            *external_tool.rules(),
            *graph.rules(),
            *package.rules(),
            *pants_archive.rules(),
            *process.rules(),
            *publish.rules(),
            *source_files.rules(),
            *synthetic_targets.rules(),
            *system_binaries.rules(),
            *adhoc_binaries.rules(),
            *subsystem.rules(),
            *util_rules.rules(),
            QueryRule(
                publish.PublishProcesses,
                [
                    publish.PublishImageRequest,
                ],
            ),
            QueryRule(package.BuiltPackage, [package.ImageFieldSet]),
        ],
    )

    return rule_runner


def test_publish_with_no_repository_is_skipped(rule_runner) -> None:
    files = {"BUILD": "oci_image_build(name='empty_derived', base=[':empty'])"}

    rule_runner.write_files(files)
    target = rule_runner.get_target(Address("", target_name="empty_derived"))

    request = publish.PublishImageRequest(
        publish.PublishImageFieldSet.create(target),
        packages=(
            MockPackage(
                artifacts=(MockMetadata(sha="sha256:1234567890"),),
            ),
        ),
    )
    result: publish.PublishProcesses = rule_runner.request(publish.PublishProcesses, [request])

    assert len(result) == 1
    assert result[0].description == "(because it has no repository)"
    assert result[0].names == (f"{target.address}",)


@pytest.mark.parametrize("package_output", ["archive", "layout"])
def test_publish_with_repository_has_process(rule_runner, package_output: str) -> None:
    files = {"BUILD": "oci_image_build(name='empty_derived', base=[':empty'], repository='foobar')"}

    rule_runner.set_options([f"--oci-package-output={package_output}"])
    rule_runner.write_files(files)
    target = rule_runner.get_target(Address("", target_name="empty_derived"))

    pack_request = package.ImageFieldSet.create(target)
    built_package = rule_runner.request(package.BuiltPackage, [pack_request])
    request = publish.PublishImageRequest(
        publish.PublishImageFieldSet.create(target),
        packages=(built_package,),
    )
    result: publish.PublishProcesses = rule_runner.request(publish.PublishProcesses, [request])

    assert len(result) == 1
    assert result[0].description.startswith("foobar:latest")
    artifact = built_package.artifacts[0]
    assert result[0].names == (artifact.sha,)
    assert result[0].process.process.argv[1:] == ("push", "build", "foobar:latest")
    # The pushed layout holds the packaged image's manifest, so the registry digest matches.
    contents = rule_runner.request(DigestContents, [result[0].process.process.input_digest])
    (index,) = [json.loads(c.content) for c in contents if c.path == "build/index.json"]
    assert [m["digest"] for m in index["manifests"]] == [artifact.sha]

    packaged = {c.path: c.content for c in rule_runner.request(DigestContents, [built_package.digest])}
    if package_output == "archive":
        assert list(packaged) == [artifact.relpath]
        assert artifact.relpath.endswith(".tar")
        return
    # A layout package holds the index naming the image and exactly the blobs it references.
    layout = artifact.relpath
    assert not layout.endswith(".tar")
    (entry,) = json.loads(packaged[f"{layout}/index.json"])["manifests"]
    assert entry["digest"] == artifact.sha
    manifest = json.loads(packaged[f"{layout}/blobs/sha256/{artifact.sha[7:]}"])
    own = {artifact.sha, manifest["config"]["digest"], *(layer["digest"] for layer in manifest["layers"])}
    assert set(packaged) == {
        f"{layout}/index.json",
        f"{layout}/oci-layout",
        *(f"{layout}/blobs/sha256/{digest[7:]}" for digest in own),
    }
