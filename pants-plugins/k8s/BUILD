resources(
    name="package_data", sources=["pyproject.toml", "README.md", "LICENSE-MIT.txt", "CHANGELOG.md"]
)

python_distribution_with_secret(
    name="package",
    dependencies=[
        ":package_data",
        "./pants_backend_k8s:pants_backend_k8s@resolve=pants-plugins",
    ],
    provides=python_artifact(
        name="pants_backend_k8s",
        version="0.5.0",
        long_description_content_type="markdown",
    ),
    long_description_path="pants-plugins/k8s/README.md",
    wheel_config_settings={"--global-option": ["--python-tag", "py39"]},
    repo_secrets={"@pypi": "//examples/bitwarden:pypi_token"},
)
