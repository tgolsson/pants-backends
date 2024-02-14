python_sources(
    resolve="example-1",
    interpreter_constraints=[">=3.9,<3.10"],
)

pex_binary(
    name="app",
    entry_point="app.py",
    resolve="example-1",
    interpreter_constraints=[">=3.9,<3.10"],
    layout="loose",
    execution_mode="venv",
)

oci_layer(
    name="layer",
    packages=[":app"],
)

oci_image_build(
    name="img",
    base=["//examples/oci:python3-d11#latest"],
    layers=[ ":layer"],
    entrypoint="python3",
    args=["/examples.oci.layers/app.pex"],
)
