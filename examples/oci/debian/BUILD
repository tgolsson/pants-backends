oci_pull_images(
    name="debian",
    repository="docker.io/library/debian",
    variants={
        "bookworm-20241016-slim": "d83056144b2dd301730d2739635c8cbdeaaae20d6887146434184f8c060f03ce",
    },
    architecture="amd64",
    os="linux",
)

# `jq` and the libraries it needs on top of bookworm-slim, pinned to a snapshot.debian.org snapshot.
_SNAPSHOT = "https://snapshot.debian.org/archive/debian/20241001T000000Z/pool/main"

file(
    name="jq_deb",
    source=http_source(
        url=f"{_SNAPSHOT}/j/jq/jq_1.6-2.1_amd64.deb",
        sha256="d1833fdd7a1340b59c71c223178940044fc41a8c8e28861bc03ab258d791d303",
        len=64932,
        filename="jq_1.6-2.1_amd64.deb",
    ),
)

file(
    name="libjq1_deb",
    source=http_source(
        url=f"{_SNAPSHOT}/j/jq/libjq1_1.6-2.1_amd64.deb",
        sha256="7dc22726010e660d294682018b0013badda93b6791049cff9edb303ddffbb35d",
        len=134944,
        filename="libjq1_1.6-2.1_amd64.deb",
    ),
)

file(
    name="libonig5_deb",
    source=http_source(
        url=f"{_SNAPSHOT}/libo/libonig/libonig5_6.9.8-1_amd64.deb",
        sha256="59ecfce6d88c7c4b09496ce182b3b8303e8e8477664e009b16ae83a09cd12be7",
        len=187828,
        filename="libonig5_6.9.8-1_amd64.deb",
    ),
)

oci_deb_layer(
    name="jq",
    debs=[":jq_deb", ":libjq1_deb", ":libonig5_deb"],
)

oci_image_build(
    name="image",
    base=[":debian#bookworm-20241016-slim"],
    layers=[":jq"],
    repository="pants-backend-oci/examples/debian",
    tag="latest",
    entrypoint="/usr/bin/jq",
    args=["--version"],
)
