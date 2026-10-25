python_sources()

python_tests(
    name="tests",
    # Podman keeps the store's runtime state in the user's runtime directory; `run_pants` passes
    # only what HERMETIC_ENV lists.
    overrides={"scripted_integration_test.py": {"extra_env_vars": ["XDG_RUNTIME_DIR", "HERMETIC_ENV=XDG_RUNTIME_DIR"]}},
)

pex_binary(
    name="podman_runner",
    entry_point="podman_runner.py",
)
