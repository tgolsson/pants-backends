oci_pull_images(
    name="debian",
    repository="docker.io/library/debian",
    variants={
        "bookworm-20241016-slim": "d83056144b2dd301730d2739635c8cbdeaaae20d6887146434184f8c060f03ce",
    },
    architecture="amd64",
    os="linux",
)

# `jq` and the libraries it needs on top of bookworm-slim, resolved from a snapshot.debian.org
# snapshot into `bookworm.lock.json` by `pants generate-lockfiles --resolve=bookworm`.
deb_packages(
    name="bookworm",
    resolve="bookworm",
    sources=["https://snapshot.debian.org/archive/debian/20241001T000000Z bookworm main"],
    architectures=["amd64", "arm64"],
    packages=["jq"],
    # Already in the bookworm-slim base image.
    exclude=["libc6"],
)

oci_deb_layer(
    name="jq",
    debs=[":bookworm#amd64/jq"],
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
