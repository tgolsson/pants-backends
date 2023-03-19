oci_pull_images(
    name="python3-d11",
    repository="gcr.io/distroless/python3-debian11",
    variants={"latest": "42ff8b00a03517f39a968d2e2a6e82c6445586c95e484ff079cbf06f7590cfa7"},
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

# in pants 2.15+ we use a synthetic target so no need to declare an empty image base
empty_base = "//:empty"  # also see [oci].empty_image_target configuration

if not pants_at_least("2.15.0.dev0"):
    empty_base = ":empty"
    oci_image_empty(
        name="empty",
    )

oci_image_build(
    name="with_empty_base",
    base=[empty_base],
    packages=[
        ":example2",
    ],
    tag="latest",
)
