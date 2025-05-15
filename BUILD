resources(name="readme", sources=["README.md"])

files(name="files", sources=["BUILD_ROOT", "pants.toml"])

shell_sources(
    name="root",
)

TOOLS = {
    "pytest": [
        "pytest-cov!=2.12.1,<3.1,>=2.12",
        "pytest-xdist<3,>=2.5",
        "pytest==7.0.*",
    ],
    "black": ["black>=22.6.0,<24"],
    "ipython": ["ipython>=7.27.0,<8"],
    "isort": ["isort[pyproject,colors]>=5.9.3,<6.0"],
    "twine": ["twine>=5"]
}

for tool, reqs in TOOLS.items():
    python_requirement(
        name=tool,
        requirements=reqs,
        resolve=tool,
    )
