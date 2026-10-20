"""Stable errors for Praetoria's CLI and library."""


class PraetoriaError(Exception):
    code = "PRAETORIA_E_UNKNOWN"
    exit_code = 1

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class UsageError(PraetoriaError):
    code = "PRAETORIA_E_USAGE"
    exit_code = 2


class TransportError(PraetoriaError):
    code = "PRAETORIA_E_TRANSPORT"
    exit_code = 3


class UpstreamError(PraetoriaError):
    code = "PRAETORIA_E_UPSTREAM"
    exit_code = 4


class SchemaError(PraetoriaError):
    code = "PRAETORIA_E_SCHEMA"
    exit_code = 5


class CacheError(PraetoriaError):
    code = "PRAETORIA_E_CACHE"
    exit_code = 6


class OutputError(PraetoriaError):
    code = "PRAETORIA_E_OUTPUT"
    exit_code = 7

