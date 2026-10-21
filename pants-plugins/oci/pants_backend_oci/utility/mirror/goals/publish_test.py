from types import SimpleNamespace

import pytest
from pants.engine.env_vars import EnvironmentVars
from pants.engine.fs import EMPTY_DIGEST
from pants.engine.platform import Platform
from pants.testutil.rule_runner import run_rule_with_mocks

from pants_backend_oci.utility.mirror.goals.publish import (
    OciMirrorProcessRequest,
    oci_mirror_process,
)


@pytest.mark.parametrize(
    "tag, destination",
    [
        ("", "mirror.example/python"),
        ("3.12", "mirror.example/python:3.12"),
    ],
)
def test_mirror_copies_with_crane(tag, destination) -> None:
    process = run_rule_with_mocks(
        oci_mirror_process,
        rule_args=[
            OciMirrorProcessRequest(
                digest="1234", source="docker.io/library/python", tag=tag, destination="mirror.example/python"
            ),
            SimpleNamespace(get_request=lambda _: None),
            Platform.linux_x86_64,
        ],
        mock_calls={
            "pants.core.util_rules.external_tool.download_external_tool": lambda request: SimpleNamespace(
                exe="./crane", digest=EMPTY_DIGEST
            ),
            "pants.core.util_rules.env_vars.environment_vars_subset": lambda request,
            **kwargs: EnvironmentVars(),
        },
    )

    assert process.argv == ("./crane", "copy", "docker.io/library/python@sha256:1234", destination)
