"""The net worth in some years (docs/phases/phase-9.md §7.5, D247 amended). Fixture:
tests/fixtures/phase9/net_worth_projection.yaml."""

from hausbuch.schemas.plan import NetWorthProjection


def project_net_worth(*args: object, **kwargs: object) -> NetWorthProjection:
    """From the stored run at the newest closed month-end, on the household's typed rates;
    the app proposes none."""
    raise NotImplementedError("phase 9 step 2")
