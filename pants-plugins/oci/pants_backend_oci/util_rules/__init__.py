from pants_backend_oci.util_rules import (
    archive,
    build_image_bundle,
    image_bundle,
    layer,
    oci_sha,
    pull_image_bundle,
    unpack,
)


def rules():
    return [
        *archive.rules(),
        *build_image_bundle.rules(),
        *image_bundle.rules(),
        *layer.rules(),
        *oci_sha.rules(),
        *pull_image_bundle.rules(),
        *unpack.rules(),
    ]
