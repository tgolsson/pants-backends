file(name="files", source="test.txt")

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

oci_image_build(
    name="with_empty_base",
    base=["//:empty"],
    packages=[
        ":example2",
    ],
    tag="latest",
)

oci_structure_test(
    name="structure_test",
    base=["//:empty"],
    expected_digest="e0039d9a394788147eb854e5efe5429723352c288faa47de3bb1abbd53a7f7bb",
)

oci_structure_test(
    name="structure_test_2",
    base=[":with_empty_base"],
    expected_digest="325c5377ca16b7a5ae737e2bb581f618540e967b1d0af4a410410bbf57b4834a",
)

oci_image_build(
    name="with_file",
    base=["//:empty"],
    packages=[
        ":example2",
        ":files",
    ],
    tag="latest",
)


oci_structure_test(
    name="structure_test_3",
    base=[":with_file"],
    expected_digest="c55e7c3fd6257e99aa041268974b4b6ecbd3d54bdda2254e858a2e6a7890b8c",
)
