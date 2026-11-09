"""AfA on movable assets (phase 3 D80, the Einbauküche). Pure: AfaFacts in, lines out.

A fitted kitchen is not part of the building but a movable asset with its own AfA (BFH IX R
14/15), linear over its useful life (§7 Abs. 1 S. 1 EStG; 10 years for a kitchen). It is on
the Anlage V's own block, Z42–45 (Kz 60), not on Z33–35 (wk.afa_movable).

- Basis: bought with the flat (in_purchase_price), the input's amount is its share of the
  purchase price, and it takes its share of the Nebenkosten with it: amount × (price + Σ cost
  rows) ÷ price, to the cent. That basis leaves the building's AfA basis (afa.basis), never the
  land's. Bought separately, the basis is the amount and the building's basis is unchanged.
- AfA: basis × months ÷ (useful_life_years × 12) per year, to the cent, from from_year on
  (§7 Abs. 1 S. 4 EStG: pro rata by month in the first year, by owned_months, which counts
  from the handover month in the purchase year, else from January); the year in which the
  life's remaining months run out takes the rest of the book value, so the years sum exactly
  to the basis; 0.00 after it, and after the ownership ends.
- Z42's method: 2 (laut Erläuterung) in the first year, 1 (wie Vorjahr) after it; None in a
  year without owned months.
- Sources: the tax input, its document, the property. Without a document: info warning
  afa.movable_asset_no_evidence (the value is an estimate).
"""

from decimal import Decimal

from hausbuch.schemas.tax import AfaFacts, MovableAfaLine, MovableAssetFact, SourceRef, WarningCode

from ._common import ZERO, owned_months, warning
from .rounding import cents, money_str


def current_assets(facts: AfaFacts) -> list[MovableAssetFact]:
    """One asset per label: the later in the list wins (the service keeps one per label)."""
    by_label: dict[str, MovableAssetFact] = {}
    for asset in facts.movable_assets:
        by_label[asset.label] = asset
    return list(by_label.values())


def asset_basis(facts: AfaFacts, asset: MovableAssetFact) -> Decimal:
    """The asset's AfA basis, to the cent: with its share of the Nebenkosten when it was
    bought with the flat (amount × (price + Σ cost rows) ÷ price), else its amount."""
    if not asset.in_purchase_price:
        return cents(asset.amount)
    total = facts.purchase_price + sum((r.amount for r in facts.cost_rows), ZERO)
    return cents(asset.amount * total / facts.purchase_price)


def _sources(facts: AfaFacts, asset: MovableAssetFact) -> list[SourceRef]:
    refs = [SourceRef(kind="tax_input", id=asset.tax_input_id)]
    if asset.document_id is not None:
        refs.append(SourceRef(kind="document", id=asset.document_id))
    refs.append(SourceRef(kind="property", id=facts.property_id))
    return refs


def asset_afa(facts: AfaFacts, asset: MovableAssetFact, tax_year: int) -> MovableAfaLine:
    """The asset's line for `tax_year` (from_year ≤ tax_year), walking the years from
    from_year."""
    if tax_year < asset.from_year:
        raise ValueError("a movable asset has no AfA before its first year")
    base = asset_basis(facts, asset)
    total = asset.useful_life_years * 12
    book, remaining = base, total
    for year in range(asset.from_year, tax_year + 1):
        months = owned_months(facts.purchase_date, facts.ownership_ended_on, year)
        start_book, start_remaining = book, remaining
        if months == 0:
            amount = cents(ZERO)
        elif months >= remaining:  # the last months take the rest (0.00 once written off)
            amount = cents(book)
        else:
            amount = min(cents(base * months / total), book)
        remaining = max(remaining - months, 0)
        book -= amount
    warnings = []
    if asset.document_id is None and amount:
        warnings.append(
            warning(
                WarningCode.AFA_MOVABLE_ASSET_NO_EVIDENCE,
                [SourceRef(kind="tax_input", id=asset.tax_input_id)],
                label=asset.label,
                amount=money_str(asset.amount),
            )
        )
    method_code = None if not months else (2 if tax_year == asset.from_year else 1)
    return MovableAfaLine(
        tax_input_id=asset.tax_input_id,
        label=asset.label,
        tax_year=tax_year,
        amount=amount,
        price_amount=asset.amount,
        basis=base,
        in_purchase_price=asset.in_purchase_price,
        useful_life_years=asset.useful_life_years,
        from_year=asset.from_year,
        months=months,
        book_value_start=start_book,
        remaining_months=start_remaining,
        method_code=method_code,
        sources=_sources(facts, asset),
        warnings=warnings,
    )


def movable_afa(facts: AfaFacts, tax_year: int) -> list[MovableAfaLine]:
    """One line per movable asset in force whose first year is at or before `tax_year`."""
    return [
        asset_afa(facts, asset, tax_year)
        for asset in current_assets(facts)
        if asset.from_year <= tax_year
    ]
