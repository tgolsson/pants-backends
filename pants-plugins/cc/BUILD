resources(
    name="package_data", sources=["pyproject.toml", "README.md", "LICENSE-MIT.txt", "CHANGELOG.md"]
)

python_distribution_with_secret(
    name="package",
    dependencies=[
        ":package_data",
        "//pants-plugins/cc/pants_backend_cc:pants_backend_cc",
        "//pants-plugins/cc/pants_backend_cc/util_rules:util_rules",
        "//pants-plugins/cc/pants_backend_cc/generate:generate",
        "//pants-plugins/cc/pants_backend_cc/goals:goals",
        "//pants-plugins/cc/pants_backend_cc/lint:lint",
        "//pants-plugins/cc/pants_backend_cc/lint/clang_tidy:clang_tidy",
        "//pants-plugins/cc/pants_backend_cc/lint/compile_cost:compile_cost",
    ],
    provides=python_artifact(
        name="pants_backend_cc",
        version="0.1.0",
        long_description_content_type="markdown",
    ),
    long_description_path="pants-plugins/cc/README.md",
    repo_secrets={"@pypi": "//examples/bitwarden:pypi_token"},
)
