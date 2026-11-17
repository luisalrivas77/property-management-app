"""The marks on the key figures (docs/phases/phase-9.md §5.6, D250). Data: bands.yaml beside
this module; fixture: tests/fixtures/phase9/lights.yaml.

Step 0 writes the loader of the proposals; `light` and `lights` are step 1's.
"""

from functools import cache
from importlib.resources import files

import yaml

from hausbuch.schemas.health import BandProposal, Light, MarkedFigure


@cache
def proposals() -> tuple[str, dict[MarkedFigure, BandProposal], dict[str, str]]:
    """bands.yaml: its version, the proposal per figure and the sentences by code."""
    data = yaml.safe_load((files("hausbuch.rules.health") / "bands.yaml").read_text("utf-8"))
    by_figure = {}
    for row in data["figures"]:
        proposal = BandProposal.model_validate(row)
        if proposal.figure in by_figure:
            raise ValueError(f"bands.yaml: {proposal.figure} twice")
        by_figure[proposal.figure] = proposal
    sentences = dict(data["sentences"])
    for proposal in by_figure.values():
        for codes in proposal.sentences.values():
            missing = [c for c in codes if c not in sentences]
            if missing:
                raise ValueError(f"bands.yaml: unknown sentence codes {missing}")
    return data["version"], by_figure, sentences


def light(*args: object, **kwargs: object) -> Light:
    """One figure's colour against its confirmed limits, at stored precision."""
    raise NotImplementedError("phase 9 step 1")


def lights(*args: object, **kwargs: object) -> dict[MarkedFigure, Light]:
    """Every figure's mark; nothing coloured while no limit is confirmed."""
    raise NotImplementedError("phase 9 step 1")
