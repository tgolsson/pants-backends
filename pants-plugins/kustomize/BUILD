resources(name="package_data", sources=["pyproject.toml", "README.md", "LICENSE-MIT.txt"])

python_distribution(
    name="package",
    dependencies=[
        ":package_data",
        "./pants_backend_kustomize:pants_backend_kustomize",
    ],
    provides=python_artifact(
        name="pants_backend_kustomize",
        version="0.1.0",
        long_description_content_type="markdown",
    ),
    long_description_path="pants-plugins/kustomize/README.md",
    wheel_config_settings={"--global-option": ["--python-tag", "py38.py39"]},
    repositories=["@pypi"],
)
