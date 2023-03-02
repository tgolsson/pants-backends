python_requirement(name="reqs", requirements=["tensorflow"], resolve="example-1")
python_sources(resolve="example-1")

pex_binary(
    name="binary-deps",
    entry_point="multistage.app:run",
    layout="packed",
    include_sources=False,
    include_tools=True,
)

oci_build_artifact(
    name="app-deps",
    base=["//examples/oci:python3-d11#latest"],
    packages=[":binary-deps"],
    commands=(
        "/examples.oci.multistage/binary-deps.pex venv --scope=deps --compile /bin/app"
    ),
    env=[
        "PEX_TOOLS=1",
    ],
    outputs=["bin/app/"],
)

pex_binary(
    name="binary-srcs",
    entry_point="multistage.app:run",
    layout="packed",
    include_requirements=False,
    include_tools=True,
)

oci_build_artifact(
    name="app-srcs",
    base=["//examples/oci:python3-d11#latest"],
    packages=[":binary-srcs"],
    commands=(
        "/examples.oci.multistage/binary-srcs.pex venv --scope=srcs --compile /bin/app"
    ),
    env=[
        "PEX_TOOLS=1",
    ],
    outputs=["bin/app/"],
)

oci_image_build(
    name="img",
    base=["//examples/oci:python3-d11#latest"],
    packages=[":app-deps", ":app-srcs"],
    entrypoint="/bin/app/pex",
    args=[]
)
