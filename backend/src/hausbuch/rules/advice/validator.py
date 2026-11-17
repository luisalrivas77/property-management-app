"""The explanation's validator (docs/phases/phase-9.md §9, D255, D256). Fixtures:
tests/fixtures/phase9/explain_validator.yaml and instrument_names.yaml."""

from hausbuch.schemas.advice import ValidationResult


def validate(*args: object, **kwargs: object) -> ValidationResult:
    """The thirteen checks over the model's output and its facts; fails closed."""
    raise NotImplementedError("phase 9 step 4")
