from pants_backend_oci.goals import lint, package, package_build_step, publish, run


def rules():
    return [
        *lint.rules(),
        *publish.rules(),
        *package.rules(),
        *package_build_step.rules(),
        *run.rules(),
    ]
