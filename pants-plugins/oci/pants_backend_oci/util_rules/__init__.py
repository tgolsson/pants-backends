from pants_backend_oci.util_rules import (
    assemble,
    binaries,
    build_image_artifact,
    build_image_bundle,
    deb_layer,
    empty_image_bundle,
    helper_layer,
    image_bundle,
    layer,
    pull_image_bundle,
    run,
    runner,
    sandboxed,
)


def rules():
    return [
        *deb_layer.rules(),
        *helper_layer.rules(),
        *build_image_bundle.rules(),
        *image_bundle.rules(),
        *layer.rules(),
        *assemble.rules(),
        *pull_image_bundle.rules(),
        *empty_image_bundle.rules(),
        *binaries.rules(),
        *build_image_artifact.rules(),
        *run.rules(),
        *runner.rules(),
        *sandboxed.rules(),
    ]
