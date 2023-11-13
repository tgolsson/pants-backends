from pants_backend_oci.util_rules import (
    archive,
    binaries,
    build_image_artifact,
    build_image_bundle,
    configure,
    copy,
    empty_image_bundle,
    image_bundle,
    jq,
    layer,
    oci_sha,
    pull_image_bundle,
    run,
    tools,
    unpack,
)


def rules():
    return [
        *copy.rules(),
        *archive.rules(),
        *build_image_bundle.rules(),
        *image_bundle.rules(),
        *layer.rules(),
        *oci_sha.rules(),
        *pull_image_bundle.rules(),
        *unpack.rules(),
        *empty_image_bundle.rules(),
        *jq.rules(),
        *binaries.rules(),
        *build_image_artifact.rules(),
        *run.rules(),
        *configure.rules(),
        *tools.rules(),
    ]
