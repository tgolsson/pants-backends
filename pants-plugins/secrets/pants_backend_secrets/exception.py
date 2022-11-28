"""
Exceptions related to secret management.
"""


class NoDecrypterException(Exception):
    pass


class FailedDecryptException(Exception):
    pass
