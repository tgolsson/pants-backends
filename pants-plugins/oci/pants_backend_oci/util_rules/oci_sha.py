""" """

import json
from dataclasses import dataclass

from pants.engine.fs import Digest, DigestSubset, PathGlobs
from pants.engine.intrinsics import digest_subset_to_digest, get_digest_contents
from pants.engine.rules import collect_rules, rule


class MissingRequiredFile(Exception):
    pass


@dataclass(frozen=True)
class OciSha:
    image_digest: str


@dataclass(frozen=True)
class OciShaRequest:
    bundle_digest: Digest


@rule
async def extract_build_info_sha(request: OciShaRequest) -> OciSha:
    digest = await digest_subset_to_digest(
        DigestSubset(request.bundle_digest, PathGlobs(["build/index.json"]))
    )
    digest_contents = await get_digest_contents(digest)

    if not digest_contents:
        raise MissingRequiredFile("did not find `build/index.json` in OCI build context")

    index = json.loads(digest_contents[0].content)
    manifests = index["manifests"]
    sha256 = manifests[len(manifests) - 1]["digest"]

    return OciSha(sha256)


def rules():
    return collect_rules()
