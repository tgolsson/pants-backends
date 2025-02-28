""" """

from dataclasses import dataclass

from pants.engine.internals.synthetic_targets import SyntheticAddressMaps, SyntheticTargetsRequest
from pants.engine.internals.target_adaptor import TargetAdaptor
from pants.engine.rules import collect_rules, rule
from pants.engine.unions import UnionRule

from pants_backend_oci.subsystem import OciSubsystem


@dataclass(frozen=True)
class SyntheticEmptyImageRequest(SyntheticTargetsRequest):
    path: str = SyntheticTargetsRequest.SINGLE_REQUEST_FOR_ALL_TARGETS


@rule
async def example_synthetic_targets(
    request: SyntheticEmptyImageRequest, oci: OciSubsystem
) -> SyntheticAddressMaps:
    return SyntheticAddressMaps.for_targets_request(
        request,
        [
            (
                "BUILD.oci_image_empty",
                (
                    TargetAdaptor(
                        "oci_image_empty",
                        oci.empty_image_target,
                        "synthetic target",
                    ),
                ),
            ),
        ],
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(SyntheticTargetsRequest, SyntheticEmptyImageRequest),
    ]
