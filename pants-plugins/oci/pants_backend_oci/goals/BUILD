python_sources()

python_tests(
    name="tests",
    # OCI_TEST_DOCKER=1 also tests the Docker runner, and
    # OCI_TEST_PODMAN_REMOTE=<connection> remote podman.
    overrides={
        "run_integration_test.py": {
            "extra_env_vars": [
                "OCI_TEST_DOCKER",
                "OCI_TEST_PODMAN_REMOTE",
            ]
        }
    },
)
