python_sources(
    resolve="example-1",
    interpreter_constraints=[">=3.9,<3.10"],
)


pex_binary(
    name="binary-deps",
    entry_point="app.py",
    include_sources=False,
    resolve="example-1",
    layout="packed",
    interpreter_constraints=[">=3.9,<3.10"],
)

oci_layer(
    name="app-deps",
    packages=[":binary-deps"],
)

pex_binary(
    name="binary-srcs",
    entry_point="app.py",
    include_requirements=False,
    resolve="example-1",
    layout="packed",
    interpreter_constraints=[">=3.9,<3.10"],
)

oci_layer(
    name="app-srcs",
    packages=[":binary-srcs"],
)

oci_image_build(
    name="img",
    base=["//examples/oci:python3-d11#latest"],
    layers=[":app-deps", ":app-srcs"],
    entrypoint="python3",
    args=["/examples.oci.layers/binary-srcs.pex"],
    env=[
        "PEX_PATH=/examples.oci.layers/binary-srcs.pex:/examples.oci.layers/binary-deps.pex",
    ]
)
