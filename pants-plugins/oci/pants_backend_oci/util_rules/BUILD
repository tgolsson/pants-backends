python_sources()

python_tests(
    name="tests",
    # OCI_TEST_DOCKER=1 also tests the Docker runner.
    overrides={"scripted_integration_test.py": {"extra_env_vars": ["OCI_TEST_DOCKER"]}},
)

pex_binary(
    name="podman_runner",
    entry_point="podman_runner.py",
)

pex_binary(
    name="docker_runner",
    entry_point="docker_runner.py",
)
