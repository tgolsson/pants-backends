resources(
    name="package_data", sources=["pyproject.toml", "README.md", "LICENSE-MIT.txt", "CHANGELOG.md"]
)

python_distribution_with_secret(
    name="package",
    dependencies=[
        ":package_data",
        "//pants-plugins/cmake/pants_backend_cmake:pants_backend_cmake",
    ],
    provides=python_artifact(
        name="pants_backend_cmake",
        version="0.1.0",
        long_description_content_type="markdown",
    ),
    long_description_path="pants-plugins/cmake/README.md",
    repo_secrets={"@pypi": "//examples/bitwarden:pypi_token"},
)
