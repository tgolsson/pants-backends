import pytest
from pants.engine.addresses import Address
from pants.engine.target import InvalidFieldException

from pants_backend_oci.target_types import ImageBuildCommand

ADDRESS = Address("oci", target_name="layer")


def test_commands_string_or_list() -> None:
    assert ImageBuildCommand("echo hi", ADDRESS).value == "echo hi"
    assert ImageBuildCommand(["echo", "hi"], ADDRESS).value == ("echo", "hi")
    assert ImageBuildCommand(None, ADDRESS).value is None


@pytest.mark.parametrize("raw", [[], ["echo", 1], 3])
def test_commands_rejects_other_values(raw: object) -> None:
    with pytest.raises(InvalidFieldException, match="non-empty list of strings"):
        ImageBuildCommand(raw, ADDRESS)
