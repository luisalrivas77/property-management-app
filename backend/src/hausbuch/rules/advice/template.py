"""The app's own explanation from the same facts (docs/phases/phase-9.md §8.5, D257).
Fixture: the `template_*` cases of tests/fixtures/phase9/explain_render.yaml."""

from hausbuch.schemas.advice import Paragraph


def render(*args: object, **kwargs: object) -> list[Paragraph]:
    """The template sentences for the facts' states, shown as „Standardtext“."""
    raise NotImplementedError("phase 9 step 4")
