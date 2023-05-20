from pants_backend_oci.goals import package, package_build_step, publish, run


def rules():
    return [
        *publish.rules(),
        *package.rules(),
        *package_build_step.rules(),
        *run.rules(),
    ]
