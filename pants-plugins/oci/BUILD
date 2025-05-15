resources(name="package_data", sources=["pyproject.toml", "README.md", "LICENSE-MIT.txt", "CHANGELOG.md"])

python_distribution_with_secret(
    name="package",
    dependencies=[
        ":package_data",
        "./pants_backend_oci:pants_backend_oci@resolve=pants-plugins",
        "./pants_backend_oci/utility/mirror:mirror@resolve=pants-plugins",
    ],
    provides=python_artifact(
        name="pants_backend_oci",
        version="0.8.0",
        long_description_content_type="markdown",
    ),
    long_description_path="pants-plugins/oci/README.md",
    wheel_config_settings={"--global-option": ["--python-tag", "py38.py39"]},
    repo_secrets={"@pypi": "//examples/bitwarden:pypi_token"},
)

__defaults__(
    {
        python_tests: dict(
            dependencies=[
                "//BUILD_ROOT:files",
                "//pants-plugins/oci/pants_backend_oci/register.py",
            ],
        ),
    },
    extend=True,
)
