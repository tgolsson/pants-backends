oci_pull_images(
    name="python3-d12",
    repository="gcr.io/distroless/python3-debian12",
    variants={"latest": "87406cabca8cc6846b5ea08bd9b25960b4e29b845f76c1af0e3f4a0affd1d7b4"},
    architecture="amd64",
    os="linux",
)

pex_binary(name="example", entry_point="example.py:main", shebang="#!/usr/bin/python")

python_sources(name="examples")
oci_image_build(
    name="oci",
    base=[":python3-d12#latest"],
    packages=[
        ":example",
    ],
    repository="harbor.sbg.dev/test/pants-plugin-oci-example",
    tag="latest",
)

pex_binary(name="example2", entry_point="example2.py:main", shebang="#!/usr/bin/python")

oci_image_build(
    name="oci2",
    base=[":oci#latest"],
    packages=[
        ":example2",
    ],
    repository="harbor.sbg.dev/test/pants-plugin-oci-example-2",
    tag="latest",
)


oci_image_build(
    name="with_empty_base",
    base=["//:empty"],
    packages=[
        ":example2",
    ],
    tag="latest",
)
