"""Docker as the OCI runner, for `[oci].runner = "docker"`. Needs `pants.backend.docker`."""

from pants_backend_oci.docker import rules as docker_rules


def rules():
    return docker_rules.rules()
