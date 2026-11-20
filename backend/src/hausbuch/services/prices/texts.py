"""The price cards' texts in Zu prüfen, German beside English (phase 10 D284, step 4c); chosen
when Zu prüfen is read. Kept beside `overview.py` so the cards there change one line each."""

from decimal import ROUND_HALF_UP, Decimal

from hausbuch import i18n

TEXTS = i18n.Texts(
    "prices",
    {
        "issuer.unknown": ("der Fondsgesellschaft", "the fund company"),
        "differs.nav": (
            "der Fondspreis (NAV), den {issuer} für diesen Tag veröffentlicht,",
            "the fund price (NAV) {issuer} publishes for this day",
        ),
        "differs.nav.valued": (
            "Hausbuch bewertet den Fonds an diesem Tag mit dem NAV von {issuer}.",
            "Hausbuch values the fund on this day with the NAV of {issuer}.",
        ),
        "differs.exchange": (
            "der Börsenkurs von {venue} desselben Tages",
            "the exchange price of {venue} on the same day",
        ),
        "differs.exchange.valued": (
            "Hausbuch bewertet den Fonds an diesem Tag mit dem Kurs des Berichts.",
            "Hausbuch values the fund on this day with the report's price.",
        ),
        "differs": (
            "Der Depotbericht bewertet {name} am {day} mit {report_price} je Anteil, {other}"
            " liegt bei {price}: {percent} % Abstand. {valued}",
            "The depot report values {name} on {day} at {report_price} per unit; {other} is"
            " {price}: {percent} % apart. {valued}",
        ),
        "differs.question": (
            "Bitte im Bericht prüfen, welcher Kurs stimmt („Bericht öffnen“). Ist ein anderer"
            " Kurs richtig, ihn unter Anlagen › Übersicht mit „Kurs eintragen“ für diesen Tag"
            " eintragen; dann gilt er, und dieser Hinweis entfällt. Hausbuch ändert von sich"
            " aus keinen Kurs.",
            "Please check in the report which price is right (“Open report”). If another price"
            " is right, enter it for this day under Investments › Overview with “Enter price”;"
            " it then applies, and this note goes away. Hausbuch never changes a price by"
            " itself.",
        ),
        "coin.no_source": (
            "Für {name} kennt Hausbuch keine Kursquelle, daher fehlt sein Wert in der"
            " Vermögensübersicht.",
            "Hausbuch knows no price source for {name}, so its value is missing from the net"
            " worth overview.",
        ),
        "coin.no_source.question": (
            "Bitte unter Anlagen › Übersicht „Kursquelle wählen“: die CoinGecko-ID oder das"
            " Kraken-Paar der Münze eintragen. Bis dahin kann ein Kurs von Hand eingetragen"
            " werden.",
            "Please use “Choose price source” under Investments › Overview: enter the coin's"
            " CoinGecko ID or Kraken pair. Until then a price can be entered by hand.",
        ),
        "value.older": (
            "der Wert in der Vermögensübersicht beruht auf einem älteren Kurs.",
            "the value in the net worth overview rests on an older price.",
        ),
        "value.missing": (
            "in der Vermögensübersicht fehlt sein Wert.",
            "its value is missing from the net worth overview.",
        ),
        "currency": (
            "{issuer} veröffentlicht den Kurs von {name} in {currency}. Für diese Währung gibt"
            " die Europäische Zentralbank keinen Referenzkurs heraus, daher kann Hausbuch den"
            " Kurs nicht in Euro umrechnen; {value}",
            "{issuer} publishes the price of {name} in {currency}. The European Central Bank"
            " publishes no reference rate for this currency, so Hausbuch cannot convert the"
            " price into euros; {value}",
        ),
        "currency.question": (
            "Bitte unter Anlagen › Übersicht „Kurs eintragen“ den aktuellen Kurs in Euro"
            " eintragen, zum Beispiel aus der Depotansicht der Bank. Der nächste Depotbericht"
            " oder eine Wertpapierabrechnung bewertet den Fonds ebenfalls neu.",
            "Please enter the current price in euros under Investments › Overview with “Enter"
            " price”, for example from the bank's depot view. The next depot report or a trade"
            " confirmation also values the fund anew.",
        ),
        "report.silent": (
            "der letzte Depotbericht nennt für ihn keinen Wert, aus dem sich ein Kurs ergibt",
            "the latest depot report states no value for it from which a price follows",
        ),
        "report.stale": (
            "kein aktueller Depotbericht bewertet ihn",
            "no current depot report values it",
        ),
        "report.none": ("bisher nennt ihn kein Depotbericht", "no depot report lists it yet"),
        "fund.not_delivered": (
            "Für {name} hat Hausbuch keinen aktuellen Kurs: {issuer} hat ihn zuletzt nicht"
            " geliefert, und {report}.",
            "Hausbuch has no current price for {name}: {issuer} did not deliver it last time,"
            " and {report}.",
        ),
        "fund.no_issuer": (
            "Für {name} veröffentlicht die Fondsgesellschaft keinen Kurs, den Hausbuch abrufen"
            " kann, und {report}.",
            "The fund company publishes no price for {name} that Hausbuch can fetch, and {report}.",
        ),
        "fund.question": (
            "Der nächste Depotbericht unter Dokumente bewertet den Fonds von selbst („Kurs aus"
            " dem letzten Bericht“). Bis dahin kann unter Anlagen › Übersicht „Kurs eintragen“"
            " ein Kurs von Hand eingetragen werden.",
            "The next depot report under Documents values the fund by itself (“Price from the"
            " latest report”). Until then a price can be entered by hand under Investments ›"
            " Overview with “Enter price”.",
        ),
        "failed": (
            "Der Kurs von {name} kam seit {days} Tagen nicht von {issuer} (seit dem {first});"
            " {value}",
            "The price of {name} has not come from {issuer} for {days} days (since {first});"
            " {value}",
        ),
        "failed.question": (
            "Hausbuch versucht es jeden Morgen erneut. Soll ein aktueller Kurs von Hand"
            " eingetragen werden („Kurs eintragen“)? Der nächste Depotbericht oder eine"
            " Wertpapierabrechnung bewertet den Fonds ebenfalls neu.",
            "Hausbuch tries again every morning. Should a current price be entered by hand"
            " (“Enter price”)? The next depot report or a trade confirmation also values the"
            " fund anew.",
        ),
    },
)


def price(value: Decimal) -> str:
    """A fund price to at most four places and at least two: Decimal("8.47650000") → de
    „8,4765 €“, en „€8.4765“; Decimal("8.7") → „8,70 €“, „€8.70“."""
    text = f"{value.quantize(Decimal('0.0001'), ROUND_HALF_UP):f}"
    fraction = text.partition(".")[2].rstrip("0").ljust(2, "0")
    shown = i18n.number(Decimal(text), places=len(fraction))
    return f"{shown} €" if not i18n.english() else f"€{shown}"


def percent(value: Decimal) -> str:
    """A gap in percent as computed, de „2,5“, en „2.5“."""
    text = f"{value:f}"
    return text if i18n.english() else text.replace(".", ",")
