python_sources(resolve="example-1")

python_requirements(
    name="reqs",
    module_mapping={
        "tensorflow-cpu": ["tensorflow"],
    },
    resolve="example-1",
)

pex_binary(
    name="binary-deps",
    entry_point="app.py",
    layout="packed",
    include_sources=False,
    include_tools=True,
    resolve="example-1",
    interpreter_constraints=[">=3.8,<3.11"],
)

oci_build_layer(
    name="app-deps",
    base=["//examples/oci:python3-d11#latest"],
    packages=[":binary-deps"],
    commands="/examples.oci.multistage/binary-deps.pex venv --scope=deps --compile /bin/app",
    env=[
        "PEX_TOOLS=1",
        "PY=python3.9",
    ],
    outputs=["bin/app/"],
)

pex_binary(
    name="binary-srcs",
    entry_point="app.py",
    layout="packed",
    include_requirements=False,
    include_tools=True,
    resolve="example-1",
    interpreter_constraints=[">=3.8,<3.11"],
)

oci_build_layer(
    name="app-srcs",
    base=["//examples/oci:python3-d11#latest"],
    packages=[":binary-srcs"],
    commands="/examples.oci.multistage/binary-srcs.pex venv --scope=srcs --compile /bin/app",
    env=[
        "PEX_TOOLS=1",
        "PY=python3.9",
    ],
    outputs=["bin/app/"],
)

oci_image_build(
    name="img",
    base=["//examples/oci:python3-d11#latest"],
    packages=[":app-deps", ":app-srcs"],
    entrypoint="/bin/app/pex",
    args=[],
)
