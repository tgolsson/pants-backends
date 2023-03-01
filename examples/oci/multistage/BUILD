python_requirement(name="example-resolve", requirements=["tensorflow"], resolve="example-1")
python_sources(resolve="example-1")

pex_binary(
    name="binary-deps",
    entry_point="app.py",
    layout="packed",
    include_sources=False,
    include_tools=True,
)

oci_build_artifact(
    name="app-deps",
    base=["//examples/oci:python3-d11#latest"],
    packages=[":binary-deps"],
    commands=        "RUN PEX_TOOLS=1 /usr/local/bin/python3.10 /binary-deps.pex venv --scope=deps --compile /bin/app"
,
    outputs=["/bin/app/"],
)

pex_binary(
    name="binary-srcs",
    entry_point="app.py",
    layout="packed",
    include_requirements=False,
    include_tools=True,
)

oci_build_artifact(
    name="app-srcs",
    base=["//examples/oci:python3-d11#latest"],
    packages=[":binary-srcs"],
    commands=       "RUN PEX_TOOLS=1 /usr/local/bin/python3.10 /binary-srcs.pex venv --scope=srcs --compile /bin/app"
    ,
    outputs=["/bin/app/"],
)

oci_python_image(
    name="img",
    base=["//examples/oci:python3-d11#latest"],
    packages=[":app-deps", ":app-srcs"],
)
