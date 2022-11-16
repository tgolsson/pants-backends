from pants_backend_oci.util_rules import build_image_bundle, image_bundle, pull_image_bundle, unpack


def rules():
    return [
        *image_bundle.rules(),
        *pull_image_bundle.rules(),
        *build_image_bundle.rules(),
        *unpack.rules(),
    ]
