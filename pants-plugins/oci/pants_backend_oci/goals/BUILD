python_sources()

python_tests(
    name="tests",
    # Podman keeps the store's runtime state in the user's runtime directory; `run_pants` passes
    # only what HERMETIC_ENV lists. OCI_TEST_DOCKER=1 also tests the Docker runner, and
    # OCI_TEST_PODMAN_REMOTE=<connection> remote podman.
    overrides={
        "run_integration_test.py": {
            "extra_env_vars": [
                "XDG_RUNTIME_DIR",
                "HERMETIC_ENV=XDG_RUNTIME_DIR",
                "OCI_TEST_DOCKER",
                "OCI_TEST_PODMAN_REMOTE",
            ]
        }
    },
)
