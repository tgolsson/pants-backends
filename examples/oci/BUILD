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

file(
    name="grpc_health_probe",
    source=http_source(
        "https://github.com/grpc-ecosystem/grpc-health-probe/releases/download/v0.4.16/grpc_health_probe-linux-amd64",
        len=11019805,
        sha256="c72704c9cd49fb18f8df28de26e29c2563b6515e38efee65a6c3a29ec7368a91",
        filename="grpc_health_probe",
    )
)

oci_image_build(
    name="oci2",
    base=[":oci#latest"],
    packages=[
        ":example2",
        ":grpc_health_probe",
    ],
    repository="harbor.sbg.dev/test/pants-plugin-oci-example-2",
    tag="latest",
)
