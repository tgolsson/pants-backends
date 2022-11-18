resources(name="package_data", sources=["pyproject.toml", "README.md", "LICENSE-MIT.txt"])

python_distribution(
    name="package",
    dependencies=[
        ":package_data",
        "//pants-plugins/mdbook/pants_backend_mdbook:pants_backend_mdbook",
    ],
    provides=python_artifact(
        name="pants_backend_mdbook",
        version="0.1.3",
        long_description_content_type="markdown",
    ),
    long_description_path="pants-plugins/mdbook/README.md",
    wheel_config_settings={"--global-option": ["--python-tag", "py38.py39"]},
    repositories=["@pypi"],
)
