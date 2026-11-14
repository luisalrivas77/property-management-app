// The API's error envelope (docs/phases/phase-1-step-3.md §3.5) as a thrown error, and the
// German text the UI shows for it. Rule: show the API's `error`, `message` and field `msg`s,
// never anything the user typed (the API never echoes input either, §3.6).
import type { ErrorKind, ErrorResponse, FieldError } from "./schemas";

export class ApiError extends Error {
  readonly status: number;
  /** The parsed ErrorResponse, or null when the body was not one (e.g. the proxy's own 500). */
  readonly body: ErrorResponse | null;
  readonly requestId: string | null;

  constructor(status: number, body: ErrorResponse | null, requestId: string | null) {
    super(body ? `${status} ${body.error}` : `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.requestId = body?.request_id ?? requestId;
  }

  get kind(): ErrorKind | null {
    return this.body?.error ?? null;
  }
}

/** Parse a non-2xx response body into an ErrorResponse, or null if it is not one. */
export async function readErrorBody(response: Response): Promise<ErrorResponse | null> {
  try {
    const parsed: unknown = await response.json();
    if (parsed && typeof parsed === "object" && typeof (parsed as { error?: unknown }).error === "string") {
      return parsed as ErrorResponse;
    }
  } catch {
    // not JSON
  }
  return null;
}

const KIND_TEXT: Record<ErrorKind, string> = {
  validation: "Die Eingaben sind ungültig.",
  rule_violation: "Die Angaben verletzen eine Regel.",
  invalid: "Der Server hat die Anfrage abgelehnt.",
  unauthenticated: "Nicht angemeldet oder Sitzung abgelaufen.",
  forbidden_origin: "Anfrage abgelehnt: sie kam nicht von dieser Seite.",
  not_found: "Nicht gefunden.",
  conflict: "Konflikt mit dem gespeicherten Stand.",
  retention_hold: "Aufbewahrungsfrist läuft noch.",
  payload_too_large: "Die Datei ist zu groß.",
  unsupported_media_type: "Dieser Dateityp wird nicht angenommen.",
  shutting_down: "Der Server startet gerade neu. Bitte gleich noch einmal versuchen.",
  internal: "Interner Serverfehler.",
};

/** German text for the stable rule codes (phase-1.md, §9.5). */
export const RULE_CODE_TEXT: Record<string, string> = {
  "ownership.shares_must_sum_to_one": "Die Anteile müssen zusammen genau 1 ergeben.",
  "tenancy.exactly_one_primary": "Ein Mietverhältnis braucht genau einen Hauptmieter.",
  "tenancy.no_tenant": "Ein Mietverhältnis braucht mindestens einen Mieter.",
  "tenancy.overlap": "Für diese Einheit gibt es in diesem Zeitraum schon ein Mietverhältnis.",
  "rent_schedule.outside_tenancy": "Die Miete muss innerhalb der Laufzeit des Mietverhältnisses beginnen.",
  "iban.malformed": "Das ist keine gültige IBAN.",
  "iban.bad_length": "Die IBAN hat für dieses Land nicht die richtige Länge.",
  "iban.bad_checksum": "Die Prüfziffern der IBAN stimmen nicht.",
  "loan.payment_does_not_cover_interest": "Die Rate deckt die Zinsen nicht; das Darlehen würde nie getilgt.",
  "loan.term_too_long": "Mit dieser Rate dauert die Tilgung länger als 100 Jahre.",
  "event.correction_mismatch": "Die Korrektur passt nicht zum ursprünglichen Eintrag.",
  // phase 2 (phase-2.md §12)
  "proposal.already_decided": "Über diesen Vorschlag wurde schon entschieden (übernommen oder verworfen).",
  "proposal.not_ready": "Dieser Vorschlag ist noch nicht fertig oder wurde ersetzt.",
  "proposal.other_notice_without_patch":
    "Der zweite Eintrag passt nur, wenn der Betrag der Grunderwerbsteuer oben auf diesen Bescheid gesenkt wird. Bitte beides übernehmen oder den zweiten Eintrag überspringen.",
  "proposal.bank_file_not_read":
    "Diese Datei wird nicht mit KI ausgelesen: Hausbuch hat sie beim Bankabruf von der Bank geholt, oder sie ist ein Kontoauszug oder Umsatz-Export einer Bank, eine Kreditkartenabrechnung oder ein Kontoauszug des Depot-Verrechnungskontos. Solche Dateien liest Hausbuch selbst, ohne KI, auf ihrem Konto ein; steht sie dort noch nicht, auf der Seite des Dokuments „Auf Konto importieren“ oder „Konto anlegen und einlesen“ wählen.",
  "statement.no_account_offer":
    "Aus dieser Datei lässt sich kein neues Konto anlegen: sie ist schon eingelesen, oder sie ist kein Kontoauszug, den Hausbuch selbst liest. Bitte die Seite neu laden.",
  // Phase 6 D166, step 5a: an exchange export is never sent to the model.
  "proposal.exchange_export_not_read":
    "Das ist ein Export einer Börse (BISON oder Binance). Hausbuch liest ihn selbst, ohne KI, auf dem Konto der Börse ein: unter Konten das Konto der Börse öffnen und den Export dort mit „Export der Börse hochladen“ hochladen; gibt es noch keins, ein Konto der Art „Börse (Krypto)“ anlegen.",
  "document.extraction_stale": "Das Dokument wurde inzwischen neu ausgelesen. Bitte die Seite neu laden und die Felder prüfen.",
  "document.extraction_kind_mismatch": "Für diese Dokumentart gibt es eines der Felder von Hand nicht; es kann nicht gespeichert werden.",
  // bank statements (phase-2.md §12 and the step 3 additions, rules/statements.py)
  "statement.unreconciled": "Der berechnete Endsaldo stimmt nicht mit dem Saldo der Bank überein.",
  "statement.opening_mismatch": "Der Anfangssaldo der Datei stimmt nicht mit dem berechneten Kontostand überein.",
  "statement.before_anchor":
    "Der Auszug enthält Umsätze am oder vor dem Datum des Anfangssaldos. Den Anfangssaldo auf einen früheren Tag legen.",
  "statement.unknown_format":
    "Dateiformat nicht erkannt. Erwartet: camt (XML oder ZIP), Sparkasse CSV-CAMT, der CSV-Export der DKB, die PDF-Abrechnung einer Kreditkarte oder der PDF-Kontoauszug eines Depots. Andere Unterlagen unter Dokumente hochladen.",
  "statement.unparsable": "Die Datei ist beschädigt oder unvollständig und konnte nicht gelesen werden.",
  "statement.archive_rejected":
    "Das ZIP-Archiv wurde abgelehnt: nur XML-Dateien ohne Ordner, höchstens 400 Dateien und 20 MiB, keine ZIPs im ZIP.",
  "statement.entity_declared": "Die XML-Datei enthält eine DTD oder Entity-Deklaration und wird aus Sicherheitsgründen abgelehnt.",
  "statement.report_unbalanced": "In einem Tagesbericht ergeben Anfangssaldo und Umsätze nicht den Endsaldo.",
  "statement.chain_break": "Ein Tagesbericht beginnt nicht mit dem Endsaldo des vorherigen; vermutlich fehlt ein Tag.",
  "statement.account_mismatch":
    "Der Auszug gehört zu einem anderen Konto: die IBAN oder Kundennummer in der Datei passt nicht, oder die Datei ist keine Abrechnung dieser Kontoart (eine Kreditkartenabrechnung nur auf eine Kreditkarte, ein Kontoauszug nur auf ein Bankkonto).",
  "statement.no_payment_iban": "Diese Abrechnung nennt keine IBAN für Einzahlungen.",
  "statement.unsupported_currency": "Es werden nur Umsätze in Euro unterstützt.",
  "statement.empty": "Die Datei enthält keine gebuchten Umsätze.",
  "statement.owner_account": "Auf ein Privatkonto der Eigentümer werden keine Kontoauszüge importiert.",
  "statement.balance_from_file": "Dieser Auszug bringt seine Salden selbst mit; ein Saldo wird nicht eingegeben.",
  "statement.opening_not_derivable":
    "Der Anfangssaldo lässt sich aus diesem Auszug nicht mehr ableiten: ein früherer Auszug ist hinzugekommen, ein anderer geht schon auf, oder der Endsaldo wurde geändert. Bitte die Seite neu laden.",
  "statement.duplicate_accounts_differ": "Die beiden Umsätze gehören zu verschiedenen Konten.",
  "statement.duplicate_excludes_both": "Zusammenführen würde beide Umsätze aus dem Saldo nehmen; einer muss zählen.",
  // allocations and learned rules (phase-2.md §8, step 4)
  "allocation.sum_mismatch": "Die Zeilen ergeben zusammen nicht den Betrag der Buchung.",
  "allocation.empty": "Eine Zuordnung braucht mindestens eine Zeile.",
  "allocation.transaction_mismatch": "Die Zuordnung gehört zu einer anderen Buchung. Bitte die Seite neu laden.",
  "allocation.import_not_reconciled":
    "Der Kontoauszug dieser Buchung ist nicht abgestimmt. Erst wenn seine Salden stimmen, kann sie zugeordnet werden.",
  "allocation.line_not_open":
    "Diese Buchung zählt nicht (zusammengeführte Doppelbuchung) oder wartet noch auf die Entscheidung über eine mögliche Doppelbuchung.",
  "allocation.scope_mismatch":
    "Einheit, Mietverhältnis oder Darlehen gehören nicht zur gewählten Immobilie, oder die Immobilie fehlt.",
  "allocation.excluded_transaction": "Diese Buchung wurde als Doppelbuchung zusammengeführt und zählt nicht.",
  "rule.not_learnable":
    "Für diese Zuordnung lernt Hausbuch keine Regel. Bitte ohne „Künftig immer so zuordnen“ bestätigen.",
  "rule.invalid_pattern":
    "Das Muster ist kein gültiger Suchausdruck (regulärer Ausdruck, höchstens 200 Zeichen).",
  // every account of the household (phase-6.md D156-D160, D168)
  // „Alle bestätigen“ in Zu prüfen (phase-6.md D159 amended 13 Nov 2026)
  "inbox.category_not_bulk": "Zahlungen dieser Kategorie werden einzeln bestätigt, nicht als Gruppe.",
  "inbox.line_not_private": "Nur Zahlungen privater Konten lassen sich als Gruppe bestätigen. Bitte die Seite neu laden.",
  "allocation.category_not_for_account":
    "Diese Kategorie passt nicht zu diesem Konto: ein Privatkonto nimmt nur Haushaltskategorien, das Mietkonto nur die der Immobilie. Für ein Konto mit Buchungen der anderen Art bitte erst diese Buchungen umbuchen.",
  "account.kind_not_available": "Diese Kontoart kann noch nicht angelegt werden.",
  "ownership.sum_not_one": "Die Anteile müssen zusammen genau 1 (100 %) ergeben, jeder Eigentümer höchstens einmal.",
  "ownership.owner_missing": "Ein Anteil nennt keinen Eigentümer dieses Haushalts. Bitte die Seite neu laden.",
  "rule.too_broad":
    "Unter diesem Namen wurden schon Umsätze verschiedener Kategorien bestätigt (z. B. PayPal). Bitte stattdessen ein Muster aus dem Verwendungszweck angeben.",
  "rule.conflicts":
    "Eine Regel für diesen Namen würde Umsätze treffen, die anders bestätigt wurden. Ohne Regel bestätigen oder ein Muster aus dem Verwendungszweck angeben.",
  // „Diese auch auf … umbuchen“ (phase-6.md D160, 14 Nov 2026); the panel names the line beforehand.
  "rule.rebook_blocked":
    "Nichts wurde umgebucht: eine frühere Zahlung dieses Empfängers lässt sich nicht automatisch umbuchen (sie ist aufgeteilt, hat einen Beleg, ist mit einer Immobilie, einer Überweisung oder dem Depot verknüpft, oder ihr Kontoauszug ist nicht abgestimmt). Bitte die Seite neu laden: die Zahlung wird dann hier genannt und lässt sich öffnen und von Hand ändern. Oder ohne „Künftig immer so zuordnen“ bestätigen.",
  "settings.consent_switch_off": "Erst „Private Konten mit KI zuordnen“ einschalten, dann zustimmen.",
  "settings.consent_already_recorded": "Ihre Zustimmung ist schon gespeichert.",
  // POST /inbox/ai-retry (D50); a proposal's ai.disabled has its own text in AI_ERROR_TEXT.
  "ai.disabled": "Die KI ist auf dem Server nicht eingeschaltet. Zahlungen lassen sich nur von Hand zuordnen.",
  // the Anlage V draft (phase-3.md §10)
  "tax.year_not_available": "Für dieses Jahr gibt es zu dieser Immobilie keinen Entwurf (vor dem Kauf oder in der Zukunft).",
  "tax.no_rule_set": "Für dieses Steuerjahr gibt es noch kein Regelwerk, also keinen Entwurf.",
  // tax inputs (phase-3.md §10, D78, D79)
  "tax_input.extension_needs_herstellung":
    "Eine Erweiterung zählt nur bei einer Renovierung mit „Herstellungskosten“ nicht zur 15 %-Grenze. Solange diese Ausnahme gilt, bleibt die Renovierung Herstellungskosten; dafür erst die Ausnahme „Erweiterung“ zurücknehmen.",
  "tax_input.useful_life_not_shorter":
    "Die Nutzungsdauer laut Gutachten muss kürzer sein als der Rest der gesetzlichen (40 Jahre vor 1925, 50 Jahre bis 2022, 33⅓ Jahre ab 2023 fertiggestellt, abzüglich der Jahre seit der Übergabe). Sonst gilt der gesetzliche AfA-Satz.",
  // a movable asset, the Einbauküche (D80)
  "tax_input.movable_asset_year":
    "Eine mit der Wohnung gekaufte Einbauküche beginnt im Steuerjahr des Kaufs; eine später gekaufte frühestens in diesem Jahr.",
  "tax_input.movable_asset_exceeds_building":
    "Der Wert der Küche (mit ihrem Anteil an den Nebenkosten) muss kleiner sein als der Gebäudeanteil des Kaufpreises. Bitte den Wert oder die Kaufpreisaufteilung prüfen.",
  "tax_input.movable_asset_no_split":
    "Zuerst eine Kaufpreisaufteilung wählen: eine mitgekaufte Küche wird aus dem Gebäudeanteil herausgerechnet.",
  "tax_input.movable_asset_separate_unsupported":
    "Eine gesondert gekaufte Küche wird noch nicht unterstützt; sie braucht ihr Anschaffungsdatum.",
  "tax_input.movable_asset_no_purchase_date": "Zuerst das Übergabedatum der Immobilie eintragen.",
  // household income and the tax effect (phase-4.md §8, D83)
  "income_input.scope_invalid":
    "Diese Angabe passt nicht zu ihrem Eintrag: eine Korrektur gilt für dieselbe Art, dasselbe Jahr und dieselbe Person, eine Rücknahme wiederholt den Wert.",
  "income_input.not_current":
    "Für dieses Jahr gibt es schon einen gültigen Eintrag dieser Art, oder der korrigierte Eintrag ist nicht mehr aktuell. Bitte die Seite neu laden und den aktuellen Eintrag ändern.",
  "income.assessed_result_needed":
    "Der Bescheid eines früheren Jahres gilt weiter; für die Steuerwirkung braucht es die Immobilie, deren Ergebnis im Bescheid steckt.",
  "tariff.no_file": "Für dieses Jahr gibt es keinen Einkommensteuertarif.",
  // the projection and its scenarios (phase-4.md §8, D87)
  "projection.no_rule_set":
    "Für das Kaufjahr dieser Immobilie gibt es noch kein Regelwerk, also weder Ist-Zahlen noch eine Prognose.",
  "scenario.sale_year_before_today": "Das Verkaufsjahr muss nach dem laufenden Jahr liegen: die Prognose beginnt im nächsten Januar.",
  "scenario.not_current":
    "Das Szenario ist nicht mehr aktuell, oder es gibt schon ein Standard-Szenario. Bitte die Seite neu laden.",
  "scenario.scope_invalid": "Das Szenario passt nicht zu dieser Immobilie oder ihren Darlehen.",
  // the letter to a tenant about the Nebenkostenpauschale (phase-4.md D155 (7))
  "flat_rate.no_letter":
    "Für dieses Mietverhältnis gibt es heute nichts mitzuteilen: die Kosten sind seit der Festlegung der Pauschale nicht um mehr als 10 € im Monat gestiegen und nicht gesunken.",
  // exports of the draft (phase-3.md §10, §12)
  // the FinTS sync (phase-5.md §4.4)
  "bank.not_configured":
    "Der Bankabruf ist auf dem Server nicht eingerichtet: PIN oder Produkt-ID fehlen in der Serverkonfiguration (Runbook Schritt 14).",
  "bank.rate_limited": "Höchstens vier Abrufe pro Stunde von Hand. Bitte etwas später noch einmal.",
  "bank.sync_running": "Für dieses Konto läuft schon ein Abruf.",
  "bank.window_invalid":
    "Der Abruf hätte nach heute begonnen. Entweder liegt der Anfangssaldo des Kontos heute oder später: dann den Stichtag im Konto prüfen. Oder die hochgeladenen Kontoauszüge reichen schon bis heute: dann morgen erneut abrufen.",
  "bank.url_not_https": "Die FinTS-Adresse muss mit https:// beginnen.",
  "bank.tan_not_decoupled":
    "Die Bank bietet für diesen Zugang keine Freigabe per App (pushTAN) an. Im Online-Banking S-pushTAN einrichten.",
  "bank.approval_timeout": "Die Freigabe in der S-pushTAN-App kam nicht innerhalb von fünf Minuten.",
  "bank.account_not_found": "Unter diesem Zugang führt die Bank kein Konto mit der IBAN dieses Kontos. Anmeldename und BLZ prüfen.",
  "bank.state_rejected":
    "Die gespeicherte Verbindung zur Bank passte nicht mehr und wurde verworfen; der nächste Abruf fragt wieder nach einer Freigabe.",
  "bank.unreachable": "Die Bank war nicht erreichbar. Später erneut abrufen; hält es an, die FinTS-Adresse prüfen.",
  "bank.refused":
    "Die Bank hat den Abruf abgelehnt: wegen Anmeldename oder PIN, einer in der App abgelehnten Freigabe oder der ersten Einrichtung; der Code der Bank steht unter Zu prüfen. Nicht einfach erneut abrufen: nach drei falschen PINs sperrt die Bank den Zugang.",
  "bank.unsupported_format": "Die Bank liefert Umsätze in keinem Format, das Hausbuch lesen kann. Kontoauszüge weiter als Datei hochladen.",
  "bank.failed": "Der Abruf ist an einem unerwarteten Fehler gescheitert. Erneut abrufen; hält es an, als Datei hochladen.",
  // one PIN per online-banking login, a private account's history first, DKB's daily fetch (phase-6.md D163, D173)
  "bank.pin_missing":
    "Für den Zugang dieses Kontos ist keine PIN auf dem Server hinterlegt. In der Scaleway-Konsole beim API-Container die geheime Umgebungsvariable anlegen, die beim Bankabruf unter „PIN-Variable“ steht, und den Container neu bereitstellen (Runbook Schritt 17).",
  "bank.history_missing": "Laden Sie zuerst den Kontoauszug-Export für 2026 hoch, dann verbinden Sie das Konto.",
  "bank.auto_sync_waits":
    "Der tägliche Abruf lässt sich erst einschalten, wenn zwei Abrufe von Hand nacheinander ohne Freigabe in der DKB-App durchgelaufen sind; sonst käme jeden Morgen eine Anfrage aufs Handy.",
  "tax_export.too_large":
    "Die Belege des Jahres sind zusammen größer als 100 MiB. Das Archiv bitte von Hand aufteilen; die Ausfüllhilfe geht weiterhin.",
  "tax_export.failed": "Der Export ist fehlgeschlagen. Bitte noch einmal versuchen.",
  "tax_export.kind_invalid":
    "Diese Art Export gibt es hier nicht: das Feldblatt der Anlage N je Eigentümer, Ausfüllhilfe und Archiv je Immobilie.",
  // return inputs, the Anlage N draft and the whole-return estimate (phase-4b.md §9)
  "return_input.scope_invalid":
    "Diese Angabe passt nicht zu ihrem Eintrag: eine Änderung gilt für dieselbe Art, dasselbe Jahr und dieselbe Person, eine Rücknahme wiederholt den Wert, und Zeiträume liegen im Steuerjahr.",
  "return_input.not_current":
    "Für dieses Jahr gibt es schon einen gültigen Eintrag dieser Art, oder der geänderte Eintrag ist nicht mehr aktuell. Bitte die Seite neu laden und den aktuellen Eintrag ändern.",
  "return_input.work_room_conflict":
    "Ein Arbeitszimmer, das das ganze Jahr Mittelpunkt der Tätigkeit ist, schließt die Homeoffice-Tagespauschale aus. Erst die Homeoffice-Tage entfernen oder weniger als 12 Monate angeben.",
  "return_input.carried_from_invalid": "Diese Angabe lässt sich nicht aus dem Vorjahr übernehmen.",
  "anlage_n.no_pay":
    "Für dieses Jahr ist noch kein Arbeitslohn erfasst (Lohnsteuerbescheinigung, Gehaltsabrechnung oder erwartetes Bruttogehalt).",
  "anlage_n.useful_life_missing": "Für ein sonstiges Arbeitsmittel bitte die Nutzungsdauer in Monaten angeben.",
  "anlage_n.work_room_excludes_day_rate":
    "Ein ganzjähriges Arbeitszimmer und die Homeoffice-Tagespauschale schließen sich aus.",
  "return.childcare_age": "Kinderbetreuungskosten zählen nur bis zum 14. Geburtstag des Kindes.",
  "return.household_services_cash":
    "Bar bezahlte Handwerker- oder Haushaltsleistungen zählen nicht (§ 35a): nur Überweisungen.",
  // Phase 6 step 4: the depot's documents (services/depot.py)
  "depot.account_required": "Bitte als Konto ein Depot wählen; Wertpapiere werden nur auf einem Depot gebucht.",
  "depot.movement_exists":
    "Dieser Vorgang (gleiche Referenz oder gleicher Tag und Betrag) ist auf dem Depot schon gebucht. Das Dokument wurde vermutlich zweimal hochgeladen; diesen Eintrag überspringen.",
  "depot.holding_exists":
    "Dieser Bericht ist für dieses Depot und diesen Stichtag schon übernommen. Diesen Eintrag überspringen.",
  "depot.holdings_mismatch":
    "Der Depotbericht nennt andere Stückzahlen, als die gebuchten Abrechnungen ergeben. Bitte die fehlenden Wertpapierabrechnungen hochladen.",
  // Phase 6 step 5b: prices (services/prices/overview.py)
  "asset.not_crypto":
    "Eine CoinGecko-ID oder ein Kraken-Paar gibt es nur für Kryptowährungen. Für einen Fonds bitte „Kurs eintragen“ nutzen.",
  "asset.is_euro": "Der Euro hat immer den Kurs 1; dafür wird kein Kurs eingetragen.",
  // Phase 6 step 5a: an exchange's exports (services/exchange/imports.py, D166, D178)
  "exchange.unreadable":
    "Diese Datei ist kein Export von BISON oder Binance, oder eine Zeile darin ist unlesbar. Bitte den Transaktions-Export der Börse unverändert hochladen (CSV oder die ZIP-Datei, in der er kommt).",
  "exchange.binance_spot_history":
    "Das ist Binances Spot-Handelsverlauf. Hausbuch braucht ihn nicht: bitte den Transaktionsverlauf („Transaction History“) und den Auszahlungsverlauf hochladen.",
  "exchange.binance_ambiguous_trade":
    "In derselben Sekunde stehen zwei Tauschgeschäfte, die sich nicht eindeutig zuordnen lassen. Bitte den Export für einen kürzeren Zeitraum erneut herunterladen.",
  "exchange.transactions_first":
    "Das ist der Auszahlungsverlauf. Bitte zuerst den Transaktionsverlauf hochladen, dann den Auszahlungsverlauf.",
  "exchange.account_mismatch":
    "Diese Datei stammt von einer anderen Börse als dieses Konto. Bitte beim richtigen Börsenkonto hochladen.",
  "exchange.not_an_exchange": "Dieses Konto ist kein Börsenkonto.",
  "exchange.balance_unknown_asset":
    "Für einen der Coins gibt es in diesem Export keine Bewegung. Bitte nur die Coins eintragen, die das Formular zeigt.",
  "exchange.balance_missing": "Bitte für jeden Coin, den der Export als Bestand ergibt, den Bestand laut Börse eintragen.",
  "exchange.provider_unknown": "Bitte als Börse BISON oder Binance wählen.",
  "exchange.answer_invalid": "Diese Antwort passt nicht zu dieser Bewegung (Eingang oder Ausgang).",
  "exchange.movement_done": "Diese Bewegung ist schon bestätigt oder beantwortet.",
  "exchange.unreconciled":
    "Der Bestand laut Börse weicht von den eingelesenen Bewegungen ab. Fehlt ein Teil des Exports, oder wurde ein Bestand falsch abgelesen?",
  "exchange.withdrawal_history_missing":
    "Binance nennt Netzwerk und Transaktions-Hash einer Auszahlung nur im Auszahlungsverlauf. Bitte ihn hochladen.",
  "exchange.withdrawal_history_no_match":
    "Im Auszahlungsverlauf steht keine abgeschlossene Auszahlung desselben Coins, deren Betrag plus Gebühr dem gesendeten Betrag entspricht, innerhalb einer Stunde davor.",
  "exchange.deposit_question": "Ist dieser Eingang ein Bonus, eine Belohnung oder kam er von einer eigenen Wallet?",
  // D178 amended: „Differenz als Korrektur buchen“
  "exchange.correction_not_unreconciled":
    "Für diesen Export weicht kein Bestand ab. Eine Korrektur ist nur möglich, solange ein Coin abweicht.",
  "exchange.correction_not_latest":
    "Nach diesem Export wurde schon ein späterer eingelesen. Bitte die Korrektur beim neuesten Export buchen, sonst ändert sie dessen Abgleich unbemerkt.",
  "exchange.correction_eur":
    "Für Euro gibt es keine Korrektur: Euro-Beträge gleichen sich innerhalb der Rundung ab. Bitte den Euro-Bestand prüfen und neu eingeben.",
  "exchange.correction_no_difference": "Dieser Coin stimmt mit dem Bestand laut Börse überein; es gibt nichts zu korrigieren.",
  "exchange.correction_stale":
    "Seit der Eingabe des Bestands haben sich die Bewegungen geändert. Bitte den Bestand laut Börse neu eingeben und dann die Differenz buchen.",
};

/** German text for a proposal's `error_code`: why reading the document failed or was
 * stopped (phase-2.md §5.5, §12; the job's extra codes in backend rules/errors.py). */
export const AI_ERROR_TEXT: Record<string, string> = {
  "ai.disabled": "Automatisches Auslesen ist auf dem Server nicht eingeschaltet.",
  "ai.blocked_tenant_data": "Dokumente mit Mieterdaten werden nicht an die KI geschickt, solange das nicht freigegeben ist.",
  "ai.unparsable": "Die Antwort der KI ließ sich auch beim zweiten Versuch nicht auswerten.",
  "ai.refused": "Die KI hat das Auslesen abgelehnt oder abgebrochen.",
  "ai.too_many_pages": "Das Dokument hat zu viele Seiten, um es automatisch auszulesen.",
  "ai.unsupported_media_type": "Dieser Dateityp kann nicht automatisch ausgelesen werden (z. B. HEIC); bitte als PDF, JPEG oder PNG hochladen.",
  "ai.api_error": "Der KI-Dienst hat die Anfrage mit einem Fehler beantwortet.",
  "ai.unavailable": "Der KI-Dienst war nicht erreichbar. Später noch einmal auslesen.",
  "ai.unreadable": "Die Datei ließ sich nicht öffnen (beschädigt oder mit Passwort geschützt).",
  "ai.internal_error": "Beim Auslesen ist ein interner Fehler aufgetreten.",
  "document.adobe_form":
    "Dieses PDF ist ein Adobe-Formular und lässt sich nur in Adobe Reader anzeigen. Öffnen Sie es dort, drucken Sie es als PDF und laden Sie die Kopie hoch.",
  // Phase 6 step 4 (D168): a depot document while the private-accounts switch is off.
  "ai.blocked_private_data":
    "Das ist ein Dokument Ihres Depots. Es geht erst an die KI, wenn unter Einstellungen „Private Konten mit KI zuordnen“ eingeschaltet ist; danach hier „Neu auslesen“ drücken.",
  // Phase 6 D176 (3) and step 4: a credit card statement or the depot's cash statement uploaded
  // as a document is never sent to the model; since 13 Nov 2026 a bank's export neither.
  "proposal.bank_file_not_read":
    "Das ist ein Kontoauszug oder Umsatz-Export einer Bank, eine Kreditkartenabrechnung oder ein Kontoauszug des Depot-Verrechnungskontos. Hausbuch liest die Datei selbst, ohne KI, auf ihrem Konto ein: mit „Auf Konto importieren“ auf der Seite dieses Dokuments oder unter Zu prüfen, direkt aus der gespeicherten Datei, ohne sie noch einmal hochzuladen. Gibt es das Konto noch nicht, legt „Konto anlegen und einlesen“ es aus der Datei an.",
  // Phase 6 D166, step 5a: a BISON or Binance export uploaded through Dokumente.
  "proposal.exchange_export_not_read":
    "Das ist ein Export einer Börse (BISON oder Binance). Hausbuch liest ihn selbst, ohne KI, auf dem Konto der Börse ein: unter Konten das Konto der Börse öffnen und den Export dort mit „Export der Börse hochladen“ hochladen; gibt es noch keins, ein Konto der Art „Börse (Krypto)“ anlegen.",
};

/** The German sentence for a proposal's error code; unknown codes get a generic one. */
export function aiErrorText(code: string | null): string {
  return (code && AI_ERROR_TEXT[code]) || "Das Dokument konnte nicht gelesen werden.";
}

/** The German sentence for one rule code, with the payment number of a loan schedule error. */
export function ruleCodeText(code: string, atNumber?: number | null): string | null {
  const text = RULE_CODE_TEXT[code];
  if (!text) return null;
  return atNumber != null && code.startsWith("loan.") ? `${text} (ab Rate Nr. ${atNumber})` : text;
}

/** Every rule code of an error body, without repeats (`code` is also `codes[0]`). */
export function ruleCodes(body: ErrorResponse): string[] {
  return [...new Set([...(body.codes ?? []), ...(body.code ? [body.code] : [])])];
}

export interface ErrorDescription {
  /** One German sentence. */
  text: string;
  /** The API's stable `error` string (or a client-side code), for reference. */
  code: string | null;
  /** The API's `message`, rule-code texts and field errors (`loc: msg`). */
  details: string[];
  /** Quote this when reporting a 500. */
  requestId: string | null;
}

function fieldErrorLine(error: FieldError): string {
  const loc = error.loc[0] === "body" ? error.loc.slice(1) : error.loc;
  return loc.length > 0 ? `${loc.join(".")}: ${error.msg}` : error.msg;
}

export function describeApiError(error: ApiError): ErrorDescription {
  const body = error.body;
  if (!body) {
    const text =
      error.status >= 500
        ? "Der Server ist nicht erreichbar oder hat einen Fehler gemeldet."
        : `Unerwartete Antwort des Servers (HTTP ${error.status}).`;
    return { text, code: null, details: [], requestId: error.requestId };
  }
  const details: string[] = [];
  const codes = ruleCodes(body);
  let allCodesKnown = codes.length > 0;
  for (const code of codes) {
    const known = ruleCodeText(code, body.at_number);
    if (!known) allCodesKnown = false;
    details.push(known ?? code);
  }
  // A known code says it in German; the server's message would only repeat it in English (and
  // a loan schedule message names the submitted payment). Unknown codes show the message.
  if (body.message && !allCodesKnown) details.push(body.message);
  for (const fieldError of body.detail ?? []) details.push(fieldErrorLine(fieldError));
  return { text: KIND_TEXT[body.error] ?? "Fehler.", code: body.error, details, requestId: error.requestId };
}
