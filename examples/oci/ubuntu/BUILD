oci_pull_images(
    name="ubuntu",
    repository="docker.io/library/ubuntu",
    variants={
        "noble-20241009": "08a4455a1f5a50a3dd22309df017cec827f352258596174f1cbbb7da990d1368",
    },
    architecture="amd64",
    os="linux",
)

# The Ubuntu counterpart of `examples/oci/debian`: `jq` and `ca-certificates` on top of the noble base
# image, resolved from a snapshot.ubuntu.com snapshot into `noble.lock.json` by
# `pants generate-lockfiles --resolve=noble`. Ubuntu's debs are zstd-compressed. The snapshot serves
# arm64 packages under `/ubuntu` too, so one set of sources covers both architectures.
deb_packages(
    name="noble",
    resolve="noble",
    sources=[
        "https://snapshot.ubuntu.com/ubuntu/20241001T000000Z noble main",
        "https://snapshot.ubuntu.com/ubuntu/20241001T000000Z noble-updates main",
        "https://snapshot.ubuntu.com/ubuntu/20241001T000000Z noble-security main",
    ],
    architectures=["amd64", "arm64"],
    packages=["jq", "ca-certificates"],
    # Already in the noble base image.
    exclude=["libc6", "debconf", "libssl3t64"],
)

oci_deb_layer(
    name="jq",
    debs=[":noble#amd64/jq"],
)

oci_image_build(
    name="image",
    base=[":ubuntu#noble-20241009"],
    layers=[":jq"],
    repository="pants-backend-oci/examples/ubuntu",
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
    debs=[":noble#amd64/ca-certificates"],
)

oci_image_build(
    name="nonroot",
    base=[":ubuntu#noble-20241009"],
    layers=[":jq", ":passwd", ":group", ":cacerts"],
    repository="pants-backend-oci/examples/ubuntu-nonroot",
    tag="latest",
    user="nonroot",
    entrypoint="/bin/sh",
    args=["-c", "id && grep -c 'BEGIN CERTIFICATE' /etc/ssl/certs/ca-certificates.crt"],
)
