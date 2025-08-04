resources(
    name="package_data", sources=["pyproject.toml", "README.md", "LICENSE-MIT.txt", "CHANGELOG.md"]
)

python_distribution_with_secret(
    name="package",
    dependencies=[
        ":package_data",
        "./pants_backend_odin:sources@resolve=pants-plugins",
    ],
    provides=python_artifact(
        name="pants_backend_odin",
        version="0.1.0",
        long_description_content_type="markdown",
    ),
    long_description_path="pants-plugins/odin/README.md",
    wheel_config_settings={"--global-option": ["--python-tag", "py38.py39"]},
    repo_secrets={"@pypi": "//examples/bitwarden:pypi_token"},
)