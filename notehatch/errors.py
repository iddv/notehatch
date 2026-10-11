EXIT_OK = 0
EXIT_WARN = 1
EXIT_USAGE = 2
EXIT_SOURCE = 3
EXIT_SCHEMA = 4
EXIT_OUTPUT = 5
EXIT_INTERRUPT = 130


class NHError(Exception):
    """A user-facing failure with the exit code to return."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message
