oci_pull_images(
    name="python3-d11",
    repository="gcr.io/distroless/python3-debian11",
    variants={"latest": "42ff8b00a03517f39a968d2e2a6e82c6445586c95e484ff079cbf06f7590cfa7"},
    architecture="x86_64",
    os="linux",
)

pex_binary(name="example", entry_point="example.py:main", shebang="#!/usr/bin/python")

python_sources(name="examples")
oci_image_build(
    name="oci",
    base=[":python3-d11#latest"],
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
