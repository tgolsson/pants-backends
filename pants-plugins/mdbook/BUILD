resources(
    name="package_data", sources=["pyproject.toml", "README.md", "LICENSE-MIT.txt", "CHANGELOG.md"]
)

python_distribution_with_secret(
    name="package",
    dependencies=[
        ":package_data",
        "//pants-plugins/mdbook/pants_backend_mdbook:pants_backend_mdbook@resolve=pants-plugins",
    ],
    provides=python_artifact(
        name="pants_backend_mdbook",
        version="0.6.0",
        long_description_content_type="markdown",
    ),
    long_description_path="pants-plugins/mdbook/README.md",
    repo_secrets={"@pypi": "//examples/bitwarden:pypi_token"},
)
