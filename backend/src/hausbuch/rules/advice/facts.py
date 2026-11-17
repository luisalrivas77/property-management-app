"""The request's facts (docs/phases/phase-9.md §8.1, D252–D254). Fixture:
tests/fixtures/phase9/explain_facts.yaml."""

from hausbuch.schemas.advice import ExplainFacts


def build(*args: object, **kwargs: object) -> ExplainFacts:
    """Figures, states, kinds and classes under opaque keys; the labels apart, never sent."""
    raise NotImplementedError("phase 9 step 4")
