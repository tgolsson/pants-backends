"""
Exceptions related to secret management.
"""


class MissingSecret(Exception):
    pass


class NoDecrypterException(Exception):
    pass


class FailedDecryptException(Exception):
    pass
