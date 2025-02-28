""" """

from pants.engine.target import DictStringToStringField, SpecialCasedDependencies, softwrap


class SecretsField(DictStringToStringField):  # SECRET_NAME: //secret/rule:name
    alias = "secrets"
    help = softwrap("For when a target needs multiple secrets")


class SingleSecretField(SpecialCasedDependencies):  # //secret/rule:name
    alias = "secret"
    help = softwrap("Secret to be used by the target")
