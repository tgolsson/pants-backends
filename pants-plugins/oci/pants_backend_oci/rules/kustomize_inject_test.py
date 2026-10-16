from types import SimpleNamespace

import pytest
from pants.engine.addresses import Address
from pants.engine.fs import EMPTY_DIGEST
from pants.engine.process import Process
from pants.testutil.rule_runner import run_rule_with_mocks

from pants_backend_kustomize.requests import KustomizeInjectData
from pants_backend_oci.rules.kustomize_inject import (
    KustomizeInjectOciTagFieldSet,
    KustomizeInjectOciTagRequest,
    generate_oci_tag_injection,
)
from pants_backend_oci.targets import ImageBuild
from pants_backend_oci.util_rules.image_bundle import (
    FallibleImageBundle,
    FallibleImageBundleRequest,
    FallibleImageBundleRequestWrap,
    ImageBundle,
)


@pytest.mark.parametrize("tag", [None, "release"])
@pytest.mark.parametrize("is_local", [True, False])
def test_image_tag_injection_publishes_local_layout(tag, is_local):
    address = Address("images", target_name="app")
    target = ImageBuild({"repository": "registry.example/app", "tag": tag}, address)
    field_set = KustomizeInjectOciTagFieldSet.create(target)
    image = ImageBundle(EMPTY_DIGEST, "sha256:1234", is_local=is_local)
    published = []
    executed = []

    def publish(request, **kwargs):
        published.append(request)
        return Process(argv=("/bin/true",), description="Mock registry upload")

    result = run_rule_with_mocks(
        generate_oci_tag_injection,
        rule_args=[KustomizeInjectOciTagRequest(field_set)],
        mock_calls={
            "pants_backend_oci.util_rules.image_bundle.resolve_image_bundle": lambda request,
            **kwargs: FallibleImageBundle(image),
            "pants.engine.internals.graph.resolve_target": lambda request, **kwargs: SimpleNamespace(
                target=target
            ),
            "pants_backend_oci.util_rules.image_bundle.ibr_to_fibr": lambda request, **kwargs: (
                FallibleImageBundleRequestWrap(FallibleImageBundleRequest(target))
            ),
            **(
                {
                    "pants_backend_oci.goals.publish.publish_oci_process": publish,
                    "pants.engine.process.fallible_to_exec_result_or_raise": lambda process: executed.append(
                        process
                    ),
                }
                if is_local
                else {}
            ),
        },
    )

    assert result == KustomizeInjectData(address, image.image_sha)
    assert len(published) == len(executed) == int(is_local)
    if is_local:
        assert published[0].directory == "build:build"
        assert published[0].input_digest == image.digest
        assert published[0].repository == "registry.example/app"
        assert published[0].tag == (tag or "latest")
