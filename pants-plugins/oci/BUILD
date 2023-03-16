resources(
    name="package_data", sources=["pyproject.toml", "README.md", "LICENSE-MIT.txt", "CHANGELOG.md"]
)

python_distribution_with_secret(
    name="package",
    dependencies=[
        ":package_data",
        "./pants_backend_oci:pants_backend_oci@resolve=pants-plugins",
    ],
    provides=python_artifact(
        name="pants_backend_oci",
        version="0.3.0",
        long_description_content_type="markdown",
    ),
    long_description_path="pants-plugins/oci/README.md",
    wheel_config_settings={"--global-option": ["--python-tag", "py38.py39"]},
    repositories=["@pypi"],
    repo_secrets={"@pypi": "//examples/bitwarden:pypi_token"},
)
