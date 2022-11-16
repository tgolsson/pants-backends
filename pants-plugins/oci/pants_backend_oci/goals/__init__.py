from pants_backend_oci.goals import package, publish, run


def rules():
    return [
        *publish.rules(),
        *package.rules(),
        *run.rules(),
    ]
