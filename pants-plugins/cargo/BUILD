resources(name="package_data", sources=["pyproject.toml", "README.md", "LICENSE-MIT.txt", "CHANGELOG.md"])

# The clippy sub-backend is enabled on its own, so nothing imports it: listed by hand.
python_distribution_with_secret(
    name="package",
    dependencies=[
        ":package_data",
        "//pants-plugins/cargo/pants_backend_cargo:pants_backend_cargo",
        "//pants-plugins/cargo/pants_backend_cargo/backends/clippy:clippy",
        "//pants-plugins/cargo/pants_backend_cargo/backends/clippy/goals:goals",
    ],
    provides=python_artifact(
        name="pants_backend_cargo",
        version="0.1.0",
        long_description_content_type="markdown",
    ),
    long_description_path="pants-plugins/cargo/README.md",
    repo_secrets={"@pypi": "//examples/bitwarden:pypi_token"},
)
