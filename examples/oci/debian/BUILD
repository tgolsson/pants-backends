oci_pull_images(
    name="debian",
    repository="docker.io/library/debian",
    variants={
        "bookworm-20241016-slim": "d83056144b2dd301730d2739635c8cbdeaaae20d6887146434184f8c060f03ce",
    },
    architecture="amd64",
    os="linux",
)

# `jq` and the libraries it needs on top of bookworm-slim, plus `ca-certificates` for the CA bundle
# and `libc-bin` for the `C.utf8` locale, resolved from a snapshot.debian.org snapshot into
# `bookworm.lock.json` by `pants generate-lockfiles --resolve=bookworm`.
deb_packages(
    name="bookworm",
    resolve="bookworm",
    sources=["https://snapshot.debian.org/archive/debian/20241001T000000Z bookworm main"],
    architectures=["amd64", "arm64"],
    packages=["jq", "ca-certificates", "libc-bin"],
    # Already in the bookworm-slim base image.
    exclude=["libc6"],
    # `/usr/share/keyrings/debian-archive-keyring.gpg` from the snapshot's
    # `debian-archive-keyring_2023.3+deb12u1_all.deb` (sha256 c1bac61abefa0d957394d33c02b7bfb2a3ab3ce5e6d90617c4019ddea4bdbf63).
    keyrings=["debian-archive-keyring.gpg"],
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

# The users, groups and CA bundle `useradd`, `groupadd` and `update-ca-certificates` would create,
# built without running anything. They replace the base image's files, so `root` is listed too, and
# the base's other accounts (`nobody`, `daemon`, ...) are gone unless listed here.
oci_passwd_layer(
    name="passwd",
    users=[
        {"name": "root", "uid": 0, "gid": 0, "home": "/root", "shell": "/bin/sh"},
        {"name": "nonroot", "uid": 65532, "gid": 65532, "gecos": "nonroot"},
    ],
)

oci_group_layer(
    name="group",
    groups=[
        {"name": "root", "gid": 0},
        {"name": "nonroot", "gid": 65532, "members": ["nonroot"]},
    ],
)

oci_cacerts_layer(
    name="cacerts",
    debs=[":bookworm#amd64/ca-certificates"],
)

# Only the compiled `C.utf8` locale from `libc-bin`, without its binaries or running `locale-gen`.
oci_locale_layer(
    name="locales",
    debs=[":bookworm#amd64/libc-bin"],
    locales=["C.UTF-8"],
)

oci_image_build(
    name="nonroot",
    base=[":debian#bookworm-20241016-slim"],
    layers=[":jq", ":passwd", ":group", ":cacerts"],
    repository="pants-backend-oci/examples/debian-nonroot",
    tag="latest",
    user="nonroot",
    entrypoint="/bin/sh",
    args=["-c", "id && grep -c 'BEGIN CERTIFICATE' /etc/ssl/certs/ca-certificates.crt"],
)
