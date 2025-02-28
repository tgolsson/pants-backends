""" """

from pants_backend_oci.language_target import python


def targets():
    return [
        python.PythonImageBuild,
    ]


def rules():
    return [
        *python.rules(),
    ]
