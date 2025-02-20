""" """

from pants.engine.target import COMMON_TARGET_FIELDS, StringField, Target


class EnvironmentSecretKey(StringField):
    alias = "key"

    help = "The name/key for a secret in the host environment"


class EnvironmentSecret(Target):
    alias = "env_secret"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        EnvironmentSecretKey,
    )

    help = "A declaration for an environment key that can be read as a secret."


def targets():
    return [
        EnvironmentSecret,
    ]
