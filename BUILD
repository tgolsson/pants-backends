resources(name="readme", sources=["README.md"])

files(name="files", sources=["BUILD_ROOT", "pants.toml"])

shell_sources(
    name="root",
)

TOOLS = {
    "pytest": [
        "pytest-cov>=5,<7",
        "pytest-xdist>=3.6.1,<4",
        "pytest>=7,<9,!=7.1.0,!=7.1.1",
    ],
    "black": ["black>=22.6.0,<24"],
    "ipython": ["ipython>=7.34,<9"],
    "isort": ["isort[pyproject,colors]>=5.9.3,<6.0"],
    "twine": ["twine>=5"],
}

for tool, reqs in TOOLS.items():
    python_requirement(
        name=tool,
        requirements=reqs,
        resolve=tool,
    )
