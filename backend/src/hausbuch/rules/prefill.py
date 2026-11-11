"""Building a pre-fill proposal from an extraction (tests/fixtures/phase2/prefill.yaml).

build_proposal(kind, extraction, context) -> list[ProposalRecord]. Spec:
docs/phases/phase-2.md §6.2, §6.3, D14, D15. Pure: no I/O, no model. It decides per record
whether it creates, patches, corrects or leaves an existing record unchanged, computes the
derived values (building share, the Rate from the Tilgungssatz, a loan's interest-only flag,
ownership shares, the prepayment sum, rent model, the tenancy's unit, an invoice's tax year:
D36) and writes notes.
A financing invoice (Grundschuld, Grundpfandrecht, Hypothek, Darlehen) is decided here, not
by the classifier: it proposes no Nebenkosten row (D35) and the subtype financing_invoice,
which only code sets (D138). Nothing it returns reaches a
domain table: only the user's apply does, through the phase 1 services (D14).

Phase 2 D135 (every cost document into every record it implies): an invoice's or Bescheid's
cost lines, when they sum to its total (extraction_parse.derive_fields), are sorted by
sort_bill: a line naming a financing term is loan_fees, one naming a purchase term
(Auflassungsvormerkung, Eigentumsumschreibung, Eintragung des Eigentümers, Eigentümerwechsel,
Kaufvertrag) acquisition_cost; on a financing bill without a purchase line the other lines are
loan_fees too; every other line keeps the category the reading gave it. The Umsatzsteuer is
shared over the parts by their net amounts (largest remainder, to the cent). The
acquisition_cost part becomes the Nebenkosten row (create, or patch the row whose Beleg this
is, or a row of that kind without one); every deductible part (categories.TAX_TREATMENT)
becomes a tax_input:private_cost:<category> record {tax_year, value.cost_class,
value.category, value.label, value.amount} unless context.paid_from_rental_account (a
rental-account line pays the bill: the bank line's split books it). A whole financing bill
without readable lines proposes one loan_fees record of its total. A part of an undecided
category (renovation, other_expense) is named in a note, never proposed. The catalogue's
invoices (utility bill, §35a, work expenses) are not sorted.

Phase 2 D145 (Luis, 11 Nov 2026): the Finanzamt sends each buyer of a joint purchase a
Grunderwerbsteuerbescheid on that buyer's share. A Bescheid reading whose kind is
real_estate_transfer_tax and whose amount B is smaller than the Grunderwerbsteuer row A it
would patch (cost rule 1 or 2) never lowers the row: the row is patched to B with this
document as its Beleg, and a second record cost:real_estate_transfer_tax:other_notice creates
the rest A − B without a Beleg ("Grunderwerbsteuer, weiterer Bescheid"), so the total stays A
and the checklist asks for the other notice. Cost rule 2 prefers the document-less row whose
amount equals the reading's within a cent, so the other notice later fills that row.

Every value inside a record (`fields[*].value`, `current`) is JSON: dates as ISO strings,
decimals as strings, fractions as {numerator, denominator}, ids as strings. The result never
depends on the order of the context's lists (the fixture runs every case shuffled): where
several existing records match, the one with the smallest id wins.

Record keys, in this order when a kind proposes them: typing, property, correction:<field>,
unit, price_split, ownership, cost:<kind>, cost:<kind>:other_notice, loan, tenant:<n>,
tenancy, rent, deposit_received, link:property / link:loan / link:tenancy / link:owner,
tax_input:<kind>, return_input:<kind>.

Phase 3 (docs/phases/phase-3.md §9): a Zinsbescheinigung also proposes
tax_input:interest_certified {tax_year, loan_id, value.amount}, a Hausgeldabrechnung
tax_input:ruecklage_spent {tax_year, value.amount}; the apply writes them through
services/tax_inputs (D14).

Phase 4b (docs/phases/phase-4b.md §8.2, D96, D105): a Lohnsteuerbescheinigung proposes
link:owner (the employee's name against this workspace's owners, by name tokens; unmatched,
the user picks the owner) and return_input:wage_certificate {owner_id, tax_year,
value.<field>} with every WageCertificate field (a line the model did not read is proposed
empty, never 0, and named in a note); a Gehaltsabrechnung link:owner and return_input:payslip
{owner_id, tax_year, value.<field>, value.<figure>.month / .ytd} with months_remaining_paid
= 12 − month and the gross projection in a note. return_input_value turns the confirmed
values into the row's value: an unread certificate line becomes 0 and is listed in `unread`
(Q-O29). The apply writes them through services/return_inputs (D14).

Phase 4b D129 (§8.4): an Einkommensteuerbescheid proposes income_input:bescheid_zve
{tax_year, value.amount ← taxable_income}; the apply writes it through
services/income_inputs with this document as the Bescheid and supersedes the year's current
row. Notes say which row it replaces ("ersetzt den Bescheid vom …"), that the document is
already recorded under another year (the apply refuses it), and warn, never blocking, when
the assessment is not a Zusammenveranlagung or the addressee names neither owner. The
addressee's names stay in the extraction: no note quotes them.

Phase 2 D130, D131 (§6.2.1): the general reading and a Verwaltervertrag propose typing and
at most one link, never a domain record. The link follows the subtype's group
(schemas.extraction.GENERAL_LINK): property (by the address read, else the only property),
loan (by lender and contract number, else the only loan) or tenancy (the one tenancy on the
property found by address whose term holds the document date; none or several, no link),
except a tenant's Selbstauskunft and a Betriebskostenabrechnung, which link the property by
address and ask for the tenancy by hand. An address read that is none of the workspace's
properties (the parser keeps its source, not its value) links nothing.
Without a match the typing record carries a note naming what was not found. The catalogue's
invoices (utility bill, household services, work expenses) are read as invoices but propose
no Nebenkosten row: typing with the tax year from the invoice date, and the property link for
a utility bill.

Phase 2 D147: a Gutachten (appraisal) is in the property group, and with its property found and
a remaining useful life read it proposes tax_input:useful_life {tax_year, value.years}, both
derived by code from [remaining_useful_life, valuation_date] (_useful_life): the life counts
from the handover, a Stichtag before it takes off the whole months between them, rounded up to
whole years; a Stichtag in a later year than the handover starts the life in its own year. A
life not shorter than the statutory one left (the check of services.tax_inputs, D79), or none
left, proposes no record and says why on the typing record.

Phase 2 D132 (one pre-filled form, Luis 10 Nov 2026): the typing record also carries the
document's hand-entry fields (rules.hand_fields: amount_total, period_start, period_end,
counterparty, reference, notes, as the kind maps them) under their hand-field names, each
with the reading's value (cited, or derived by code), proposed empty when the reading holds
none, so the review asks for it there. A hand field whose reading field another record of
the proposal already asks for (a loan contract's lender, an invoice's amount in its
Nebenkosten row) is left out, so no value is asked twice; the typing record's hand_from
names that record's field, and the apply writes the value the user applied there into the
document's reading as well. The apply writes them as the document's hand fields
(services.documents.hand_fields_into).
"""

import re
from collections.abc import Callable, Iterable, Sequence
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import JsonValue

from hausbuch.schemas.ai import ConfidenceLevel, ExtractedValue
from hausbuch.schemas.allocation import AllocationCategory, TaxTreatment
from hausbuch.schemas.common import Fraction
from hausbuch.schemas.employment import CERTIFICATE_AMOUNTS, PAYSLIP_FIGURES
from hausbuch.schemas.employment import employer_key as _shared_employer_key
from hausbuch.schemas.enums import (
    AcquisitionCostKind,
    DepositForm,
    DocumentSubtype,
    ExtractionKind,
    PropertyKind,
    RentModel,
)
from hausbuch.schemas.extraction import GENERAL_LINK, _Typing
from hausbuch.schemas.proposal import (
    CarriedField,
    ExistingCost,
    ExistingLoan,
    ExistingOwner,
    ExistingPayRow,
    ExistingProperty,
    ExistingUnit,
    FieldOption,
    PrefillContext,
    ProposalRecord,
)
from hausbuch.schemas.tax import GRUNDSTEUER_SUM_TOLERANCE, LIMIT_15_CATEGORIES

from .categories import CATEGORY_LABEL, TAX_TREATMENT
from .extraction_parse import _MONTHS, Dwelling, cost_lines_balance
from .extraction_parse import cost_lines as parse_cost_lines
from .extraction_parse import dwellings as parse_dwellings
from .extraction_parse import house_number_key as _house_number_key
from .extraction_parse import lone_dwelling as _lone_dwelling
from .extraction_parse import street_key as _street_key
from .hand_fields import HAND_FIELD_MAP, HAND_FIELDS, hand_values
from .tax import registry as tax_registry
from .tax.afa import statutory_months_left
from .tax.rounding import split_cents

_CONFIDENCE_ORDER: dict[ConfidenceLevel, int] = {"low": 0, "medium": 1, "high": 2}
_SIX_PLACES = Decimal("0.000001")
_CENTS = Decimal("0.01")
# The property facts a Kaufvertrag states, by record field and extraction field.
_PROPERTY_FIELDS = (
    ("kind", "property_kind"),
    ("address.street", "street"),
    ("address.house_number", "house_number"),
    ("address.postal_code", "postal_code"),
    ("address.city", "city"),
    ("build_year", "build_year"),
    ("notarized_on", "notarized_on"),
    ("purchase_date", "purchase_date"),
    ("purchase_price", "purchase_price"),
)
# Milestone dates (D7): never patched, corrected through correct_* (§6.5).
_MILESTONES = ("notarized_on", "purchase_date")
# A new loan's fields that are proposed only when the contract states them.
_LOAN_OPTIONAL = ("contract_number", "fixed_until", "follow_up_rate", "account_iban", "signed_on")
# A loan contract's clause that only interest is paid until repayment starts (Luis's Sparkasse
# contract, 8 Nov 2026: "Bis zum Tilgungsbeginn sind nur die Sollzinsen zu diesen Terminen zu
# zahlen"; also "nur Zinsen", "nur Sollzinsen"), searched in the casefolded clause. A clause
# without "nur" before the interest ("Die Zinsen sind ... zu zahlen") does not tick the box.
_INTEREST_ONLY = re.compile(r"\bnur (?:die )?(?:soll)?zinsen\b")
# A handover protocol is linked to the tenancy whose move-in is this close to its date.
_HANDOVER_WINDOW_DAYS = 31
# An invoice whose subject or title names one of these is a financing cost (D35). Deliberately
# not finanzierung / kaufvertrag / zwangsvollstreckungsunterwerfung: a Kaufvertrag bill names
# its Finanzierungsvollmacht, a Grundschuld bill the Kaufvertrag it finances.
_FINANCING_TERMS = ("grundschuld", "grundpfandrecht", "hypothek", "darlehen")
# A cost line naming one of these is a cost of the purchase (D135), unless it names a
# financing term too (a Grundschuld line may name the Kaufvertrag it secures).
_PURCHASE_TERMS = (
    "auflassungsvormerkung",
    "eigentumsumschreibung",
    "eintragung des eigentümers",
    "eigentümerwechsel",
    "kaufvertrag",
)
# Entries in the land register a purchase needs: a bill line naming one is a Grundbuch cost
# when the sender, subject and title name no kind (the row's kind, 11 Nov 2026).
_LAND_REGISTRY_ENTRIES = (
    "auflassungsvormerkung",
    "eigentumsumschreibung",
    "eintragung des eigentümers",
)
# The loan link of a bill that is only partly a loan cost (D135): which part it is for.
_LOAN_PART_LINK_NOTE = (
    "Nur der Teil für die Finanzierung (Geldbeschaffungskosten, z. B. die Grundschuld) "
    "betrifft das Darlehen; darum wird die Rechnung auch mit dem Darlehen verknüpft. Der "
    "Anteil für den Kauf bleibt bei den Nebenkosten des Objekts und hat mit dem Darlehen "
    "nichts zu tun."
)
# A whole financing bill (D35, D138): what it is and where it counts. One text for both ways it
# can have been paid; a rental-account payment adds _PAID_FROM_ACCOUNT_NOTE.
_FINANCING_NOTE = (
    "Diese Rechnung gehört zur Finanzierung (Grundschuld, Hypothek oder Darlehen). Solche "
    "Kosten sind Geldbeschaffungskosten: Sie zählen im Jahr der Zahlung als Werbungskosten und "
    "gehören nicht zu den Nebenkosten des Kaufs, also nicht zur AfA-Grundlage. Darum schlägt "
    "Hausbuch keinen Nebenkosten-Eintrag vor. Kam die Zahlung vom Mietkonto, schlägt Hausbuch "
    "sie beim Abgleich des Kontoauszugs als Geldbeschaffungskosten vor; kam sie von einem "
    "anderen Konto, zählt sie über die Karte „Von einem anderen Konto bezahlt“ in diesem "
    "Vorschlag."
)
# A Kautionsnachweis whose tenancy cannot be told (D42): typing only, and why.
_DEPOSIT_SEVERAL_NOTE = (
    "Die Kaution passt zu keinem oder zu mehreren Mietverhältnissen; bitte den Eingang beim "
    "richtigen Mietverhältnis von Hand erfassen (Kaution erhalten)."
)
_DEPOSIT_NONE_NOTE = (
    "Es gibt noch kein Mietverhältnis, zu dem die Kaution gehören könnte; bitte zuerst den "
    "Mietvertrag erfassen und den Eingang dann beim Mietverhältnis eintragen."
)
# A Wohnflächenberechnung's dwelling (D139): what the area is for, which row was taken and
# why, or the question when no row could be picked.
_LIVING_AREA_USE = (
    "Die Wohnfläche der ganzen Wohnung zählt für die Anlage V (Zeile 13) und für den "
    "Vergleich der Miete mit dem Mietspiegel."
)
_DWELLING_TAKEN_NOTE = "Wohnfläche aus der Zeile „{row}“ der Wohnflächenberechnung ({why}). "
_DWELLING_WHY = {
    "only": "die einzige Wohnung darin",
    "mea": "erkannt am Miteigentumsanteil Ihrer Wohnung",
    "label": "erkannt an der Bezeichnung Ihrer Wohnung",
}
_DWELLING_PICK_NOTE = (
    "Welche Wohnung ist Ihre? Die Wohnflächenberechnung nennt {count} Wohnungen; bitte Ihre "
    "unten anklicken, dann „Übernehmen“. "
)
_NO_DWELLING_NOTE = (
    "Aus dieser Unterlage ließ sich keine Wohnung mit ihrer Wohnfläche lesen. Fehlt die "
    "Wohnfläche, lässt sie sich bei der Aufgabe „Wohnfläche der Wohnung fehlt“ unter Steuern "
    "eintragen."
)
_DWELLING_KEPT_NOTE = "Ohne Klick bleibt die eingetragene Wohnfläche, wie sie ist. "
# D147: a Gutachten's remaining useful life (§7 Abs. 4 S. 2 EStG) as the shorter useful life.
_LIFE_BEFORE_NOTE = (
    "Das Gutachten nennt {life} zum {stichtag}; bis zur Übergabe am {handover} sind {elapsed} "
    "vergangen, daher {years} ab {year}."
)
_LIFE_AFTER_NOTE = (
    "Das Gutachten nennt {life} zum {stichtag}, nach der Übergabe am {handover}; daher {years} "
    "ab {year}."
)
_LIFE_LATER_NOTE = (
    "Das Gutachten nennt {life} zum {stichtag}, in einem späteren Jahr als die Übergabe am "
    "{handover}; daher {years} ab {year}. Bis dahin bleibt es bei der gesetzlichen AfA."
)
_LIFE_NO_STICHTAG_NOTE = (
    "Das Gutachten nennt {life}; ein Bewertungsstichtag wurde nicht gelesen, daher {years} ab "
    "der Übergabe am {handover} ({year}). Bitte prüfen, ob das Gutachten auf einen früheren "
    "Stichtag rechnet: dann ist die Zeit bis zur Übergabe abzuziehen."
)
_LIFE_ROUNDED = " Auf ganze Jahre aufgerundet: die längere Nutzungsdauer ist die vorsichtige Wahl."
_LIFE_USE = (
    " Mit „Übernehmen“ wird die AfA des Gebäudes ab {year} über diese Nutzungsdauer gerechnet "
    "statt über die gesetzlichen {statutory}, die dann noch bleiben."
)
_LIFE_NOT_SHORTER_NOTE = (
    "Das Gutachten nennt {life}; daraus folgen {years} ab {year}. Das ist nicht kürzer als die "
    "gesetzliche Nutzungsdauer, die dann noch {statutory} beträgt, daher wird keine kürzere "
    "Nutzungsdauer vorgeschlagen und die AfA bleibt beim gesetzlichen Satz."
)
_LIFE_USED_UP_NOTE = (
    "Das Gutachten nennt {life} zum {stichtag}; bis zur Übergabe am {handover} sind {elapsed} "
    "vergangen, mehr als diese Restnutzungsdauer. Daher wird keine Nutzungsdauer vorgeschlagen; "
    "bitte das Gutachten prüfen."
)
_LIFE_UNREADABLE_NOTE = (
    "Die Restnutzungsdauer des Gutachtens ließ sich nicht als Zahl lesen. Sie lässt sich unter "
    "Steuern bei „Kürzere Nutzungsdauer (Gutachten)“ eintragen."
)
# A catalogue document whose link was not found (D131): typing only, and what to do.
_NO_PROPERTY_NOTE = (
    "Zu diesem Dokument wurde kein Objekt gefunden: die Adresse passt zu keinem oder zu "
    "mehreren Objekten. Bitte das Dokument auf seiner Seite von Hand mit dem Objekt verknüpfen."
)
_NO_LOAN_NOTE = (
    "Zu diesem Dokument wurde kein Darlehen gefunden: Darlehensgeber und Nummer passen zu "
    "keinem Darlehen, und es gibt nicht genau eines. Bitte das Dokument auf seiner Seite von "
    "Hand mit dem Darlehen verknüpfen."
)
_NO_TENANCY_NOTE = (
    "Zu diesem Dokument wurde kein eindeutiges Mietverhältnis gefunden: Adresse und Datum "
    "passen zu keinem oder zu mehreren Mietverhältnissen. Bitte das Dokument auf seiner Seite "
    "von Hand mit dem Mietverhältnis verknüpfen."
)
# The two tenancy-group subtypes the document date cannot place (PR #135 review, F3): a
# tenant's Selbstauskunft predates the tenancy it applies for, and a
# Betriebskostenabrechnung covers a year in which the tenants may have changed. They link the
# property by address and leave the tenancy to the user.
_TENANCY_BY_HAND = frozenset(
    {DocumentSubtype.TENANT_SELF_DISCLOSURE, DocumentSubtype.OPERATING_COSTS_STATEMENT}
)
_TENANCY_BY_HAND_NOTE = (
    "Das Mietverhältnis lässt sich bei diesem Dokument nicht am Datum erkennen. Bitte das "
    "Dokument auf seiner Seite von Hand mit dem Mietverhältnis verknüpfen."
)
_CATALOGUE_INVOICES = (
    DocumentSubtype.UTILITY_BILL,
    DocumentSubtype.HOUSEHOLD_SERVICES_INVOICE,
    DocumentSubtype.WORK_EXPENSE_INVOICE,
)
# The proposal never deletes: the row is removed with its own button (Objekt › Nebenkosten).
_FINANCING_BELEG_NOTE = (
    "Diese Rechnung ist schon als Beleg eines Nebenkosten-Eintrags hinterlegt. Als "
    "Geldbeschaffungskosten gehört sie nicht zu den Nebenkosten des Kaufs und zählte sonst "
    "doppelt: bitte den Eintrag beim Objekt unter „Nebenkosten“ mit „Entfernen“ aus den "
    "Nebenkosten nehmen."
)
# D135: where each part of a bill counts, named in its tax_input:private_cost record's hint.
_ANLAGE_V_PLACE = {
    AllocationCategory.LOAN_INTEREST: "unter den Schuldzinsen",
    AllocationCategory.LOAN_FEES: "unter den Geldbeschaffungskosten",
    AllocationCategory.HAUSGELD_UMLAGEFAEHIG: "unter den umlagefähigen laufenden Kosten",
    AllocationCategory.HAUSGELD_VERWALTUNG: "unter den nicht umlagefähigen Kosten",
    AllocationCategory.PROPERTY_MANAGEMENT: "unter den nicht umlagefähigen Kosten",
    AllocationCategory.GRUNDSTEUER: "unter den umlagefähigen laufenden Kosten",
    AllocationCategory.INSURANCE: "unter den umlagefähigen laufenden Kosten",
    AllocationCategory.ELECTRICITY: "unter den umlagefähigen laufenden Kosten",
    AllocationCategory.REPAIRS: "unter dem Erhaltungsaufwand",
    AllocationCategory.PROFESSIONAL_FEES: "unter den sonstigen Werbungskosten",
    AllocationCategory.BANK_FEES: "unter den nicht umlagefähigen Kosten",
}
# The categories a privately paid part may carry: the deductible ones (D24 rule data).
PRIVATE_COST_CATEGORIES = tuple(
    c for c in AllocationCategory if TAX_TREATMENT[c] is TaxTreatment.DEDUCTIBLE
)
if set(_ANLAGE_V_PLACE) != set(PRIVATE_COST_CATEGORIES):  # pragma: no cover
    raise RuntimeError("every deductible category needs its Anlage V place")
_PAID_PRIVATELY_HINT = (
    "Von einem anderen Konto bezahlt: zählt so in der Anlage V {place}. Kam die Zahlung vom "
    "Mietkonto, diesen Teil überspringen."
)
_SPLIT_NOTE = (
    "Hausbuch hat die Positionen dieser Rechnung nach Art der Kosten aufgeteilt (die "
    "Umsatzsteuer anteilig): {parts}. Der Teil für den Kauf gehört zu den Nebenkosten und "
    "damit zur AfA-Grundlage, die übrigen Teile zählen im Jahr der Zahlung."
)
_UNDECIDED_NOTE = (
    "Nicht vorgeschlagen: {parts}. Ob das sofort abziehbar ist oder zu den Herstellungskosten "
    "gehört, entscheidet sich erst bei der Steuer (15-%-Grenze); bitte bei Bedarf von Hand "
    "erfassen."
)
_PAID_FROM_ACCOUNT_NOTE = (
    "Eine Zahlung dieser Rechnung vom Mietkonto wurde gefunden; ihre Aufteilung wird beim "
    "Abgleich des Kontoauszugs vorgeschlagen. Darum kein Eintrag „von einem anderen Konto "
    "bezahlt“."
)


# D145: one buyer's Grunderwerbsteuerbescheid against a row that holds both buyers' tax. The
# rest of the row becomes its own row without a Beleg, under this key suffix
# (cost:<kind>:other_notice); the apply creates it without this document as its Beleg.
OTHER_NOTICE = "other_notice"
_OTHER_NOTICE_LABEL = "Grunderwerbsteuer, weiterer Bescheid (z. B. des Miteigentümers)"
_ONE_NOTICE_NOTE = (
    "Dieser Bescheid setzt {read} fest, eingetragen sind {row} Grunderwerbsteuer. Bei einem "
    "gemeinsamen Kauf schickt das Finanzamt oft jedem Käufer einen eigenen Bescheid über "
    "seinen Anteil. Darum wird der Eintrag nicht gesenkt: Er bekommt {read} und diesen "
    "Bescheid als Beleg, und die übrigen {rest} stehen im nächsten Eintrag „Grunderwerbsteuer, "
    "weiterer Bescheid“, zusammen weiter {row}."
)
# D146: a card that attaches this document to a cost row says so; the Beleg is not a field.
_COST_BELEG_NOTE = (
    "Beim Übernehmen wird dieses Dokument als Beleg dieses Nebenkosten-Eintrags gespeichert."
)
_OTHER_NOTICE_NOTE = (
    "Die übrigen {rest} gehören vermutlich zum Bescheid des anderen Käufers. Der Eintrag "
    "bleibt ohne Beleg, bis dieser Bescheid hochgeladen ist; er wird ihm dann zugeordnet, und "
    "bis dahin fragt die Liste der fehlenden Unterlagen danach. Ist die Grunderwerbsteuer "
    "insgesamt nur {read}, diesen Eintrag überspringen."
)


# --- A bill's cost lines (D135) -------------------------------------------------------


class BillSort:
    """How a bill is booked (D135). financing: the whole bill is a financing cost (D35, or
    every line is). parts: the gross euro part per category in taxonomy order, VAT shared,
    summing to the total; empty when the lines were not used (missing, not summing, a line
    without a category): the bill is then booked whole, as before. nets: the same parts before
    their share of the Umsatzsteuer (phase 3 D137: a repair's net for the 15 % test)."""

    def __init__(
        self,
        financing: bool,
        parts: dict[AllocationCategory, Decimal],
        nets: dict[AllocationCategory, Decimal] | None = None,
    ) -> None:
        self.financing = financing
        self.parts = parts
        self.nets = nets or {}

    @property
    def by_lines(self) -> bool:
        return bool(self.parts)


def sort_bill(
    *,
    kind: ExtractionKind | None,
    subject: Any,
    title: Any,
    cost_lines: ExtractedValue,
    amount_total: ExtractedValue,
) -> BillSort:
    """The sorting of one invoice or Bescheid (D135); pure, for the proposal and for the
    bank line's split (services.matching). subject and title are the reading's values."""
    if kind is ExtractionKind.INVOICE:
        whole_financing = names_financing(subject, title)
    elif kind is ExtractionKind.BESCHEID:
        whole_financing = names_financing(title)
    else:
        return BillSort(False, {})
    parts, nets = _line_parts(cost_lines, amount_total, whole_financing)
    if parts and set(parts) == {AllocationCategory.LOAN_FEES}:
        return BillSort(True, parts, nets)
    return BillSort(whole_financing and not parts, parts, nets)


def sort_reading(
    kind: ExtractionKind | None,
    subtype: str | None,
    ev: Callable[[str], ExtractedValue],
) -> BillSort:
    """sort_bill over a reading's values (`ev` gives a field's value, missing when absent).
    The catalogue's invoices (a utility bill, a §35a or work-expense invoice, D131) are never
    sorted: they are no Nebenkosten or financing cost of the purchase."""
    if subtype in _CATALOGUE_INVOICES:
        return BillSort(False, {})
    return sort_bill(
        kind=kind,
        subject=ev("subject").value,
        title=ev("title").value,
        cost_lines=ev("cost_lines"),
        amount_total=ev("amount_total"),
    )


_NoParts = tuple[dict[AllocationCategory, Decimal], dict[AllocationCategory, Decimal]]


def _line_parts(read: ExtractedValue, total: ExtractedValue, whole_financing: bool) -> _NoParts:
    """The gross part per category (VAT shared, summing to the total) and its net."""
    if read.value is None or "validates" not in read.checks:
        return {}, {}
    lines = parse_cost_lines(read.value)
    if lines is None or not cost_lines_balance(lines, total.value):
        return {}, {}
    any_purchase = any(_names(line.text, _PURCHASE_TERMS) for line in lines if not line.vat)
    net: dict[AllocationCategory, Decimal] = {}
    vat = Decimal(0)
    for line in lines:
        if line.vat:
            vat += line.amount
            continue
        if _names(line.text, _FINANCING_TERMS):
            category = AllocationCategory.LOAN_FEES
        elif _names(line.text, _PURCHASE_TERMS):
            category = AllocationCategory.ACQUISITION_COST
        elif whole_financing and not any_purchase:
            category = AllocationCategory.LOAN_FEES
        else:
            category = line.category
        if category is None:
            return {}, {}  # a line nobody could place: the bill stays whole
        net[category] = net.get(category, Decimal(0)) + line.amount
    if not net or any(amount <= 0 for amount in net.values()):
        return {}, {}
    ordered = [c for c in AllocationCategory if c in net]
    gross = [net[c] for c in ordered]
    if vat:
        whole = sum(gross, Decimal(0))
        shares = [
            Fraction(numerator=int(net[c] * 100), denominator=int(whole * 100)) for c in ordered
        ]
        gross = [g + v for g, v in zip(gross, split_cents(vat, shares), strict=True)]
    # The parts sum to the lines; the total may differ by the tolerated cent: the last part
    # takes it, so the parts are exactly the bill.
    stated = _decimal(total.value)
    if stated is not None:
        gross[-1] += stated - sum(gross, Decimal(0))
    return dict(zip(ordered, gross, strict=True)), {c: net[c] for c in ordered}


def _names(text: str, terms: Sequence[str]) -> bool:
    t = _norm(text)
    return any(term in t for term in terms)


def _parts_text(parts: Iterable[tuple[AllocationCategory, Decimal]]) -> str:
    return ", ".join(f"{CATEGORY_LABEL[c]} {_euro_de(a)}" for c, a in parts)


# --- Entry point ---------------------------------------------------------------------


def build_proposal(
    kind: ExtractionKind | None, extraction: _Typing, context: PrefillContext
) -> list[ProposalRecord]:
    """The records a document proposes (fixture prefill.yaml). `extraction` is the kind's
    extraction model (schemas/extraction.py), `_Typing` when kind is None."""
    b = _Builder(kind, extraction, context)
    b.typing()
    match kind:
        case ExtractionKind.PURCHASE_CONTRACT:
            b.purchase_contract()
        case ExtractionKind.INVOICE if b.catalogue_invoice:
            b.catalogue_link()  # D131: an invoice of the catalogue proposes no Nebenkosten row
        case ExtractionKind.INVOICE | ExtractionKind.BESCHEID:
            b.cost_document()
        case ExtractionKind.GENERAL | ExtractionKind.MANAGEMENT_CONTRACT:
            b.catalogue_link()
        case ExtractionKind.LOAN_CONTRACT:
            b.loan_contract()
        case ExtractionKind.LEASE:
            b.lease()
        case ExtractionKind.HANDOVER_PROTOCOL:
            b.handover_protocol()
        case ExtractionKind.GRUNDSTEUERBESCHEID | ExtractionKind.WIRTSCHAFTSPLAN:
            b.link_property(_resolve_property(extraction, context))
        case ExtractionKind.HAUSGELDABRECHNUNG:
            b.hausgeldabrechnung()
        case ExtractionKind.ZINSBESCHEINIGUNG:
            b.zinsbescheinigung()
        case ExtractionKind.DEPOSIT_RECORD:
            b.deposit_record()
        case ExtractionKind.LOHNSTEUERBESCHEINIGUNG:
            b.wage_certificate()
        case ExtractionKind.GEHALTSABRECHNUNG:
            b.payslip()
        case ExtractionKind.EINKOMMENSTEUERBESCHEID:
            b.income_tax_assessment()
        case _:  # statement, unknown: typing only
            pass
    b.hand_fields()
    return b.records


# --- The Grundsteuer schedule (phase 5 D126) --------------------------------------------

GRUNDSTEUER_SCHEDULE_KEY = "tax_input:grundsteuer_schedule"
GRUNDSTEUER_SLOTS = 4  # a Bescheid states at most four Fälligkeiten (§28 GrStG)
_DUE_DAY = re.compile(r"^\s*(\d{1,2})\.\s*(\d{1,2})\.?\s*(\d{4})?\s*$")
_NO_YEAR_NOTE = (
    "Das erste Jahr des Bescheids wurde nicht gelesen; bitte das Jahr und die Fälligkeiten "
    "eintragen."
)
_SUM_NOTE = "Die Raten ergeben nicht den Jahresbetrag; bitte die Beträge prüfen."


def grundsteuer_schedule_record(
    extraction: _Typing, context: PrefillContext
) -> ProposalRecord | None:
    """A Grundsteuerbescheid's schedule as a `tax_input:grundsteuer_schedule` record (phase 5
    D126), for a property found (a tax input belongs to one); None otherwise.

    Fields: tax_year (the Bescheid's first year), value.annual_amount (the Jahresbetrag),
    value.due_on_<n> and value.amount_<n> per Fälligkeit read ("15.02." becomes the date in the
    first year, keeping the quote), and value.rhythm from the number of dates. An instalment's
    amount is the Vierteljahresbetrag when the Bescheid states one, else the Jahresbetrag over
    the number of dates, rounded to cents (derived). Without dates, four empty slots are
    proposed for the user to type. The apply turns the slots into the schedule's instalments
    (services/proposals). Kept out of build_proposal so phase 2's prefill fixture, which pins
    a Bescheid's records to typing and link:property, stays as it is (step 3 as-built)."""
    if _resolve_property(extraction, context) is None:
        return None
    ev = _field_of(extraction)
    year_ev = ev("tax_year")
    year = year_ev.value if isinstance(year_ev.value, int) else None
    dates_ev = ev("due_dates")
    days = _due_days(dates_ev.value)
    notes: list[str] = []
    fields: dict[str, ExtractedValue] = {
        "tax_year": year_ev,
        "value.annual_amount": ev("yearly_amount"),
    }
    if year is None and dates_ev.value is not None:
        notes.append(_NO_YEAR_NOTE)
    dates: list[ExtractedValue] = []
    for month, day in days:
        due = _safe_date(year, month, day) if year is not None else None
        if due is None:
            dates.append(_unmapped(dates_ev))
        else:
            dates.append(dates_ev.model_copy(update={"value": due.isoformat()}))
    dates.sort(key=lambda d: (d.value is None, d.value or ""))
    amount = _instalment_amount(ev("installment_amount"), ev("yearly_amount"), dates_ev, len(dates))
    for n, due in enumerate(dates or [ExtractedValue.missing()] * GRUNDSTEUER_SLOTS, start=1):
        fields[f"value.due_on_{n}"] = due
        fields[f"value.amount_{n}"] = amount if dates else ExtractedValue.missing()
    fields["value.rhythm"] = _derived(_rhythm(days), [("due_dates", dates_ev)])
    yearly = _decimal(ev("yearly_amount").value)
    each = _decimal(amount.value)
    if (
        yearly is not None
        and each is not None
        and dates
        and abs(each * len(dates) - yearly) > GRUNDSTEUER_SUM_TOLERANCE
    ):
        notes.append(_SUM_NOTE)
    return ProposalRecord(
        key=GRUNDSTEUER_SCHEDULE_KEY,
        entity_type="tax_input",
        mode="create",
        fields=fields,
        notes=notes,
    )


def grundsteuer_schedule_value(values: dict[str, Any]) -> dict[str, Any]:
    """The apply's `value` of a grundsteuer_schedule record from the confirmed slots
    ({annual_amount, rhythm, due_on_<n>, amount_<n>}): the filled slots as instalments in date
    order; a slot with neither date nor amount is dropped, a half-filled one kept (the schema
    then refuses it). Never computes an amount."""
    out = {k: v for k, v in values.items() if not re.match(r"^(due_on|amount)_\d+$", k)}
    slots = sorted(
        {int(m.group(2)) for k in values if (m := re.match(r"^(due_on|amount)_(\d+)$", k))}
    )
    instalments = [
        {"due_on": values.get(f"due_on_{n}"), "amount": values.get(f"amount_{n}")}
        for n in slots
        if values.get(f"due_on_{n}") is not None or values.get(f"amount_{n}") is not None
    ]
    instalments.sort(key=lambda i: str(i["due_on"]))
    return out | {"instalments": instalments}


def _field_of(extraction: _Typing) -> Callable[[str], ExtractedValue]:
    def ev(name: str) -> ExtractedValue:
        value = getattr(extraction, name, None)
        return value if isinstance(value, ExtractedValue) else ExtractedValue.missing()

    return ev


def _due_days(value: Any) -> list[tuple[int, int]]:
    """(month, day) per Fälligkeit read: "15.02." or "15.02.2027"; a list or one text."""
    items = value if isinstance(value, list) else re.split(r"[;,]|\bund\b", str(value or ""))
    out: list[tuple[int, int]] = []
    for item in items:
        m = _DUE_DAY.match(str(item))
        if m is not None and (int(m.group(2)), int(m.group(1))) not in out:
            out.append((int(m.group(2)), int(m.group(1))))
    return out[:GRUNDSTEUER_SLOTS]


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _unmapped(source: ExtractedValue) -> ExtractedValue:
    """A value that does not map: proposed missing, its quote kept."""
    checks = ["cited"] if source.source is not None else []
    return ExtractedValue(value=None, source=source.source, checks=checks, confidence="low")


def _instalment_amount(
    stated: ExtractedValue, yearly: ExtractedValue, dates: ExtractedValue, n: int
) -> ExtractedValue:
    if stated.value is not None and _decimal(stated.value) is not None:
        return stated
    total = _decimal(yearly.value)
    if total is None or n == 0:
        return _derived(None, [("yearly_amount", yearly)])
    each = (total / n).quantize(_CENTS, rounding=ROUND_HALF_UP)
    return _derived(str(each), [("yearly_amount", yearly), ("due_dates", dates)])


def _rhythm(days: list[tuple[int, int]]) -> str | None:
    """The Bescheid's rhythm (§28 GrStG), informational: four dates quarterly, two
    half-yearly, one on 15 Aug or 1 Jul yearly."""
    if len(days) == 4:
        return "quarterly"
    if len(days) == 2:
        return "half_yearly"
    if days == [(8, 15)]:
        return "yearly_august"
    if days == [(7, 1)]:
        return "yearly_july"
    return None


# --- The builder -----------------------------------------------------------------------


class _Builder:
    def __init__(
        self, kind: ExtractionKind | None, extraction: _Typing, context: PrefillContext
    ) -> None:
        self.kind = kind
        self.x = extraction
        self.ctx = context
        self.records: list[ProposalRecord] = []
        # Reading fields a record of this proposal asks for without proposing them (an
        # unstated optional field of a record that creates, whose card still shows it), by
        # the record field that asks: the typing record leaves their hand fields out and
        # names that field in hand_from (D132).
        self.carried: dict[str, CarriedField] = {}
        # A utility bill or a §35a or work-expense invoice (D131): read as an invoice, never a
        # Nebenkosten or financing cost of the purchase.
        self.catalogue_invoice = context.classification.subtype in _CATALOGUE_INVOICES
        # A Grundbuchamt Kostenrechnung may be typed bescheid, which has no subject (D35, F1).
        # Its cost lines decide whether it is whole or split by kind of cost (D135).
        self.bill = sort_reading(kind, context.classification.subtype, self.ev)
        self.financing = self.bill.financing

    # -- values

    def ev(self, name: str) -> ExtractedValue:
        """The extraction's value of a field; missing when the model has no such field."""
        value = getattr(self.x, name, None)
        return value if isinstance(value, ExtractedValue) else ExtractedValue.missing()

    def stated(self, name: str) -> bool:
        return self.ev(name).value is not None

    def add(
        self,
        key: str,
        entity_type: str,
        mode: str,
        fields: dict[str, ExtractedValue],
        *,
        target_id: UUID | None = None,
        current: dict[str, JsonValue] | None = None,
        notes: Sequence[str] = (),
    ) -> None:
        self.records.append(
            ProposalRecord(
                key=key,
                entity_type=entity_type,
                mode=mode,
                target_id=target_id,
                fields=fields,
                current=current,
                notes=list(notes),
            )
        )

    def link(self, key: str, target_id: UUID | None) -> None:
        self.add(key, "link", "create", {}, target_id=target_id)

    def link_property(self, prop: ExistingProperty | None) -> None:
        if prop is not None:
            self.link("link:property", prop.id)

    # -- typing (every kind)

    def typing(self) -> None:
        c = self.ctx.classification
        fields = {"type": _code_value(c.type.value, c.confidence)}
        # A financing bill is a financing_invoice, whatever the classifier said (D35, D138);
        # code decides it, so the classifier's confidence does not apply.
        if self.financing:
            fields["subtype"] = _code_value(
                DocumentSubtype.FINANCING_INVOICE.value, self._financing_confidence()
            )
        elif c.subtype is not None:
            fields["subtype"] = _code_value(c.subtype.value, c.confidence)
        for name in ("title", "document_date", "tax_year"):
            if self.stated(name):
                fields[name] = self.ev(name)
        if self.kind is ExtractionKind.INVOICE:
            tax_year = self._invoice_tax_year()
            if tax_year is not None:
                fields["tax_year"] = tax_year
        # D146: a Bescheid that proves a purchase cost (the Grunderwerbsteuerbescheid) states
        # no year of its own; it is filed like an invoice, by its due date, else its date (D36).
        if (
            self.kind is ExtractionKind.BESCHEID
            and fields.get("subtype") is not None
            and fields["subtype"].value == DocumentSubtype.COST_BASIS_EVIDENCE.value
            and "tax_year" not in fields
        ):
            tax_year = self._invoice_tax_year()
            if tax_year is not None:
                fields["tax_year"] = tax_year
        if self.kind in _PAY_KINDS and "tax_year" not in fields:
            tax_year = self._pay_tax_year()
            if tax_year.value is not None:
                fields["tax_year"] = tax_year
        notes = []
        if self.financing:
            notes.append(_FINANCING_NOTE)
            if any(cost.document_id == self.ctx.document_id for cost in self.ctx.costs):
                notes.append(_FINANCING_BELEG_NOTE)
        parts = self.bill.parts
        if len(parts) > 1:
            notes.append(_SPLIT_NOTE.format(parts=_parts_text(parts.items())))
        undecided = [(c, a) for c, a in parts.items() if c not in _BOOKED_FROM_LINES]
        if undecided:
            notes.append(_UNDECIDED_NOTE.format(parts=_parts_text(undecided)))
        if self.ctx.paid_from_rental_account and self._private_parts():
            notes.append(_PAID_FROM_ACCOUNT_NOTE)
        self.add("typing", "typing", "create", fields, notes=notes)

    def _financing_confidence(self) -> ConfidenceLevel:
        """How sure the financing decision is (D138): the confidence of the text that named
        the financing term (an invoice's subject or title, a Bescheid's title; the higher when
        both do), else of the cost lines that are all loan costs (sort_bill)."""
        names = ("subject", "title") if self.kind is ExtractionKind.INVOICE else ("title",)
        named = [self.ev(n).confidence for n in names if names_financing(self.ev(n).value)]
        if named:
            return max(named, key=_CONFIDENCE_ORDER.__getitem__)
        return self.ev("cost_lines").confidence

    def hand_fields(self) -> None:
        """D132: the kind's hand-entry fields on the typing record (records[0]), once every
        other record is built. Each is the reading's own value (as read, so a value code
        derived keeps its derived_from), or missing when the reading holds none; a stored
        value the hand field cannot hold (raw text in a money field) is proposed empty with
        its quote, as the typing form shows it (rules.hand_fields.hand_values). Left out: a
        hand field whose reading field another record proposes, i.e. holds that very value
        (by identity, not equality: two missing values are equal), or that is `carried`; the
        record field that asks for it instead is named in the typing's hand_from, so the
        apply writes the value applied there into the reading (D132, J1).

        Without a reading of its own (kind None: typing only, or blocked by the tenant gate)
        the typing record carries the hand fields the document's typing form offers now
        (context.hand_fields): each stored value as a code value, the rest missing."""
        typing = self.records[0]
        if self.kind is None:
            section = self.ctx.hand_fields
            if section is None:
                return
            stored = section.values.model_dump(mode="json")
            for name in HAND_FIELDS:
                if name in section.available:
                    value = stored[name]
                    typing.fields[name] = (
                        ExtractedValue.missing() if value is None else _code_value(value, "high")
                    )
            return
        mapping = HAND_FIELD_MAP.get(self.kind)
        if not mapping:
            return
        proposed: dict[int, CarriedField] = {}
        for record in self.records[1:]:
            for field, value in record.fields.items():
                proposed.setdefault(id(value), CarriedField(key=record.key, field=field))
        shown = hand_values(self.x, mapping)
        for name in HAND_FIELDS:
            target = mapping.get(name)
            if target is None:
                continue
            value = self.ev(target)
            carrier = self.carried.get(target) or proposed.get(id(value))
            if carrier is not None:
                typing.hand_from[name] = carrier
                continue
            if value.value is not None and getattr(shown, name) is None:
                checks = ["cited"] if value.source is not None else []
                value = ExtractedValue(
                    value=None, source=value.source, checks=checks, confidence="low"
                )
            typing.fields[name] = value

    def _invoice_tax_year(self) -> ExtractedValue | None:
        """The filing year of an invoice (D36): the year of due_on, else of document_date;
        neither stated, nothing. The year a cost counts in stays the payment's (D31)."""
        for name in ("due_on", "document_date"):
            on = _iso_date(self.ev(name).value)
            if on is not None:
                return _derived(on.year, [(name, self.ev(name))])
        return None

    # -- Kaufvertrag

    def purchase_contract(self) -> None:
        proposed = {
            field: (_mapped(self.ev(src), _property_kind) if field == "kind" else self.ev(src))
            for field, src in _PROPERTY_FIELDS
        }
        # D139: an ETW's contract states the flat's Wohnfläche, which is the property's too
        # (phase 3 D71); the unit keeps it as before.
        if proposed["kind"].value == PropertyKind.ETW.value and self.stated("floor_area_m2"):
            proposed["living_area_m2"] = self.ev("floor_area_m2")
        notes = []
        purchase = self.ev("purchase_date")
        if purchase.value is None and purchase.source is not None:
            notes.append(f"Übergang laut S. {purchase.source.page}: „{purchase.source.quote}“")

        prop = _match_property(self.x, self.ctx.properties)
        if prop is None:
            self.add("property", "property", "create", proposed, notes=notes)
        else:
            self._match_record(
                "property",
                "property",
                prop.id,
                proposed,
                _property_current(prop),
                notes=notes,
                milestones=_MILESTONES,
                keyed={"address.street": _street_key, "address.house_number": _house_number_key},
            )
        self._unit(prop)
        self._price_split()
        self._ownership()
        self.link("link:property", None if prop is None else prop.id)

    def _match_record(
        self,
        key: str,
        entity_type: str,
        target_id: UUID,
        proposed: dict[str, ExtractedValue],
        existing: dict[str, JsonValue],
        *,
        notes: Sequence[str] = (),
        milestones: Iterable[str] = (),
        keyed: dict[str, Callable[[Any], str]] | None = None,
    ) -> None:
        """unchanged when every stated field the context holds equals it, else patch with
        the differing ones; a differing milestone date gets its own correction record.
        `keyed` fields compare by that key instead (an address spelled another way is the
        same address, so the stored spelling is not proposed for change)."""
        keyed = keyed or {}

        def same(f: str, value: JsonValue) -> bool:
            if f in keyed:
                return keyed[f](value) == keyed[f](existing[f])
            return _same(value, existing[f])

        compared = {f: v for f, v in proposed.items() if v.value is not None and f in existing}
        corrections = {
            f: v for f, v in compared.items() if f in milestones and not same(f, v.value)
        }
        kept = {f: v for f, v in compared.items() if f not in corrections}
        differing = {f: v for f, v in kept.items() if not same(f, v.value)}
        fields = differing or kept
        self.add(
            key,
            entity_type,
            "patch" if differing else "unchanged",
            fields,
            target_id=target_id,
            current={f: existing[f] for f in fields},
            notes=notes,
        )
        for f, v in corrections.items():
            self.add(
                f"correction:{f}",
                entity_type,
                "correction",
                {f: v},
                target_id=target_id,
                current={f: existing[f]},
            )

    def _unit(self, prop: ExistingProperty | None) -> None:
        kind = _property_kind(self.ev("property_kind").value)
        if not self.stated("unit_label") and kind is not PropertyKind.ETW:
            return
        label = self.ev("unit_label")
        proposed = {
            "label": label,
            "mea": self.ev("mea"),
            "floor_area_m2": self.ev("floor_area_m2"),
        }
        unit = None
        if prop is not None and label.value is not None:
            unit = _first(
                u
                for u in self.ctx.units
                if u.property_id == prop.id and _norm(u.label) == _norm(label.value)
            )
        elif prop is not None:
            return  # an existing property and no label to match a unit by
        if unit is None:
            fields = {f: v for f, v in proposed.items() if f != "mea" or v.value is not None}
            self.add("unit", "unit", "create", fields)
        else:
            self._match_record("unit", "unit", unit.id, proposed, _unit_current(unit))

    def _price_split(self) -> None:
        price, land = self.ev("purchase_price"), self.ev("land_value")
        if price.value is None or land.value is None:
            return
        share = None
        p, lv = _decimal(price.value), _decimal(land.value)
        if p is not None and lv is not None and p > 0 and 0 <= lv < p:
            share = ((p - lv) / p).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
            share = share if Decimal(0) < share < Decimal(1) else None
        value = None if share is None else str(share)
        derived = _derived(value, [("purchase_price", price), ("land_value", land)])
        self.add("price_split", "price_split", "create", {"building_share": derived})

    def _ownership(self) -> None:
        buyers, clause = self.ev("buyers"), self.ev("ownership_clause")
        names = buyers.value
        if not isinstance(names, list) or not names or clause.value is None:
            return
        owners = []
        for name in names:
            if not isinstance(name, str):
                return
            matches = [o for o in self.ctx.owners if _norm(o.full_name) == _norm(name)]
            if len(matches) != 1:
                return
            owners.append(matches[0])
        if len({o.id for o in owners}) != len(owners):
            return
        share = _ownership_share(str(clause.value), len(owners))
        if share is None:
            return
        numerator, denominator = share
        value = [
            {
                "owner_id": str(o.id),
                "share": {"numerator": numerator, "denominator": denominator},
            }
            for o in owners
        ]
        derived = _derived(value, [("buyers", buyers), ("ownership_clause", clause)])
        self.add("ownership", "ownership", "create", {"shares": derived})

    # -- Nebenkosten invoices and Bescheide

    def cost_document(self) -> None:
        prop = _resolve_property(self.x, self.ctx)
        if prop is None:
            return
        parts = self.bill.parts
        if self.financing:
            # D35: no cost record at all (not even unchanged or patch); the note on typing
            # says why. The loan is linked only when it is not a guess.
            self.link_property(prop)
            self._link_only_loan(prop)
            self._private_costs()
            return
        if self.bill.by_lines:
            # D135: the purchase part is the Nebenkosten row; each deductible part a cost paid
            # from another account.
            purchase = parts.get(AllocationCategory.ACQUISITION_COST)
            if purchase is not None:
                self._cost(prop, self._part_value(purchase))
            self.link_property(prop)
            if AllocationCategory.LOAN_FEES in parts:
                self._link_only_loan(prop, notes=[_LOAN_PART_LINK_NOTE])
            self._private_costs()
            return
        if self.ctx.classification.subtype is DocumentSubtype.COST_BASIS_EVIDENCE:
            self._cost(prop, self.ev("amount_total"))
        self.link_property(prop)

    def _link_only_loan(self, prop: ExistingProperty, notes: Sequence[str] = ()) -> None:
        loans = [loan for loan in self.ctx.loans if loan.property_id == prop.id]
        if len(loans) == 1:
            self.add("link:loan", "link", "create", {}, target_id=loans[0].id, notes=notes)

    def _part_value(self, amount: Decimal) -> ExtractedValue:
        """A part of the bill: the total itself (the same value, so typing does not ask for it
        twice, D132) when it is the whole bill, else computed from the lines."""
        total = self.ev("amount_total")
        if _decimal(total.value) == amount:
            return total
        return _derived(
            str(amount.quantize(_CENTS)),
            [("cost_lines", self.ev("cost_lines")), ("amount_total", total)],
        )

    def _private_parts(self) -> list[tuple[AllocationCategory, Decimal | None]]:
        """The parts a cost paid from another account stands for: the deductible parts of the
        lines, or the whole financing bill (amount None: its total as read)."""
        if self.bill.by_lines:
            return [(c, a) for c, a in self.bill.parts.items() if c in PRIVATE_COST_CATEGORIES]
        if self.financing:
            return [(AllocationCategory.LOAN_FEES, None)]
        return []

    def _private_costs(self) -> None:
        """D135: a tax_input:private_cost:<category> record per deductible part, unless a
        rental-account line pays the bill. tax_year: the bill's year (due_on, else its
        date, D36); label: the title, else the sender."""
        if self.ctx.paid_from_rental_account:
            return
        total = self.ev("amount_total")
        tax_year = self._invoice_tax_year() or ExtractedValue.missing()
        label = _label_value(self.ev("title"), self.ev("sender"))
        for category, amount in self._private_parts():
            if amount is None:
                value = total.model_copy()  # its own object: typing keeps Betrag gesamt (D132)
            else:
                value = self._part_value(amount)
                if value is total:
                    value = total.model_copy()
            klass = "financing" if category is AllocationCategory.LOAN_FEES else "other"
            confidence = total.confidence if total.value is not None else "low"
            self.add(
                f"tax_input:private_cost:{category.value}",
                "tax_input",
                "create",
                {
                    "tax_year": tax_year,
                    "value.cost_class": _code_value(klass, "high"),
                    "value.category": _code_value(category.value, confidence),
                    "value.label": label,
                    "value.amount": value,
                    **self._limit_fields(category),
                },
                notes=[_PAID_PRIVATELY_HINT.format(place=_ANLAGE_V_PLACE[category])],
            )

    def _limit_fields(self, category: AllocationCategory) -> dict[str, ExtractedValue]:
        """Phase 3 D137: a part the 15 % test counts (repairs) also proposes when the work was
        done and its net. The invoice reading has no service period, so the date proposed is
        the bill's date (on or after the work; the card asks for the Leistungsdatum when the
        bill states one); the net is the part's lines before its share of the Umsatzsteuer."""
        if category not in LIMIT_15_CATEGORIES:
            return {}
        dated = self.ev("document_date")
        performed = dated.model_copy(
            update={"confidence": "medium" if dated.confidence == "high" else dated.confidence}
        )
        net = self.bill.nets.get(category)
        amount_net = (
            ExtractedValue.missing()
            if net is None
            else _derived(
                str(net.quantize(_CENTS)),
                [("cost_lines", self.ev("cost_lines")), ("amount_total", self.ev("amount_total"))],
            )
        )
        return {"value.performed_on": performed, "value.amount_net": amount_net}

    def _cost_kind_value(self) -> ExtractedValue:
        """The Nebenkosten row's kind: the first of the sender, the subject, the title and the
        bill's purchase lines that names one, keeping that value's quote. A Grundbuch bill is
        often sent by a Landesjustizkasse, which names no kind; its subject ("Kostenrechnung in
        der Grundbuchsache") or its lines (Auflassungsvormerkung) do (11 Nov 2026). None:
        "Sonstige", at the sender's confidence, low without a sender."""
        for name in ("sender", "subject", "title"):
            read = self.ev(name)
            if read.value is not None and _cost_kind(read.value) is not AcquisitionCostKind.OTHER:
                return _mapped(read, _cost_kind)
        lines = self.ev("cost_lines")
        if lines.value is not None and _purchase_lines_kind(lines.value) is not None:
            return _mapped(lines, _purchase_lines_kind)
        sender = self.ev("sender")
        if sender.value is None:
            return _code_value(AcquisitionCostKind.OTHER.value, "low")
        return _mapped(sender, _cost_kind)

    def _cost(self, prop: ExistingProperty, amount: ExtractedValue) -> None:
        kind_value = self._cost_kind_value()
        note = self.ev("reference")
        stated = {f: v for f, v in (("amount", amount), ("note", note)) if v.value is not None}

        # 1. A row whose Beleg is this document: never a second row for the same Beleg.
        same_beleg = _first(c for c in self.ctx.costs if c.document_id == self.ctx.document_id)
        if same_beleg is not None and self._one_buyers_notice(same_beleg, amount, kind_value):
            existing = {"amount": _json(same_beleg.amount), "note": same_beleg.note}
            differing = {f: v for f, v in stated.items() if not _same(v.value, existing[f])}
            self._split_notice(same_beleg, amount, differing, existing)
            return
        if same_beleg is not None:
            existing = {"amount": _json(same_beleg.amount), "note": same_beleg.note}
            differing = {f: v for f, v in stated.items() if not _same(v.value, existing[f])}
            fields = differing or stated
            self.add(
                f"cost:{same_beleg.kind.value}",
                "acquisition_cost",
                "patch" if differing else "unchanged",
                fields,
                target_id=same_beleg.id,
                current={f: existing[f] for f in fields},
            )
            return

        kind = kind_value.value
        # 2. A row of that kind on the property without a Beleg: the apply attaches this one.
        without_beleg = sorted(
            (
                c
                for c in self.ctx.costs
                if c.property_id == prop.id and c.kind.value == kind and c.document_id is None
            ),
            key=lambda c: (not _same_amount(amount.value, c.amount), c.id),
        )
        if without_beleg:
            row = without_beleg[0]
            existing = {"amount": _json(row.amount), "note": row.note}
            if self._one_buyers_notice(row, amount, kind_value):
                self._split_notice(row, amount, stated, existing)
                return
            self.add(
                f"cost:{kind}",
                "acquisition_cost",
                "patch",
                stated,
                target_id=row.id,
                current={f: existing[f] for f in stated},
                notes=[_COST_BELEG_NOTE],
            )
            return

        # 3. Otherwise a new row (another bill of the same kind is real).
        fields = {"kind": kind_value, "amount": amount}
        if note.value is not None:
            fields["note"] = note
        # The new row's card asks for its note, stated or not (D132).
        self.carried["reference"] = CarriedField(key=f"cost:{kind}", field="note")
        self.add(f"cost:{kind}", "acquisition_cost", "create", fields, notes=[_COST_BELEG_NOTE])

    def _one_buyers_notice(
        self, row: ExistingCost, amount: ExtractedValue, kind_value: ExtractedValue
    ) -> bool:
        """D145: a Grunderwerbsteuerbescheid that assesses less than the Grunderwerbsteuer row
        it would patch is one buyer's notice of a joint purchase (the Finanzamt sends one per
        buyer, each on that buyer's share), never a reason to lower the row.

        Only while no notice has been processed on the property: once another transfer-tax
        row has a Beleg or is a waiting rest row, the row is patched as before (the reading
        wins), so a typed total that was a little off, or a lower re-read, never splits off a
        rest row for a notice that does not exist (D145, from the review)."""
        if self.kind is not ExtractionKind.BESCHEID:
            return False
        transfer_tax = AcquisitionCostKind.REAL_ESTATE_TRANSFER_TAX
        if kind_value.value != transfer_tax.value or row.kind is not transfer_tax:
            return False
        if any(
            c.id != row.id
            and c.property_id == row.property_id
            and c.kind is transfer_tax
            and (c.document_id is not None or c.note == _OTHER_NOTICE_LABEL)
            for c in self.ctx.costs
        ):
            return False
        read = _decimal(amount.value)
        return read is not None and read > 0 and row.amount - read > _CENTS

    def _split_notice(
        self,
        row: ExistingCost,
        amount: ExtractedValue,
        fields: dict[str, ExtractedValue],
        existing: dict[str, JsonValue],
    ) -> None:
        """D145: the row takes this notice (its amount, this document as its Beleg) and the
        rest of the row becomes its own row without a Beleg, so the total stays as typed and
        the checklist asks for the other notice. Both in the one form (D132)."""
        read = _decimal(amount.value)
        assert read is not None  # _one_buyers_notice
        rest = row.amount - read
        kind = row.kind.value
        values = {
            "row": _euro_de(row.amount),
            "read": _euro_de(read),
            "rest": _euro_de(rest),
        }
        self.add(
            f"cost:{kind}",
            "acquisition_cost",
            "patch",
            fields,
            target_id=row.id,
            current={f: existing[f] for f in fields},
            notes=[_ONE_NOTICE_NOTE.format(**values)],
        )
        if amount is self.ev("amount_total"):
            inputs = [("amount_total", amount)]
        else:
            inputs = [
                ("cost_lines", self.ev("cost_lines")),
                ("amount_total", self.ev("amount_total")),
            ]
        self.add(
            f"cost:{kind}:{OTHER_NOTICE}",
            "acquisition_cost",
            "create",
            {
                "kind": _code_value(kind, "high"),
                "amount": _derived(str(rest.quantize(_CENTS)), inputs),
                "note": _code_value(_OTHER_NOTICE_LABEL, "high"),
            },
            notes=[_OTHER_NOTICE_NOTE.format(**values)],
        )

    # -- Darlehensvertrag

    def loan_contract(self) -> None:
        monthly = self.ev("monthly_payment")
        if monthly.value is None:
            monthly = self._derived_rate() or monthly
        values = {
            "lender": self.ev("lender"),
            "contract_number": self.ev("contract_number"),
            "principal": self.ev("principal"),
            "nominal_rate": self.ev("nominal_rate"),
            "monthly_payment": monthly,
            "fixed_until": self.ev("fixed_until"),
            "follow_up_rate": self.ev("follow_up_rate"),
            "disbursed_on": self.ev("disbursed_on"),
            "first_payment_on": self.ev("first_payment_on"),
            "account_iban": self.ev("account_iban"),
            "signed_on": self.ev("signed_on"),
        }
        interest_only = self._interest_only()
        loan = _match_loan(self.x, self.ctx.loans)
        if loan is None:
            fields = {
                f: v for f, v in values.items() if f not in _LOAN_OPTIONAL or v.value is not None
            }
            if interest_only is not None:
                fields["interest_only_before_first_payment"] = interest_only
            self.add("loan", "loan", "create", fields)
            # The new loan's card asks for both, stated or not (D132).
            for name in ("lender", "contract_number"):
                self.carried[name] = CarriedField(key="loan", field=name)
        else:
            # LoanPatch has no signed_on: the contract date is set when the loan is created.
            keys = ("lender", "contract_number", "signed_on")
            fields = {f: v for f, v in values.items() if f not in keys and v.value is not None}
            # The one term the context holds: proposed only when it changes false to true.
            if interest_only is not None and not loan.interest_only_before_first_payment:
                fields["interest_only_before_first_payment"] = interest_only
            self.add("loan", "loan", "patch" if fields else "unchanged", fields, target_id=loan.id)
        self.link("link:loan", None if loan is None else loan.id)

    def _interest_only(self) -> ExtractedValue | None:
        """interest_only_before_first_payment = true, derived from the contract's clause, when
        the clause says only interest is paid until repayment starts; else None (not
        proposed, the loan keeps false). Code decides, not the model (principle 1)."""
        clause = self.ev("interest_only_clause")
        if clause.value is None or not _INTEREST_ONLY.search(_norm(clause.value)):
            return None
        return _derived(True, [("interest_only_clause", clause)])

    def _derived_rate(self) -> ExtractedValue | None:
        """Rate = principal x (Sollzins + anfängliche Tilgung) / 12, in cents, half up, when
        the contract states only the Tilgungssatz."""
        names = ("principal", "nominal_rate", "initial_repayment_rate")
        inputs = [(n, self.ev(n)) for n in names]
        numbers = [_decimal(v.value) for _, v in inputs]
        if any(n is None for n in numbers):
            return None
        principal, rate, repayment = numbers
        value = (principal * (rate + repayment) / 12).quantize(_CENTS, rounding=ROUND_HALF_UP)
        return _derived(str(value) if value > 0 else None, inputs)

    # -- Mietvertrag (step 2, gated by ai_tenant_documents)

    def lease(self) -> None:
        for n in (1, 2):
            if self.stated(f"tenant_{n}_full_name"):
                self._tenant(n)
        unit, exact = self._lease_unit()
        tenancy = None
        if unit is not None:
            start = self.ev("start_date").value
            tenancy = _first(
                t
                for t in self.ctx.tenancies
                if t.unit_id == unit.id and start is not None and t.start_date.isoformat() == start
            )
        unit_value = _derived(
            None if unit is None else str(unit.id),
            [(n, self.ev(n)) for n in ("street", "house_number", "postal_code", "unit_label")],
        )
        if not exact and unit_value.confidence == "high":
            # Found inside a longer label, not equal to it: the review should look twice.
            unit_value = unit_value.model_copy(update={"confidence": "medium"})
        rent_model = _derived(
            _rent_model(self.ev("rent_clause").value), [("rent_clause", self.ev("rent_clause"))]
        )
        deposit_form = self._deposit_form()
        if tenancy is None:
            fields = {"unit_id": unit_value, "start_date": self.ev("start_date")}
            for name in ("signed_on", "fixed_term_until"):
                if self.stated(name):
                    fields[name] = self.ev(name)
            fields["rent_model"] = rent_model
            if self.stated("deposit_amount"):
                fields["deposit_amount"] = self.ev("deposit_amount")
            if deposit_form is not None:
                fields["deposit_form"] = deposit_form
            self.add("tenancy", "tenancy", "create", fields)
            self._initial_rent()
        else:
            fields = {}
            if self.stated("fixed_term_until"):
                fields["fixed_term_until"] = self.ev("fixed_term_until")
            if self.stated("rent_clause"):
                fields["rent_model"] = rent_model
            if self.stated("deposit_amount"):
                fields["deposit_amount"] = self.ev("deposit_amount")
            if deposit_form is not None:
                fields["deposit_form"] = deposit_form
            mode = "patch" if fields else "unchanged"
            self.add("tenancy", "tenancy", mode, fields, target_id=tenancy.id)
        self.link("link:tenancy", None if tenancy is None else tenancy.id)

    def _tenant(self, n: int) -> None:
        name = self.ev(f"tenant_{n}_full_name")
        contact = {
            field: self.ev(f"tenant_{n}_{field}")
            for field in ("email", "phone", "iban")
            if self.stated(f"tenant_{n}_{field}")
        }
        existing = _first(
            t
            for t in self.ctx.tenants
            if t.full_name is not None and _norm(t.full_name) == _norm(name.value)
        )
        key = f"tenant:{n}"
        if existing is None:
            self.add(key, "tenant", "create", {"full_name": name} | contact)
        else:
            mode = "patch" if contact else "unchanged"
            self.add(key, "tenant", mode, contact, target_id=existing.id)

    def _deposit_form(self) -> ExtractedValue | None:
        """The form the deposit clause names; else, when the lease states a deposit amount
        above 0, a money deposit (cash_account), derived from the amount and the clause: a
        lease that names no Bürgschaft asks for money ("eine Kaution in Höhe von 1.000,00 €
        ... zu überweisen", Luis's room lease, 7 Nov 2026), and a deposit amount with the
        form "none" is not a valid tenancy. The derived form is low confidence whenever the
        clause named no form (the code inferred it, review 7 Nov 2026). An amount that is not
        a number ("drei Nettokaltmieten") proposes the form as missing, so the form asks for
        it next to the amount. None when neither is stated."""
        clause, amount = self.ev("deposit_clause"), self.ev("deposit_amount")
        mapped = _mapped(clause, _deposit_form)
        if mapped.value is not None:
            return mapped
        inputs = [("deposit_amount", amount), ("deposit_clause", mapped)]
        value = _decimal(amount.value)
        if value is not None and value > 0:
            return _derived(DepositForm.CASH_ACCOUNT.value, inputs)
        if amount.value is not None and value is None:
            return _derived(None, inputs)
        return mapped if clause.value is not None else None

    def _lease_unit(self) -> tuple[ExistingUnit | None, bool]:
        """The tenancy's unit, and whether it was found exactly (an equal label, or the
        property's only unit); a unit found inside a longer label is not exact."""
        prop = _resolve_property(self.x, self.ctx)
        if prop is None:
            return None, True
        units = [u for u in self.ctx.units if u.property_id == prop.id]
        label = self.ev("unit_label").value
        if label is not None:
            return _unit_by_label(units, str(label))
        return (units[0] if len(units) == 1 else None), True

    def _initial_rent(self) -> None:
        fields = {"cold_rent": self.ev("cold_rent")}
        op, heat = self.ev("operating_costs_prepayment"), self.ev("heating_costs_prepayment")
        if op.value is not None and heat.value is not None:
            a, b = _decimal(op.value), _decimal(heat.value)
            total = None if a is None or b is None else str((a + b).quantize(_CENTS))
            fields["operating_costs_prepayment"] = _derived(
                total,
                [("operating_costs_prepayment", op), ("heating_costs_prepayment", heat)],
            )
        elif op.value is not None:
            fields["operating_costs_prepayment"] = op
        elif heat.value is not None:
            fields["operating_costs_prepayment"] = _derived(
                heat.value, [("heating_costs_prepayment", heat)]
            )
        if self.stated("start_date"):
            fields["effective_from"] = self.ev("start_date")
        self.add("rent", "rent_schedule", "create", fields)

    # -- Übergabeprotokoll (step 2)

    def handover_protocol(self) -> None:
        prop = _resolve_property(self.x, self.ctx)
        on = _iso_date(self.ev("document_date").value)
        if prop is None or on is None:
            return
        units = {u.id for u in self.ctx.units if u.property_id == prop.id}
        near = sorted(
            (abs((t.start_date - on).days), t.id)
            for t in self.ctx.tenancies
            if t.unit_id in units and abs((t.start_date - on).days) <= _HANDOVER_WINDOW_DAYS
        )
        if near:
            self.link("link:tenancy", near[0][1])

    # -- Kautionsnachweis (step 2, D42)

    def deposit_record(self) -> None:
        """A deposit_received record when amount and date are stated and exactly one tenancy
        is found: the one whose agreed Kaution equals the amount, else the workspace's only
        tenancy. Several or none: typing only, with a note. A receipt already recorded on
        that tenancy with the same amount and date: unchanged."""
        amount, received_on = self.ev("amount"), self.ev("received_on")
        on = _iso_date(received_on.value)
        if _decimal(amount.value) is None or on is None:
            return
        tenancies = self.ctx.tenancies
        by_amount = [
            t
            for t in tenancies
            if t.deposit_amount is not None and _same(amount.value, _json(t.deposit_amount))
        ]
        if len(by_amount) == 1:
            tenancy = by_amount[0]
            tenancy_value = _derived(str(tenancy.id), [("amount", amount)])
        elif not by_amount and len(tenancies) == 1:
            # Nothing in the document points at it: the only candidate, at low confidence.
            tenancy = tenancies[0]
            tenancy_value = _code_value(str(tenancy.id), "low")
        else:
            self.records[0].notes.append(
                _DEPOSIT_SEVERAL_NOTE if by_amount or tenancies else _DEPOSIT_NONE_NOTE
            )
            return
        fields = {"amount": amount, "received_on": received_on}
        recorded = sorted(
            (
                r
                for r in tenancy.deposit_receipts
                if r.received_on == on and _same(amount.value, _json(r.amount))
            ),
            key=lambda r: r.amount,
        )
        if recorded:
            current = {"amount": _json(recorded[0].amount), "received_on": on.isoformat()}
            self.add(
                "deposit_received",
                "deposit_received",
                "unchanged",
                fields,
                target_id=tenancy.id,
                current=current,
            )
        else:
            fields["tenancy_id"] = tenancy_value
            self.add("deposit_received", "deposit_received", "create", fields)
        self.link("link:tenancy", tenancy.id)

    # -- the document catalogue (D130, D131, §6.2.1)

    def catalogue_link(self) -> None:
        """The one link the subtype's group proposes (GENERAL_LINK), through the existing
        matchers: link:property by address (else the only property, as the
        Grundsteuerbescheid), link:loan by lender and contract number (else the only loan, as
        the Zinsbescheinigung), link:tenancy by address and a date within the tenancy. A
        Selbstauskunft and a Betriebskostenabrechnung (_TENANCY_BY_HAND) link the property by
        address and ask for the tenancy by hand. No match: typing only, with a note; an
        address read that is no property's links nothing (address_unmatched). A subtype
        outside the groups (income tax, household finances, none) proposes typing only.
        Never a record."""
        subtype = self.ctx.classification.subtype
        group = GENERAL_LINK.get(subtype) if subtype is not None else None
        if group == "property":
            prop = _resolve_property(self.x, self.ctx)
            if prop is not None:
                # D139: one Wohnung (ETW, EFH, as tax._common.is_flat); an MFH's flats are
                # its units, so a building's list proposes nothing there.
                if (
                    subtype is DocumentSubtype.BUILDING_RECORDS
                    and prop.kind is not PropertyKind.MFH
                ):
                    self._living_area(prop)
                self.link("link:property", prop.id)
                if subtype is DocumentSubtype.APPRAISAL:
                    self._useful_life(prop)  # D147: after its link, as a certificate's figure
            else:
                self.records[0].notes.append(_NO_PROPERTY_NOTE)
        elif group == "loan":
            loan = _match_loan(self.x, self.ctx.loans)
            if loan is None and len(self.ctx.loans) == 1:
                loan = self.ctx.loans[0]
            if loan is not None:
                self.link("link:loan", loan.id)
            else:
                self.records[0].notes.append(_NO_LOAN_NOTE)
        elif subtype in _TENANCY_BY_HAND:
            # A Selbstauskunft comes before the tenancy and a Betriebskostenabrechnung spans
            # a year in which tenants may change: the date finds no tenancy (review, F3).
            prop = _resolve_property(self.x, self.ctx)
            if prop is not None:
                self.link("link:property", prop.id)
            else:
                self.records[0].notes.append(_NO_PROPERTY_NOTE)
            self.records[0].notes.append(_TENANCY_BY_HAND_NOTE)
        elif group == "tenancy":
            tenancy = self._tenancy_at_date()
            if tenancy is not None:
                self.link("link:tenancy", tenancy)
            else:
                self.records[0].notes.append(_NO_TENANCY_NOTE)

    def _living_area(self, prop: ExistingProperty) -> None:
        """D139: a Wohnflächenberechnung's dwelling as the property's living_area_m2. The
        household's row is (a) the only one, else (b) the one row whose MEA equals the flat's
        (a unit's on the property, or the Kaufvertrag's), else (c) the one row whose label
        equals a unit's or the Kaufvertrag's WE label (_norm); found, a patch or unchanged
        record by _match_record, derived from [dwellings]. Otherwise (d): a patch with the
        field empty, current the stored area, and every row offered as an option (one click
        in the review). An empty field is not sent, so applying it unclicked changes no
        stored area. Rows are never summed."""
        read = self.ev("dwellings")
        rows = parse_dwellings(read.value)
        if not rows:
            if read.value:
                self.records[0].notes.append(_NO_DWELLING_NOTE)
            return
        only = _lone_dwelling(read.value)
        picked = _pick_dwelling(rows, prop, self.ctx.units, only=only)
        if picked is not None:
            row, why = picked
            note = _DWELLING_TAKEN_NOTE.format(row=_dwelling_label(row), why=_DWELLING_WHY[why])
            self._match_record(
                "property",
                "property",
                prop.id,
                {"living_area_m2": _derived(row.area, [("dwellings", read)])},
                _property_current(prop),
                notes=[note + _LIVING_AREA_USE],
            )
            return
        stored = _property_current(prop)["living_area_m2"]
        kept = _DWELLING_KEPT_NOTE if stored is not None else ""
        checks = ["cited"] if read.source is not None else []
        empty = ExtractedValue(value=None, source=read.source, checks=checks, confidence="low")
        self.add(
            "property",
            "property",
            "patch",
            {"living_area_m2": empty},
            target_id=prop.id,
            current={"living_area_m2": stored},
            notes=[_DWELLING_PICK_NOTE.format(count=len(rows)) + kept + _LIVING_AREA_USE],
        )
        self.records[-1].options["living_area_m2"] = [
            FieldOption(value=row.area, label=_dwelling_label(row)) for row in rows
        ]

    def _useful_life(self, prop: ExistingProperty) -> None:
        """D147: a Gutachten's remaining useful life as tax_input:useful_life (phase 3 D79),
        computed by code. The life counts from the handover (purchase_date, the AfA start): a
        Stichtag before it takes off the whole months between them (_whole_months); on or after
        it in the purchase year, or none read, the figure as read. A Stichtag in a later year
        than the handover starts the life in that year, where D79 restarts it. Rounded up to
        whole years (the longer life is the conservative one). Not shorter than the statutory
        life left on 1 January of that year (the service's check), or nothing left: no record,
        a note on the typing record. Not stated: nothing."""
        life_read = self.ev("remaining_useful_life")
        stichtag_read = self.ev("valuation_date")
        if life_read.value is None:
            return
        life = _decimal(life_read.value)
        if life is None or life <= 0:
            self.records[0].notes.append(_LIFE_UNREADABLE_NOTE)
            return
        handover = prop.purchase_date
        stichtag = _iso_date(stichtag_read.value)
        months = life * 12
        year = handover.year
        facts = {
            "life": _years_de(life),
            "stichtag": "" if stichtag is None else f"{stichtag:%d.%m.%Y}",
            "handover": f"{handover:%d.%m.%Y}",
        }
        if stichtag is not None and stichtag < handover:
            elapsed = _whole_months(stichtag, handover)
            months -= elapsed
            facts["elapsed"] = _span_de(elapsed)
            if months <= 0:
                self.records[0].notes.append(_LIFE_USED_UP_NOTE.format(**facts))
                return
            note = _LIFE_BEFORE_NOTE
        elif stichtag is None:
            note = _LIFE_NO_STICHTAG_NOTE
        elif stichtag.year > handover.year:
            year = stichtag.year
            note = _LIFE_LATER_NOTE
        else:
            note = _LIFE_AFTER_NOTE
        years = int((months / 12).to_integral_value(rounding=ROUND_CEILING))
        rate = tax_registry.afa_rule_set(year).afa_rate(prop.build_year)
        left = statutory_months_left(rate, handover, year)
        facts |= {"years": _years_de(Decimal(years)), "year": year}
        statutory = _span_de(int(left))
        if years * 12 >= left:
            self.records[0].notes.append(
                _LIFE_NOT_SHORTER_NOTE.format(**facts, statutory=statutory)
            )
            return
        text = note.format(**facts)
        if years * 12 != months:
            text += _LIFE_ROUNDED
        text += _LIFE_USE.format(year=year, statutory=statutory)
        inputs = [("remaining_useful_life", life_read), ("valuation_date", stichtag_read)]
        self.add(
            "tax_input:useful_life",
            "tax_input",
            "create",
            {"tax_year": _derived(year, inputs), "value.years": _derived(years, inputs)},
            notes=[text],
        )

    def _tenancy_at_date(self) -> UUID | None:
        """The one tenancy on the property found by address whose term holds the document's
        date (start ≤ date ≤ end, open while it runs); none or several: None."""
        prop = _resolve_property(self.x, self.ctx)
        on = _iso_date(self.ev("document_date").value)
        if prop is None or on is None:
            return None
        units = {u.id for u in self.ctx.units if u.property_id == prop.id}
        fits = [
            t.id
            for t in self.ctx.tenancies
            if t.unit_id in units
            and t.start_date <= on
            and (t.end_date is None or on <= t.end_date)
        ]
        return fits[0] if len(fits) == 1 else None

    # -- the two certificates (phase 3 §9)

    def zinsbescheinigung(self) -> None:
        """link:loan by lender and contract number, else the only loan; and the certified
        interest, proposed even without a loan (the user picks it in the review)."""
        loan = _match_loan(self.x, self.ctx.loans)
        if loan is not None:
            loan_value = _derived(
                str(loan.id),
                [("lender", self.ev("lender")), ("contract_number", self.ev("contract_number"))],
            )
        elif len(self.ctx.loans) == 1:
            loan = self.ctx.loans[0]
            loan_value = _code_value(str(loan.id), "low")  # nothing in the document names it
        else:
            loan_value = ExtractedValue.missing()
        if loan is not None:
            self.link("link:loan", loan.id)
        self.add(
            "tax_input:interest_certified",
            "tax_input",
            "create",
            {
                "tax_year": self.ev("tax_year"),
                "loan_id": loan_value,
                "value.amount": self.ev("interest_paid"),
            },
        )

    def hausgeldabrechnung(self) -> None:
        """link:property by address; the Rücklage the WEG spent, only for a property found
        (a tax input belongs to one)."""
        prop = _resolve_property(self.x, self.ctx)
        if prop is None:
            return
        self.link_property(prop)
        self.add(
            "tax_input:ruecklage_spent",
            "tax_input",
            "create",
            {"tax_year": self.ev("tax_year"), "value.amount": self.ev("ruecklage_spent")},
        )

    # -- the two pay documents (phase 4b §8.2)

    def wage_certificate(self) -> None:
        """link:owner and return_input:wage_certificate with every certificate field."""
        owner = self._owner()
        tax_year = self._pay_tax_year()
        tax_class = self.ev("tax_class")
        fields = {
            "owner_id": owner,
            "tax_year": tax_year,
            "value.period_from": self.ev("period_from"),
            "value.period_to": self.ev("period_to"),
            "value.employer": self.ev("employer_name"),
            "value.tax_class": _mapped(tax_class, _tax_class_number),
            "value.tax_class_factor": _mapped(tax_class, _tax_class_factor),
            "value.church_tax_marked": _mapped(self.ev("church_tax_mark"), _church_tax_marked),
            "value.child_allowance_count": self.ev("child_allowance_count"),
        }
        for name in CERTIFICATE_AMOUNTS:
            fields[f"value.{name}"] = self.ev(name)
        notes = []
        unread = [name for name in CERTIFICATE_AMOUNTS if fields[f"value.{name}"].value is None]
        if unread:
            notes.append(
                f"Nicht gelesen: {', '.join(CERTIFICATE_LINES[n] for n in unread)}. Steht dort "
                "ein Betrag, bitte eintragen; ein leeres Feld wird als 0 übernommen und als "
                "nicht gelesen vermerkt."
            )
        replaced = self._replaced("wage_certificate", owner, tax_year, fields)
        if replaced is not None and replaced.document_id == self.ctx.document_id:
            notes.append(
                "Diese Lohnsteuerbescheinigung ist schon übernommen; geänderte Werte ersetzen "
                "den Eintrag."
            )
        elif replaced is not None:
            notes.append(
                "Für diesen Zeitraum gibt es von diesem Arbeitgeber schon eine "
                "Lohnsteuerbescheinigung; diese ersetzt sie (eine berichtigte Bescheinigung)."
            )
        notes += self._pay_row_notes("wage_certificate", owner, tax_year, fields)
        self.add("return_input:wage_certificate", "return_input", "create", fields, notes=notes)

    def payslip(self) -> None:
        """link:owner and return_input:payslip: the month's and the year-to-date figures,
        months_remaining_paid = 12 − month, the gross projection in a note (D105)."""
        owner = self._owner()
        tax_year = self._pay_tax_year()
        month = _mapped(self.ev("period_month"), _month_start)
        on = _iso_date(month.value)
        tax_class = self.ev("tax_class")
        fields = {
            "owner_id": owner,
            "tax_year": tax_year,
            "value.period_month": month,
            "value.employer": self.ev("employer_name"),
            "value.tax_class": _mapped(tax_class, _tax_class_number),
            "value.tax_class_factor": _mapped(tax_class, _tax_class_factor),
            "value.months_remaining_paid": _derived(
                None if on is None else 12 - on.month, [("period_month", month)]
            ),
        }
        for figure in PAYSLIP_FIGURES:
            fields[f"value.{figure}.month"] = self.ev(f"{figure}_month")
            fields[f"value.{figure}.ytd"] = self.ev(f"{figure}_ytd")
        notes = []
        no_month = [f for f in PAYSLIP_FIGURES if fields[f"value.{f}.month"].value is None]
        if no_month:
            notes.append(
                f"Nicht gelesen (Monat): {', '.join(PAYSLIP_LINES[f] for f in no_month)}. Steht "
                "dort ein Betrag, bitte eintragen; ein leeres Monatsfeld wird als 0 übernommen."
            )
        no_ytd = [f for f in PAYSLIP_FIGURES if fields[f"value.{f}.ytd"].value is None]
        if no_ytd:
            notes.append(
                f"Nicht gelesen (Jahreswerte): {', '.join(PAYSLIP_LINES[f] for f in no_ytd)}. "
                "Steht dort ein Betrag, bitte eintragen; ein leeres Jahresfeld bleibt leer und "
                "wird hochgerechnet."
            )
        if on is not None:
            notes.append(
                _projection_note(on, fields["value.gross.month"], fields["value.gross.ytd"])
            )
            if self._replaced("payslip", owner, tax_year, fields) is not None:
                notes.append(
                    f"Für {MONTH_NAMES[on.month - 1]} {on.year} gibt es von diesem Arbeitgeber "
                    "schon eine Gehaltsabrechnung; diese ersetzt sie."
                )
        notes += self._pay_row_notes("payslip", owner, tax_year, fields)
        self.add("return_input:payslip", "return_input", "create", fields, notes=notes)

    def _owner(self) -> ExtractedValue:
        """link:owner and the owner_id value: the one owner of this workspace whose name is
        the employee's (by name tokens); else no target and no value (the user picks). The
        context holds this workspace's owners only."""
        name = self.ev("employee_name")
        owner, exact = _match_owner(name.value, self.ctx.owners)
        self.link("link:owner", None if owner is None else owner.id)
        value = _derived(None if owner is None else str(owner.id), [("employee_name", name)])
        if owner is not None and not exact and value.confidence == "high":
            value = value.model_copy(update={"confidence": "medium"})
        return value

    def _pay_tax_year(self) -> ExtractedValue:
        """A certificate's stated year, else the year of its period; a payslip's month's."""
        if self.kind is ExtractionKind.LOHNSTEUERBESCHEINIGUNG:
            if self.stated("tax_year"):
                return self.ev("tax_year")
            sources = [(n, self.ev(n)) for n in ("period_from", "period_to")]
        else:
            sources = [("period_month", _mapped(self.ev("period_month"), _month_start))]
        for name, source in sources:
            on = _iso_date(source.value)
            if on is not None:
                return _derived(on.year, [(name, source)])
        return ExtractedValue.missing()

    def _replaced(
        self,
        kind: str,
        owner: ExtractedValue,
        tax_year: ExtractedValue,
        fields: dict[str, ExtractedValue],
    ) -> ExistingPayRow | None:
        """The current row the proposed one would replace, as the apply decides it."""
        if owner.value is None or tax_year.value is None:
            return None
        return replaced_pay_row(
            kind,
            self.ctx.pay_rows,
            owner_id=UUID(str(owner.value)),
            tax_year=int(tax_year.value),
            document_id=self.ctx.document_id,
            value={name: v.value for name, v in fields.items()},
        )

    def _pay_row_notes(
        self,
        kind: str,
        owner: ExtractedValue,
        tax_year: ExtractedValue,
        fields: dict[str, ExtractedValue],
    ) -> list[str]:
        """This document already recorded under another owner or year (the apply refuses it
        until that row is withdrawn), and the owner's rows from an employer spelled
        differently but likely the same (one name's words all in the other's). Notes only:
        no proposed value changes."""
        notes = []
        names = {o.id: o.full_name for o in self.ctx.owners}
        owner_id = None if owner.value is None else UUID(str(owner.value))
        for row in self.ctx.pay_rows:
            if row.kind != kind or row.document_id != self.ctx.document_id:
                continue
            if row.owner_id != owner_id or row.tax_year != tax_year.value:
                notes.append(
                    f"Dieses Dokument ist schon für {names.get(row.owner_id, 'einen Eigentümer')}"
                    f" im Jahr {row.tax_year} übernommen. Für einen anderen Eigentümer oder ein "
                    "anderes Jahr zuerst jenen Eintrag zurückziehen."
                )
        employer = fields["value.employer"].value
        if owner_id is None or not employer:
            return notes
        key, words = employer_key(employer), _employer_words(employer)
        seen = set()
        for row in self.ctx.pay_rows:
            if row.owner_id != owner_id or employer_key(row.employer) in seen | {key}:
                continue
            stored = _employer_words(row.employer)
            if words and stored and (words <= stored or stored <= words):
                seen.add(employer_key(row.employer))
                notes.append(
                    f"Für diesen Eigentümer gibt es schon Zeilen vom Arbeitgeber "
                    f"'{row.employer}'; prüfe, ob es derselbe Arbeitgeber ist."
                )
        return notes

    def income_tax_assessment(self) -> None:
        """income_input:bescheid_zve: the year and the zvE; the notes of D129 (§8.4)."""
        tax_year = self.ev("tax_year")
        fields = {"tax_year": tax_year, "value.amount": self.ev("taxable_income")}
        notes = []
        if fields["value.amount"].value is None:
            notes.append(_ZVE_UNREAD_NOTE)
        year = tax_year.value
        for row in self.ctx.bescheid_rows:
            if row.document_id == self.ctx.document_id and row.tax_year != year:
                notes.append(
                    f"Dieser Bescheid ist schon als zvE für {row.tax_year} übernommen. Für ein "
                    "anderes Jahr zuerst jenen Eintrag zurückziehen."
                )
        replaced = _first(
            sorted(
                (r for r in self.ctx.bescheid_rows if year is not None and r.tax_year == year),
                key=lambda r: str(r.id),
            )
        )
        if replaced is not None and replaced.document_id == self.ctx.document_id:
            notes.append(
                "Dieser Bescheid ist schon übernommen; ein geänderter Betrag ersetzt den Eintrag."
            )
        elif replaced is not None and replaced.document_date is not None:
            notes.append(
                f"Für {year} gibt es schon einen Bescheid; dieser ersetzt den Bescheid vom "
                f"{replaced.document_date:%d.%m.%Y} (ein Änderungsbescheid)."
            )
        elif replaced is not None:
            notes.append(
                f"Für {year} gibt es schon einen Bescheid; dieser ersetzt den bisher "
                "übernommenen Bescheid (ein Änderungsbescheid)."
            )
        assessment = self.ev("assessment_type").value
        if assessment is not None and not _JOINT_ASSESSMENT.search(str(assessment).casefold()):
            notes.append(_NOT_JOINT_NOTE)
        addressee = self.ev("addressee").value
        if (
            addressee is not None
            and self.ctx.owners
            and not _names_an_owner(addressee, self.ctx.owners)
        ):
            notes.append(_NOT_THE_OWNERS_NOTE)
        self.add("income_input:bescheid_zve", "income_input", "create", fields, notes=notes)


# --- Matching existing records ----------------------------------------------------------


def _norm(value: Any) -> str:
    """Casefold, whitespace collapsed."""
    return " ".join(str(value).split()).casefold()


_LABEL_TOKEN = re.compile(r"[^\W\d_]+|\d+")
_LABEL_FILLERS = frozenset({"nr", "no", "nummer"})


def _label_key(label: str) -> tuple[str, ...]:
    """A unit label's tokens: runs of letters or of digits, casefolded, a number without its
    leading zeros, the filler words nr / no / nummer dropped ("WE03" -> we 3, "Zimmer Nr. 1"
    -> zimmer 1). Punctuation and brackets only separate."""
    tokens = []
    for token in _LABEL_TOKEN.findall(label.casefold()):
        if token.isdigit():
            tokens.append(token.lstrip("0") or "0")
        elif token not in _LABEL_FILLERS:
            tokens.append(token)
    return tuple(tokens)


def _contains(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    n = len(needle)
    return n > 0 and any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1))


_ROOM = "zimmer"


def _may_be_inside(unit_key: tuple[str, ...], label_key: tuple[str, ...]) -> bool:
    """Whether a unit key may match inside a longer label: it has both a word and a number
    (a bare "1" is inside "Wohnung 1. OG", a bare "EG" or "Zimmer" names no one unit), and a
    label naming a room only matches a unit whose key names one too (a room lease is never
    booked on the flat: a tenancy cannot move to another unit)."""
    has_word = any(not t.isdigit() for t in unit_key)
    has_number = any(t.isdigit() for t in unit_key)
    if not (has_word and has_number):
        return False
    return _ROOM not in label_key or _ROOM in unit_key


def _unit_by_label(units: Sequence[ExistingUnit], label: str) -> tuple[ExistingUnit | None, bool]:
    """The unit a lease's label names, among the property's units (fixture prefill.yaml,
    "lease unit"), and whether its label was equal: the one unit whose key equals the
    label's; else the one unit whose key occurs as a run of whole tokens inside it ("Zimmer
    Nr. 1 (WE 3, Zimmer 1)" -> Zimmer 1, never Zimmer 10), within _may_be_inside. Several
    candidates or none: None, never a guess."""
    key = _label_key(label)
    equal = [u for u in units if _label_key(u.label) == key]
    if equal:
        return (equal[0], True) if len(equal) == 1 else (None, True)
    inside = [
        u
        for u in units
        if _may_be_inside(_label_key(u.label), key) and _contains(key, _label_key(u.label))
    ]
    return (inside[0], False) if len(inside) == 1 else (None, False)


# The parts of a split bill a record books (D135): the purchase part and the deductible ones.
_BOOKED_FROM_LINES = frozenset({AllocationCategory.ACQUISITION_COST, *PRIVATE_COST_CATEGORIES})
_LABEL_MAX = 120  # PrivateCostValue.label


def _pick_dwelling(
    rows: Sequence[Dwelling], prop: ExistingProperty, units: Sequence[ExistingUnit], *, only: bool
) -> tuple[Dwelling, str] | None:
    """The household's dwelling of a Wohnflächenberechnung and why (D139): the document's
    only row; else the one row whose MEA equals a known MEA of the flat (a unit's on the
    property, the Kaufvertrag's), compared as ratios; else the one row whose label equals a
    known label (a unit's, the Kaufvertrag's WE label) by _norm. Two rows fitting one key,
    or none: None."""
    if only and len(rows) == 1:
        return rows[0], "only"
    own = [u for u in units if u.property_id == prop.id]
    meas = [u.mea.model_dump() for u in own if u.mea is not None]
    if prop.contract_mea is not None:
        meas.append(prop.contract_mea.model_dump())
    by_mea = [r for r in rows if r.mea is not None and any(_same(r.mea, m) for m in meas)]
    if by_mea:
        return (by_mea[0], "mea") if len(by_mea) == 1 else None
    labels = {_norm(u.label) for u in own}
    if prop.contract_unit_label is not None:
        labels.add(_norm(prop.contract_unit_label))
    by_label = [r for r in rows if _norm(r.label) in labels]
    return (by_label[0], "label") if len(by_label) == 1 else None


def _dwelling_label(row: Dwelling) -> str:
    """ "WE 3 · 2. OG links · MEA 85/1000 · 61,40 m²": the parts the row states."""
    parts = [row.label]
    if row.location is not None:
        parts.append(row.location)
    if row.mea is not None:
        parts.append(f"MEA {row.mea['numerator']}/{row.mea['denominator']}")
    parts.append(row.area.replace(".", ",") + " m²")
    return " · ".join(parts)


def _label_value(title: ExtractedValue, sender: ExtractedValue) -> ExtractedValue:
    """A private cost's label: the document's title, else its sender, cut to 120 characters
    (a copy, so the typing record keeps its own hand fields, D132); neither read, proposed
    empty for the user to type."""
    for read in (title, sender):
        if isinstance(read.value, str) and read.value.strip():
            return read.model_copy(update={"value": read.value[:_LABEL_MAX].rstrip()})
    return ExtractedValue.missing()


def names_financing(*texts: Any) -> bool:
    """Whether any of the texts names a financing term (Grundschuld, Hypothek, Darlehen),
    anywhere, case-insensitive (D35). Public for services.matching, which decides a
    financing invoice exactly as the proposal does: an invoice by its subject or title, a
    Bescheid by its title."""
    return any(
        term in _norm(text) for text in texts if text is not None for term in _FINANCING_TERMS
    )


def _first[T](items: Iterable[T]) -> T | None:
    """The match with the smallest id, so the result does not depend on list order."""
    found = sorted(items, key=lambda item: item.id)
    return found[0] if found else None


def _address_of(extraction: _Typing) -> tuple[str, str, str | None] | None:
    """The street and house number keys and the postal code, or None when the document
    states no street or no house number. The postal code counts only when its quote
    contains it (`value_in_quote`): a contract may name the flat by its land register entry
    and street alone (Luis's real Kaufvertrag, 7 Nov 2026), and an unverified code must
    neither find nor block a match."""
    street, number = (_stated_value(extraction, name) for name in ("street", "house_number"))
    if street is None or number is None:
        return None
    postal = getattr(extraction, "postal_code", None)
    verified = (
        isinstance(postal, ExtractedValue)
        and postal.value is not None
        and "value_in_quote" in postal.checks
    )
    return (
        _street_key(street),
        _house_number_key(number),
        _norm(postal.value) if verified else None,
    )


def _stated_value(extraction: _Typing, name: str) -> Any:
    value = getattr(extraction, name, None)
    return value.value if isinstance(value, ExtractedValue) else None


def _match_property(
    extraction: _Typing, properties: Sequence[ExistingProperty]
) -> ExistingProperty | None:
    """By street + house number (compared by key, so "Lindenstr. 12" finds "Lindenstraße
    12") and the postal code. Without a verified postal code: the city instead when the
    document states one, and only when exactly one property fits, so nothing is guessed."""
    address = _address_of(extraction)
    if address is None:
        return None
    street, number, postal = address
    fits = [
        p
        for p in properties
        if (_street_key(p.address.street), _house_number_key(p.address.house_number))
        == (street, number)
    ]
    if postal is not None:
        return _first(p for p in fits if _norm(p.address.postal_code) == postal)
    city = _stated_value(extraction, "city")
    if city is not None:
        fits = [p for p in fits if _norm(p.address.city) == _norm(city)]
    return fits[0] if len(fits) == 1 else None


def address_unmatched(extraction: _Typing) -> bool:
    """A general reading read an address that is none of the workspace's properties: its
    street, house number or postal code has a cited source but no value (the parser drops
    such a value, extraction_parse._unmatched_address). Only the general kinds produce it."""
    for name in ("street", "house_number", "postal_code"):
        value = getattr(extraction, name, None)
        if isinstance(value, ExtractedValue) and value.value is None and value.source is not None:
            return True
    return False


def _resolve_property(extraction: _Typing, context: PrefillContext) -> ExistingProperty | None:
    """The property a document belongs to: by its address when it states one, else the only
    property of the workspace. None when a general reading read an address that is no
    property's (address_unmatched): an owner's home is never a reason to link."""
    if address_unmatched(extraction):
        return None
    only = context.properties[0] if len(context.properties) == 1 else None
    address = _address_of(extraction)
    if address is None:
        return only
    found = _match_property(extraction, context.properties)
    # Without a verified postal code the address was never decisive: as before, the
    # workspace's only property when the street does not find it.
    return found if found is not None or address[2] is not None else only


def _match_loan(extraction: _Typing, loans: Sequence[ExistingLoan]) -> ExistingLoan | None:
    """By contract number (whitespace removed) and lender."""
    number = getattr(extraction, "contract_number", None)
    lender = getattr(extraction, "lender", None)
    if not isinstance(number, ExtractedValue) or number.value is None:
        return None
    if not isinstance(lender, ExtractedValue) or lender.value is None:
        return None
    wanted = (_compact(number.value), _norm(lender.value))
    return _first(
        loan
        for loan in loans
        if loan.contract_number is not None
        and (_compact(loan.contract_number), _norm(loan.lender)) == wanted
    )


def _compact(value: Any) -> str:
    return "".join(str(value).split()).casefold()


def _property_current(prop: ExistingProperty) -> dict[str, JsonValue]:
    d = prop.model_dump(mode="json")
    return {
        "kind": d["kind"],
        "address.street": d["address"]["street"],
        "address.house_number": d["address"]["house_number"],
        "address.postal_code": d["address"]["postal_code"],
        "address.city": d["address"]["city"],
        "build_year": d["build_year"],
        "notarized_on": d["notarized_on"],
        "purchase_date": d["purchase_date"],
        "purchase_price": d["purchase_price"],
        "living_area_m2": d["living_area_m2"],
    }


def _unit_current(unit: ExistingUnit) -> dict[str, JsonValue]:
    d = unit.model_dump(mode="json")
    return {"label": d["label"], "mea": d["mea"]}


def _same(proposed: JsonValue, existing: JsonValue) -> bool:
    """Equal as values: decimals by number, fractions by ratio, text casefolded."""
    if proposed == existing:
        return True
    if isinstance(proposed, dict) and isinstance(existing, dict):
        try:
            return (
                proposed["numerator"] * existing["denominator"]
                == existing["numerator"] * proposed["denominator"]
            )
        except (KeyError, TypeError):
            return False
    if isinstance(proposed, str) and isinstance(existing, str):
        a, b = _decimal(proposed), _decimal(existing)
        if a is not None and b is not None:
            return a == b
        return _norm(proposed) == _norm(existing)
    return False


def _same_amount(proposed: JsonValue, existing: Decimal) -> bool:
    """A read amount equal to a stored one within a cent (D145)."""
    read = _decimal(proposed)
    return read is not None and abs(read - existing) <= _CENTS


# --- Mapping and deriving values -------------------------------------------------------


def _code_value(value: JsonValue, confidence: ConfidenceLevel) -> ExtractedValue:
    """A value code set (the classifier's typing, a default): no source, validated."""
    return ExtractedValue(value=value, checks=["validates"], confidence=confidence)


def _mapped(source: ExtractedValue, mapping: Callable[[Any], Any]) -> ExtractedValue:
    """The extraction's value mapped to the target type, keeping its source, checks and
    confidence; a value that does not map is proposed as missing, its quote kept."""
    if source.value is None:
        return source
    mapped = mapping(source.value)
    if mapped is None:
        checks = ["cited"] if source.source is not None else []
        return ExtractedValue(value=None, source=source.source, checks=checks, confidence="low")
    value = mapped.value if hasattr(mapped, "value") else mapped
    return source.model_copy(update={"value": value})


def _derived(
    value: JsonValue | None, inputs: Sequence[tuple[str, ExtractedValue]]
) -> ExtractedValue:
    """Computed by code: no source, the inputs named, confidence the lowest of the inputs'
    (a missing input counts as low)."""
    levels = [v.confidence if v.value is not None else "low" for _, v in inputs]
    confidence = min(levels, key=_CONFIDENCE_ORDER.__getitem__) if levels else "low"
    if value is None:
        confidence = "low"
    return ExtractedValue(
        value=value,
        derived_from=[name for name, _ in inputs],
        checks=["derived", "validates"] if value is not None else ["derived"],
        confidence=confidence,
    )


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
        return None
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _iso_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _json(value: Any) -> JsonValue:
    return str(value) if isinstance(value, Decimal) else value


def _property_kind(text: Any) -> PropertyKind | None:
    if text is None:
        return None
    t = _norm(text)
    if t in {k.value for k in PropertyKind}:
        return PropertyKind(t)
    if any(w in t for w in ("wohnungseigentum", "eigentumswohnung", "teileigentum")):
        return PropertyKind.ETW
    if "einfamilienhaus" in t:
        return PropertyKind.EFH
    if "mehrfamilienhaus" in t:
        return PropertyKind.MFH
    return None


def _cost_kind(sender: Any) -> AcquisitionCostKind:
    t = _norm(sender)
    if "grunderwerbsteuer" in t:
        return AcquisitionCostKind.REAL_ESTATE_TRANSFER_TAX
    if "notar" in t:
        return AcquisitionCostKind.NOTARY
    if "grundbuch" in t or "amtsgericht" in t:
        return AcquisitionCostKind.LAND_REGISTRY
    if "makler" in t:
        return AcquisitionCostKind.BROKER
    return AcquisitionCostKind.OTHER


def _purchase_lines_kind(read: Any) -> AcquisitionCostKind | None:
    """The kind the bill's purchase lines name: a line naming the land register, or a land
    register entry of the purchase (Auflassungsvormerkung, Eigentumsumschreibung), is a
    Grundbuch cost. Only when every such line agrees; financing lines never decide it."""
    lines = parse_cost_lines(read)
    if not lines:
        return None
    kinds = set()
    for line in lines:
        if line.vat or _names(line.text, _FINANCING_TERMS):
            continue
        if _cost_kind(line.text) is AcquisitionCostKind.LAND_REGISTRY or _names(
            line.text, _LAND_REGISTRY_ENTRIES
        ):
            kinds.add(AcquisitionCostKind.LAND_REGISTRY)
        elif _names(line.text, _PURCHASE_TERMS):
            return None  # a purchase line of another kind (a Kaufvertrag fee): not decided here
    return kinds.pop() if len(kinds) == 1 else None


def _deposit_form(clause: Any) -> DepositForm | None:
    """The form a deposit clause names. "bar" counts only as a word, so "vereinbart" is not
    a cash deposit (7 Nov 2026)."""
    t = _norm(clause)
    if "bankbürgschaft" in t or "bankburgschaft" in t:
        return DepositForm.BANK_GUARANTEE
    if "bürgschaft" in t or "burgschaft" in t:
        return DepositForm.SURETY
    if any(
        w in t
        for w in (
            "barkaution",
            "geldkaution",
            "kautionskonto",
            "sparbuch",
            "barzahlung",
            "bareinzahlung",
            "barsicherheit",
            "bargeld",
        )
    ) or re.search(r"\bbar\b", t):
        return DepositForm.CASH_ACCOUNT
    return None


def _rent_model(clause: Any) -> str:
    t = "" if clause is None else _norm(clause)
    if "staffel" in t:
        return RentModel.STAFFEL.value
    if "index" in t:
        return RentModel.INDEX.value
    return RentModel.FIXED.value


def _ownership_share(clause: str, buyers: int) -> tuple[int, int] | None:
    """The share each buyer holds, when the clause is understood."""
    t = _norm(clause)
    if buyers == 2 and ("je zur hälfte" in t or "je 1/2" in t or "zu je 1/2" in t):
        return (1, 2)
    if "zu gleichen teilen" in t or "zu gleichen anteilen" in t:
        return (1, buyers)
    if buyers == 1 and "allein" in t:
        return (1, 1)
    return None


# --- The pay documents' values (phase 4b §8.2) ------------------------------------------

_PAY_KINDS = frozenset({ExtractionKind.LOHNSTEUERBESCHEINIGUNG, ExtractionKind.GEHALTSABRECHNUNG})
# D129: the Bescheid's notes. The app computes the joint splitting tariff only (D82, D83).
_JOINT_ASSESSMENT = re.compile(r"zusammen\s*-?\s*veranlag")
_NOT_JOINT_NOTE = (
    "Laut Bescheid ist das keine Zusammenveranlagung; die App rechnet mit dem "
    "Splittingtarif. Bitte prüfen, ob der Betrag trotzdem übernommen werden soll."
)
_NOT_THE_OWNERS_NOTE = (
    "Der Bescheid scheint nicht an die Eigentümer gerichtet (etwa der Einkommensnachweis "
    "eines Mieters). Bitte prüfen, bevor der Betrag übernommen wird."
)
_ZVE_UNREAD_NOTE = (
    "Das zu versteuernde Einkommen wurde nicht gelesen; bitte den Betrag aus dem Bescheid "
    "eintragen (die Zeile „zu versteuerndes Einkommen“)."
)
# The certificate's line of each amount, for the note naming the lines not read (§5.1).
CERTIFICATE_LINES: dict[str, str] = {
    "gross": "Nr. 3",
    "income_tax": "Nr. 4",
    "soli": "Nr. 5",
    "church_tax": "Nr. 6",
    "church_tax_spouse": "Nr. 7",
    "replacement_benefits": "Nr. 15",
    "short_time_allowance": "Nr. 15a",
    "employer_commute_tax_free": "Nr. 17",
    "employer_commute_flat_taxed": "Nr. 18",
    "pension_employer": "Nr. 22a",
    "pension_employee": "Nr. 23a",
    "pension_employee_professional": "Nr. 23b",
    "subsidy_health": "Nr. 24a",
    "subsidy_private_health": "Nr. 24b",
    "subsidy_care": "Nr. 24c",
    "health_employee": "Nr. 25",
    "care_employee": "Nr. 26",
    "unemployment_employee": "Nr. 27",
}
# The payslip's name of each figure, for the note naming the figures not read.
PAYSLIP_LINES: dict[str, str] = {
    "gross": "Steuer-Brutto",
    "income_tax": "Lohnsteuer",
    "soli": "Solidaritätszuschlag",
    "church_tax": "Kirchensteuer",
    "pension_employee": "Rentenversicherung",
    "health_employee": "Krankenversicherung",
    "care_employee": "Pflegeversicherung",
    "unemployment_employee": "Arbeitslosenversicherung",
    "tax_free_benefits": "steuerfreie Bezüge",
}
MONTH_NAMES = (
    "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
    "Oktober", "November", "Dezember",
)  # fmt: skip
_NAME_TOKEN = re.compile(r"[^\W\d_]+")
_NAME_FILLERS = frozenset({"dr", "prof", "herr", "frau", "von", "zu", "van", "der", "de"})
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6}
_FACTOR = re.compile(r"(?<![0-9])0[,.][0-9]{1,4}(?![0-9])")
_ARABIC_CLASS = re.compile(r"(?<![0-9.,])[1-6](?![0-9.,])")
_ROMAN_CLASS = re.compile(r"(?<![a-z])(vi|iv|v|iii|ii|i)(?![a-z])")
_NO_CHURCH_TAX = frozenset({"", "-", "--", "kein", "keine", "ohne", "nein", "0", "entfällt"})
_CHURCH_CODE = re.compile(r"[a-z]{2}")
_MONTH_YEAR = re.compile(r"(?<![0-9])([0-9]{1,2})\s*[./-]\s*([0-9]{4})(?![0-9])")
_WORD_MONTH_YEAR = re.compile(r"([a-zäöü]+)\.?\s+([0-9]{4})")


def _name_key(name: Any) -> frozenset[str]:
    """A person's name as a set of tokens: letters only, casefolded, ß as ss, titles and
    particles dropped, so "Musterfrau, Erika" and "Erika Musterfrau" are one key."""
    text = str(name).casefold().replace("ß", "ss")
    return frozenset(t for t in _NAME_TOKEN.findall(text) if t not in _NAME_FILLERS)


def _names_an_owner(addressee: Any, owners: Sequence[ExistingOwner]) -> bool:
    """D129: whether the Bescheid's addressee names at least one owner, by name tokens like
    D96's employee name: an owner whose tokens equal the addressee's, or one of at least two
    tokens all printed in it ("Max und Erika Mustermann" names both owners)."""
    key = _name_key(addressee)
    if not key:
        return False
    for owner in owners:
        owner_key = _name_key(owner.full_name)
        if owner_key and (owner_key == key or (len(owner_key) >= 2 and owner_key <= key)):
            return True
    return False


def _match_owner(name: Any, owners: Sequence[ExistingOwner]) -> tuple[ExistingOwner | None, bool]:
    """The one owner whose name tokens equal the employee's (exact), else the one owner of at
    least two name tokens all printed in it (a middle name printed: not exact); several or
    none: None. `owners` is the workspace's own list (RLS-scoped by prefill_context)."""
    key = _name_key(name) if name is not None else frozenset()
    if not key:
        return None, False
    exact = [o for o in owners if _name_key(o.full_name) == key]
    if exact:
        return (exact[0], True) if len(exact) == 1 else (None, False)
    within = [
        o for o in owners if len(_name_key(o.full_name)) >= 2 and _name_key(o.full_name) <= key
    ]
    return (within[0], False) if len(within) == 1 else (None, False)


def _tax_class_parts(text: Any) -> tuple[int, bool] | None:
    """ "4 / 0,950" -> (4, True); "IV" -> (4, False); "Steuerklasse 1" -> (1, False). A Faktor
    (a number below 1) counts beside class IV only (§39f). None when no class is named."""
    t = _norm(text)
    factor = _FACTOR.search(t)
    rest = t if factor is None else f"{t[: factor.start()]} {t[factor.end() :]}"
    arabic = _ARABIC_CLASS.search(rest)
    if arabic is not None:
        number = int(arabic.group())
    else:
        words = rest.replace("steuerklasse", " ").replace("faktor", " ").replace("stkl", " ")
        roman = _ROMAN_CLASS.search(words)
        if roman is None:
            return None
        number = _ROMAN[roman.group(1)]
    return number, factor is not None and number == 4


def _tax_class_number(text: Any) -> int | None:
    parts = _tax_class_parts(text)
    return None if parts is None else parts[0]


def _tax_class_factor(text: Any) -> bool | None:
    parts = _tax_class_parts(text)
    return None if parts is None else parts[1]


def _church_tax_marked(text: Any) -> bool | None:
    """The Kirchensteuermerkmal: a confession code ("ev", "rk", "ev/rk") is a mark; dashes
    or "keine" are none; anything else is not understood (None: the user decides)."""
    t = _norm(text).replace("–", "-").replace("—", "-")
    words = [w.strip(".") for w in re.split(r"[\s/,;]+", t)]
    if all(w in _NO_CHURCH_TAX or set(w) == {"-"} for w in words):
        return False
    if any(_CHURCH_CODE.fullmatch(w) for w in words):
        return True
    return None


def _month_start(value: Any) -> str | None:
    """The first of the month a payslip pays, from a date or a month written without a day
    ("08/2026", "August 2026"), as an ISO date."""
    on = _iso_date(value)
    if on is not None:
        return on.replace(day=1).isoformat()
    if not isinstance(value, str):
        return None
    t = _norm(value)
    found = _MONTH_YEAR.search(t)
    if found is not None and 1 <= int(found.group(1)) <= 12:
        return date(int(found.group(2)), int(found.group(1)), 1).isoformat()
    found = _WORD_MONTH_YEAR.search(t)
    if found is not None and found.group(1) in _MONTHS:
        return date(int(found.group(2)), _MONTHS[found.group(1)], 1).isoformat()
    return None


def _whole_months(start: date, end: date) -> int:
    """The calendar months from `start` to `end` that have passed in full: a month counts once
    its day of the month is reached (30.05.2024 to 02.10.2026: 28, D147)."""
    months = (end.year - start.year) * 12 + end.month - start.month
    return months - 1 if end.day < start.day else months


def _years_de(years: Decimal) -> str:
    """21 -> "21 Jahre", 1 -> "1 Jahr", 21.5 -> "21,5 Jahre"."""
    text = format(years.normalize(), "f").replace(".", ",")
    return f"{text} Jahr" if years == 1 else f"{text} Jahre"


def _span_de(months: int) -> str:
    """28 -> "2 Jahre und 4 Monate", 12 -> "1 Jahr", 5 -> "5 Monate"."""
    years, rest = divmod(months, 12)
    parts = []
    if years:
        parts.append("1 Jahr" if years == 1 else f"{years} Jahre")
    if rest or not years:
        parts.append("1 Monat" if rest == 1 else f"{rest} Monate")
    return " und ".join(parts)


def _euro_de(amount: Decimal) -> str:
    """60000 -> "60.000,00 €"."""
    text = f"{amount.quantize(_CENTS, rounding=ROUND_HALF_UP):,.2f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".") + " €"


def _projection_note(on: date, month: ExtractedValue, ytd: ExtractedValue) -> str:
    """What the payslip row projects the year's gross to (D105: ytd + month ×
    months_remaining_paid), so the remaining months are checked with their effect shown."""
    remaining = 12 - on.month
    advice = (
        f"Noch bezahlte Monate: {remaining} (bis Dezember); ändern, wenn das Gehalt vor "
        "Dezember endet (Mutterschutz, Jobwechsel)."
    )
    m, y = _decimal(month.value), _decimal(ytd.value)
    if m is None or y is None:
        return advice
    return (
        f"Hochrechnung {on.year}: {_euro_de(y + m * remaining)} (bis "
        f"{MONTH_NAMES[on.month - 1]} {_euro_de(y)}, dazu {remaining} × {_euro_de(m)}). " + advice
    )


def employer_key(name: Any) -> str:
    """An employer's name compared, the same way step 1's services and rules compare it
    (`schemas.employment.employer_key`: casefolded, whitespace collapsed)."""
    return _shared_employer_key(str(name))


_EMPLOYER_WORD = re.compile(r"[^\W_]+")


def _employer_words(name: Any) -> frozenset[str]:
    """An employer's name as its words, casefolded ("Muster GmbH & Co. KG" -> muster, gmbh,
    co, kg): only to spot one employer spelled two ways, never to match rows."""
    return frozenset(_EMPLOYER_WORD.findall(_norm(name)))


def replaced_pay_row(
    kind: str,
    rows: Sequence[ExistingPayRow],
    *,
    owner_id: UUID,
    tax_year: int,
    document_id: UUID,
    value: dict[str, Any],
) -> ExistingPayRow | None:
    """The current row a new pay row replaces (its supersedes_id), from the `value.*` fields
    proposed or confirmed. A payslip: the row of the same owner, year, employer and month
    (§8.2). A certificate: the row read from the same document, else the row of the same
    employer whose period overlaps this one (a corrected certificate; two periods of one
    employer that do not overlap are two certificates, both kept). Several candidates: None,
    nothing is guessed (the new row is added, the user withdraws the old one by hand)."""
    mine = [r for r in rows if r.kind == kind and r.owner_id == owner_id and r.tax_year == tax_year]
    employer = employer_key(value.get("value.employer") or "")
    if kind == "payslip":
        month = _iso_date(_month_start(value.get("value.period_month")))
        found = [
            r for r in mine if r.period_month == month and employer_key(r.employer) == employer
        ]
    else:
        found = [r for r in mine if r.document_id == document_id]
        start = _iso_date(value.get("value.period_from"))
        end = _iso_date(value.get("value.period_to"))
        if not found and start is not None and end is not None:
            found = [
                r
                for r in mine
                if employer_key(r.employer) == employer
                and r.period_from is not None
                and r.period_to is not None
                and r.period_from <= end
                and start <= r.period_to
            ]
    return found[0] if len(found) == 1 else None


def return_input_value(kind: str, values: dict[str, Any]) -> dict[str, Any]:
    """The `value` of a return_input row from the user's confirmed values of its record (the
    `value.*` fields, dotted; any other key is ignored), as ReturnInputCreate reads it.

    An empty field (missing or null) is left to the schema's default, except a certificate
    amount: it becomes "0" and is named in `unread` (Q-O29: a line the model did not read and
    the user left empty is never a silent 0). `unread` is always computed here; a value given
    for it is ignored."""
    nested: dict[str, Any] = {}
    for name, value in values.items():
        head, dot, path = name.partition(".")
        if head != "value" or not dot or value is None or path == "unread":
            continue
        *parents, leaf = path.split(".")
        target = nested
        for part in parents:
            child = target.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"field {name!r} given both whole and by part")
            target = child
        if leaf in target:
            raise ValueError(f"field {name!r} given both whole and by part")
        target[leaf] = value
    if kind == "wage_certificate":
        unread = [n for n in CERTIFICATE_AMOUNTS if nested.get(n) is None]
        for n in unread:
            nested[n] = "0"
        nested["unread"] = unread
    return nested


__all__ = ["build_proposal", "employer_key", "replaced_pay_row", "return_input_value"]
