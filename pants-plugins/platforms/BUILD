resources(
    name="package_data", sources=["pyproject.toml", "README.md", "LICENSE-MIT.txt", "CHANGELOG.md"]
)

python_distribution_with_secret(
    name="package",
    dependencies=[
        ":package_data",
        "//pants-plugins/platforms/pants_backend_platforms:pants_backend_platforms",
    ],
    provides=python_artifact(
        name="pants_backend_platforms",
        version="0.1.0",
        long_description_content_type="markdown",
    ),
    long_description_path="pants-plugins/platforms/README.md",
    repo_secrets={"@pypi": "//examples/bitwarden:pypi_token"},
)
