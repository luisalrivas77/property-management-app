"""The one exception type rules raise. Its code is a stable API contract."""


class RuleError(Exception):
    """A rule could not produce a result for valid-looking input.

    code: stable error code, e.g. "iban.bad_checksum" (docs/phases/phase-1.md).
    at_number: for loan schedule errors, the 1-based payment number where it fails.
    """

    def __init__(self, code: str, message: str | None = None, *, at_number: int | None = None):
        super().__init__(message or code)
        self.code = code
        self.at_number = at_number
