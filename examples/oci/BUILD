oci_pull_images(
    name="python3-d13",
    repository="gcr.io/distroless/python3-debian13",
    variants={"latest": "4758cbf640c7a26440b06e4a965625e1bd5929d5384038d2c957b90f37498434"},
    architecture="amd64",
    os="linux",
)

# The pex binaries run on the distroless base, so they use its Python (Debian 13's 3.13).
pex_binary(
    name="example",
    entry_point="example.py:main",
    shebang="#!/usr/bin/python",
    resolve="example-1",
    interpreter_constraints=["==3.13.*"],
)

python_sources(name="examples", resolve="example-1", interpreter_constraints=["==3.13.*"])

# Golden checks on the CA bundles and locales built from the locked Debian and Ubuntu
# `ca-certificates` and `libc-bin` debs.
python_tests(
    name="tests",
    runtime_package_dependencies=[
        "examples/oci/debian:cacerts",
        "examples/oci/ubuntu:cacerts",
        "examples/oci/debian:locales",
        "examples/oci/ubuntu:locales",
    ],
)

oci_image_build(
    name="oci",
    base=[":python3-d13#latest"],
    packages=[
        ":example",
    ],
    repository="harbor.sbg.dev/test/pants-plugin-oci-example",
    tag="latest",
)

pex_binary(
    name="example2",
    entry_point="example2.py:main",
    shebang="#!/usr/bin/python",
    resolve="example-1",
    interpreter_constraints=["==3.13.*"],
)

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
