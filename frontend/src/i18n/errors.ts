// The English of src/api/errors.ts (phase 10 D284): the error kinds, the stable rule codes and
// why reading a document failed. German stays the source there; a code missing here falls back
// to the German sentence. Words from glossary.md: Vorschlag = suggestion, Übernehmen = Apply,
// Beleg = receipt, Zu prüfen = To review, Bankabruf = Bank sync; tax terms stay German. A button
// is quoted in English with its German label beside it, since not every screen is English yet.
import type { ErrorKind } from "@/api/schemas";

export const KIND_TEXT_EN: Record<ErrorKind, string> = {
  validation: "The input is invalid.",
  rule_violation: "The details break a rule.",
  invalid: "The server refused the request.",
  unauthenticated: "Not signed in, or the session has expired.",
  forbidden_origin: "Request refused: it did not come from this page.",
  not_found: "Not found.",
  conflict: "Conflict with the saved state.",
  retention_hold: "The retention period is still running.",
  payload_too_large: "The file is too large.",
  unsupported_media_type: "This file type is not accepted.",
  shutting_down: "The server is restarting. Please try again in a moment.",
  internal: "Internal server error.",
};

export const ERROR_TEXT_EN = {
  noBody500: "The server cannot be reached or reported an error.",
  unexpected: (status: number) => `Unexpected answer from the server (HTTP ${status}).`,
  generic: "Error.",
  fromPayment: (n: number) => ` (from payment no. ${n})`,
  documentUnreadable: "The document could not be read.",
};

export const RULE_CODE_TEXT_EN: Record<string, string> = {
  "ownership.shares_must_sum_to_one": "The shares must add up to exactly 1.",
  "tenancy.exactly_one_primary": "A tenancy needs exactly one main tenant.",
  "tenancy.no_tenant": "A tenancy needs at least one tenant.",
  "tenancy.overlap": "This unit already has a tenancy in this period.",
  "rent_schedule.outside_tenancy": "The rent must start within the tenancy's term.",
  "iban.malformed": "This is not a valid IBAN.",
  "iban.bad_length": "The IBAN does not have the right length for this country.",
  "iban.bad_checksum": "The IBAN's check digits do not match.",
  "loan.payment_does_not_cover_interest": "The payment does not cover the interest; the loan would never be repaid.",
  "loan.term_too_long": "At this payment, repayment takes longer than 100 years.",
  "event.correction_mismatch": "The correction does not match the original entry.",
  "proposal.already_decided": "This suggestion has already been decided (applied or discarded).",
  "proposal.not_ready": "This suggestion is not ready yet or has been replaced.",
  "proposal.other_notice_without_patch":
    "The second entry only fits if the Grunderwerbsteuer amount above is lowered to this notice. Please apply both or skip the second entry.",
  "proposal.bank_file_not_read":
    "This file is not read with AI: Hausbuch fetched it from the bank during a bank sync, or it is a bank statement or transaction export, a credit card statement or a statement of the depot's cash account. Hausbuch reads such files itself, without AI, onto their account; if it is not there yet, choose “Import to account” („Auf Konto importieren“) or “Create account and import” („Konto anlegen und einlesen“) on the document's page.",
  "statement.no_account_offer":
    "No new account can be created from this file: it has already been imported, or it is not a statement Hausbuch reads itself. Please reload the page.",
  "statement.follow_unreadable":
    "This file could not be read just now; please click “Import to account” („Auf Konto importieren“) on its page later.",
  "proposal.exchange_export_not_read":
    "This is an export from an exchange (BISON or Binance). Hausbuch reads it itself, without AI, onto the exchange's account: open the exchange's account under Accounts and upload the export there with “Upload the exchange's export” („Export der Börse hochladen“); if there is none yet, create an account of the kind “Exchange (crypto)” („Börse (Krypto)“).",
  "document.extraction_stale": "The document has been read again in the meantime. Please reload the page and check the fields.",
  "document.extraction_kind_mismatch": "One of the hand-typed fields does not exist for this kind of document; it cannot be saved.",
  "statement.unreconciled": "The computed closing balance does not match the bank's balance.",
  "statement.opening_mismatch": "The file's opening balance does not match the computed account balance.",
  "statement.before_anchor":
    "The statement holds transactions on or before the date of the opening balance. Move the opening balance to an earlier day.",
  "statement.unknown_format":
    "File format not recognised. Expected: camt (XML or ZIP), Sparkasse CSV-CAMT, the DKB's CSV export, a credit card's PDF statement or a depot's PDF account statement. Upload other papers under Documents.",
  "statement.unparsable": "The file is damaged or incomplete and could not be read.",
  "statement.archive_rejected":
    "The ZIP archive was refused: only XML files without folders, at most 400 files and 20 MiB, no ZIPs inside the ZIP.",
  "statement.entity_declared": "The XML file contains a DTD or entity declaration and is refused for security reasons.",
  "statement.report_unbalanced": "In a daily report, the opening balance and the transactions do not give the closing balance.",
  "statement.chain_break": "A daily report does not start with the previous one's closing balance; a day is probably missing.",
  "statement.account_mismatch":
    "The statement belongs to another account: the IBAN or customer number in the file does not match, or the file is not a statement of this kind of account (a credit card statement only onto a credit card, a bank statement only onto a bank account).",
  "statement.no_payment_iban": "This statement names no IBAN for payments.",
  "statement.unsupported_currency": "Only transactions in euros are supported.",
  "statement.empty": "The file holds no booked transactions.",
  "statement.owner_account": "No statements are imported onto an owner's private account.",
  "statement.balance_from_file": "This statement brings its own balances; no balance is typed.",
  "statement.opening_not_derivable":
    "The opening balance can no longer be derived from this statement: an earlier statement has been added, another one already balances, or the closing balance was changed. Please reload the page.",
  "statement.duplicate_accounts_differ": "The two transactions belong to different accounts.",
  "statement.duplicate_excludes_both": "Merging would take both transactions out of the balance; one must count.",
  "allocation.sum_mismatch": "The lines do not add up to the booking's amount.",
  "allocation.empty": "A categorisation needs at least one line.",
  "allocation.transaction_mismatch": "The categorisation belongs to another booking. Please reload the page.",
  "allocation.import_not_reconciled":
    "This booking's statement is not reconciled. It can be categorised only once its balances match.",
  "allocation.line_not_open":
    "This booking does not count (a merged duplicate) or is still waiting for the decision on a possible duplicate.",
  "allocation.scope_mismatch":
    "The unit, tenancy or loan does not belong to the chosen property, or the property is missing.",
  "allocation.excluded_transaction": "This booking was merged as a duplicate and does not count.",
  "rule.not_learnable":
    "Hausbuch learns no rule for this categorisation. Please confirm without “Always categorise like this” („Künftig immer so zuordnen“).",
  "rule.invalid_pattern": "The pattern is not a valid search expression (regular expression, at most 200 characters).",
  "inbox.category_not_bulk": "Payments of this category are confirmed one by one, not as a group.",
  "inbox.line_not_private": "Only payments of private accounts can be confirmed as a group. Please reload the page.",
  "allocation.category_not_for_account":
    "This category does not fit this account: a private account takes household categories only, the rental account only the property's. For an account with bookings of the other kind, please re-book those bookings first.",
  "account.kind_not_available": "This kind of account cannot be created yet.",
  "ownership.sum_not_one": "The shares must add up to exactly 1 (100 %), each owner at most once.",
  "ownership.owner_missing": "A share names no owner of this household. Please reload the page.",
  "rule.too_broad":
    "Transactions of different categories have already been confirmed under this name (e.g. PayPal). Please give a pattern from the payment reference instead.",
  "rule.conflicts":
    "A rule for this name would hit transactions that were confirmed differently. Confirm without a rule, or give a pattern from the payment reference.",
  "rule.rebook_blocked":
    "Nothing was re-booked: an earlier payment to this payee cannot be re-booked automatically (it is split, has a receipt, is linked to a property, a transfer or the depot, or its statement is not reconciled). Please reload the page: the payment is then named here and can be opened and changed by hand. Or confirm without “Always categorise like this” („Künftig immer so zuordnen“).",
  "rule.no_merchant":
    "This payment through a payment service (PayPal, Klarna, Google Pay) names no merchant, so Hausbuch learns no rule for it. Please confirm without “Always categorise like this” („Künftig immer so zuordnen“).",
  "settings.consent_switch_off": "First switch on “Categorise private accounts with AI” („Private Konten mit KI zuordnen“), then consent.",
  "settings.consent_already_recorded": "Your consent is already saved.",
  "ai.disabled":
    "The AI is not set up: it is switched off on the server, or there is no readable Anthropic key (enter it under Settings › Access keys; if it says “Cannot be read” („Kann nicht gelesen werden“) there, enter it again). Until then payments can only be categorised by hand.",
  "tax.year_not_available": "There is no draft for this property in this year (before the purchase or in the future).",
  "tax.no_rule_set": "There is no rule set for this tax year yet, so no draft.",
  "tax_input.extension_needs_herstellung":
    "An Erweiterung is left out of the 15 % limit only for a renovation with “Herstellungskosten”. While this exception applies the renovation stays Herstellungskosten; withdraw the “Erweiterung” exception first.",
  "tax_input.useful_life_not_shorter":
    "The useful life from the appraisal must be shorter than what is left of the statutory one (40 years if completed before 1925, 50 years up to 2022, 33⅓ years from 2023, less the years since the handover). Otherwise the statutory AfA rate applies.",
  "tax_input.movable_asset_year":
    "A fitted kitchen bought with the flat starts in the tax year of the purchase; one bought later at the earliest in that year.",
  "tax_input.movable_asset_exceeds_building":
    "The kitchen's value (with its share of the acquisition costs) must be smaller than the building share of the purchase price. Please check the value or the purchase price split.",
  "tax_input.movable_asset_no_split":
    "Choose a purchase price split first: a kitchen bought with the flat is taken out of the building share.",
  "tax_input.movable_asset_separate_unsupported":
    "A kitchen bought separately is not supported yet; it needs its own purchase date.",
  "tax_input.movable_asset_no_purchase_date": "Enter the property's handover date first.",
  "tax_input.price_renovation_no_purchase_date":
    "Enter the property's handover date first: the renovation only counts if it begins after the handover.",
  "tax_input.price_renovation_before_handover":
    "The renovation begins before the handover; then it is the seller's own and does not count as yours. Please check the period of the work.",
  "tax_input.price_renovation_paid_before_handover_year":
    "The payment date is in a year before the year of the handover. Please enter the day the rest of the purchase price was paid.",
  "tax_input.price_renovation_exceeds_building":
    "The renovation's amount (with a kitchen bought with the flat) must be smaller than the building share of the purchase price. Please check the amount or the purchase price split.",
  "income_input.scope_invalid":
    "This entry does not fit its record: a correction applies to the same kind, year and person, a withdrawal repeats the value.",
  "income_input.not_current":
    "There is already a valid entry of this kind for this year, or the corrected entry is no longer current. Please reload the page and change the current entry.",
  "income.assessed_result_needed":
    "An earlier year's assessment still applies; the tax effect needs the property whose result is in the assessment.",
  "tariff.no_file": "There is no income tax tariff for this year.",
  "projection.no_rule_set":
    "There is no rule set yet for this property's purchase year, so neither actuals nor a projection.",
  "scenario.sale_year_before_today": "The sale year must be after the current year: the projection starts next January.",
  "net_worth.not_month_end": "A month-end always applies to the last day of a month.",
  "net_worth.month_end_before_history":
    "Month-ends exist from January 2026, the first month of account transactions in Hausbuch.",
  "net_worth.month_end_not_closed":
    "This month is still running: its month-end can be recorded from the 1st of the next month.",
  "health.month_not_closed":
    "This month is not closed yet: transactions are missing, or something is waiting in “To review”. The open points are shown with the month.",
  "health.month_end_missing":
    "No month-end is saved yet for the end of this month. Please use “Record month-end” („Monatsabschluss festhalten“) first.",
  "health.month_end_incomplete":
    "The net worth at the month's end is still incomplete: a price, a statement or a booking is missing. What is missing is shown with the month; then the key figures can be recorded.",
  "health.range_invalid": "This period does not work: the start must be before the end, and it is at most 24 months.",
  "household_scenario.not_current":
    "This household scenario is no longer current, or there already is a default scenario. Please reload the page.",
  "household_scenario.account_invalid":
    "Only current and savings accounts and credit cards count towards the reserve. Please choose such an account.",
  "household_scenario.one_off_outside":
    "A one-off payment lies after the end of the planning period. Please choose a month within it or extend the period.",
  "household_scenario.withdrawal_invalid":
    "When withdrawing, the scenario's assumptions stay unchanged. Please reload the page.",
  "household_scenario.spending_invalid":
    "There is no measured spending for this category yet, so a change in percent does not work. Please enter an amount per month.",
  "household_scenario.budget_class_invalid":
    "This category cannot be moved between Needs and Wants: one-off spending and unclassified transactions belong to neither. Please reload the page.",
  "household_scenario.one_off_code_invalid":
    "This category cannot be marked as one-off: only the household's spending can be one-off. Please reload the page.",
  "household_scenario.goal_invalid":
    "The goal's month must lie after the current month. Please choose a later month.",
  "scenario.not_current":
    "The scenario is no longer current, or there already is a default scenario. Please reload the page.",
  "scenario.scope_invalid": "The scenario does not fit this property or its loans.",
  "flat_rate.no_letter":
    "There is nothing to tell this tenancy today: the costs have not risen by more than €10 a month since the flat rate was set, and have not fallen.",
  "bank.not_configured":
    "Bank sync is not set up: the FinTS product id is missing or can no longer be read. Please enter it under Settings › Access keys (or in the server configuration, runbook step 14).",
  "bank.rate_limited": "At most four manual syncs per hour. Please try again a little later.",
  "bank.sync_running": "A sync is already running for this account.",
  "bank.window_invalid":
    "The sync would have started after today. Either the account's opening balance is today or later: then check the account's reference date. Or the uploaded statements already reach today: then sync again tomorrow.",
  "bank.url_not_https": "The FinTS address must start with https://.",
  "bank.tan_not_decoupled":
    "The bank offers no approval by app (pushTAN) for this login. Set up S-pushTAN in online banking.",
  "bank.approval_timeout": "The approval in the S-pushTAN app did not come within five minutes.",
  "bank.account_not_found": "The bank holds no account with this account's IBAN under this login. Check the login name and the bank code.",
  "bank.state_rejected":
    "The saved connection to the bank no longer fitted and was discarded; the next sync asks for an approval again.",
  "bank.unreachable": "The bank could not be reached. Sync again later; if it persists, check the FinTS address.",
  "bank.refused":
    "The bank refused the sync: because of the login name or PIN, an approval declined in the app, or the first setup; the bank's code is shown under To review. Do not simply sync again: after three wrong PINs the bank locks the login.",
  "bank.unsupported_format": "The bank delivers transactions in no format Hausbuch can read. Keep uploading statements as files.",
  "bank.failed": "The sync failed with an unexpected error. Sync again; if it persists, upload a file.",
  "bank.pin_missing":
    "No PIN is stored for this account's login yet. Under Bank sync, enter the PIN under “PIN (online banking)” („PIN (Online-Banking)“), press “Save PIN” („PIN speichern“), then sync again.",
  "bank.pin_unreadable":
    "The PIN stored in Hausbuch can no longer be read because the server's key was changed. Under Bank sync choose “Change PIN” („PIN ändern“), enter the PIN again and sync again.",
  "bank.pin_server_changed":
    "The stored PIN must be entered once more: Hausbuch sends it only to the FinTS address it was entered for, and a PIN entered before 16.11.2026 names no address yet. Under Bank sync choose “Change PIN” („PIN ändern“), enter the PIN again and sync again.",
  "bank.login_unreadable":
    "The stored login name can no longer be read because the server's key was changed. Under Bank sync choose “Change login” („Zugang ändern“), enter the login name again and sync again.",
  "bank.pin_invalid": "The PIN has 5 to 64 characters.",
  "bank.history_missing": "First upload the statement export for 2026, then connect the account.",
  "bank.auto_sync_waits":
    "The daily sync can be switched on only after two manual syncs in a row ran without an approval in the DKB app; otherwise a request would reach the phone every morning.",
  "tax_export.too_large":
    "The year's receipts together are larger than 100 MiB. Please split the archive by hand; the filing aid still works.",
  "tax_export.failed": "The export failed. Please try again.",
  "tax_export.kind_invalid":
    "This kind of export does not exist here: the Anlage N field sheet per owner, the filing aid and the archive per property.",
  "return_input.scope_invalid":
    "This entry does not fit its record: a change applies to the same kind, year and person, a withdrawal repeats the value, and periods lie within the tax year.",
  "return_input.not_current":
    "There is already a valid entry of this kind for this year, or the changed entry is no longer current. Please reload the page and change the current entry.",
  "return_input.work_room_conflict":
    "A home study that is the centre of the work all year rules out the home-office daily flat rate. Remove the home-office days first, or give fewer than 12 months.",
  "return_input.carried_from_invalid": "This entry cannot be carried over from the previous year.",
  "anlage_n.no_pay":
    "No pay is recorded for this year yet (Lohnsteuerbescheinigung, payslip or expected gross pay).",
  "anlage_n.no_pay_declared":
    "“No pay” is stated for this year: there is no Anlage N. If there was pay after all, withdraw the statement under Taxes › Your details.",
  "anlage_n.useful_life_missing": "For other work equipment, please give the useful life in months.",
  "anlage_n.work_room_excludes_day_rate": "A home study for the whole year and the home-office daily flat rate rule each other out.",
  "return.childcare_age": "Childcare costs count only up to the child's 14th birthday.",
  "return.household_services_cash": "Craftsmen's or household services paid in cash do not count (§ 35a): only bank transfers.",
  "depot.account_required": "Please choose a depot as the account; securities are booked on a depot only.",
  "depot.movement_exists":
    "This transaction (same reference, or same day and amount) is already booked on the depot. The document was probably uploaded twice; skip this entry.",
  "depot.statement_trade_differs":
    "This purchase or sale is already booked from the depot's cash statement, with other units or another amount than this trade confirmation. Nothing was changed; please compare both documents.",
  "depot.line_books_trade":
    "This line of the depot's cash statement booked a purchase or sale on the depot. It stays booked as a securities purchase or sale and is not merged as a duplicate, or its units would keep counting without a matching booking. If the trade is wrong, please upload the trade confirmation.",
  "depot.holding_exists": "This report has already been applied for this depot and this reference date. Skip this entry.",
  "depot.holdings_mismatch":
    "The depot report names other unit counts than the booked trade confirmations give. Please upload the missing trade confirmations.",
  "asset.not_crypto": "A CoinGecko id or a Kraken pair exists for cryptocurrencies only. For a fund, please use “Enter price” („Kurs eintragen“).",
  "asset.is_euro": "The euro always has the price 1; no price is entered for it.",
  "exchange.unreadable":
    "This file is not an export from BISON or Binance, or a line in it cannot be read. Please upload the exchange's transaction export unchanged (CSV or the ZIP file it comes in).",
  "exchange.binance_spot_history":
    "This is Binance's spot trade history. Hausbuch does not need it: please upload the “Transaction History” and the withdrawal history.",
  "exchange.binance_ambiguous_trade":
    "Two trades in the same second cannot be told apart. Please download the export again for a shorter period.",
  "exchange.transactions_first":
    "This is the withdrawal history. Please upload the transaction history first, then the withdrawal history.",
  "exchange.account_mismatch": "This file comes from another exchange than this account. Please upload it on the right exchange account.",
  "exchange.not_an_exchange": "This account is not an exchange account.",
  "exchange.balance_unknown_asset":
    "There is no movement in this export for one of the coins. Please enter only the coins the form shows.",
  "exchange.balance_missing": "Please enter the exchange's balance for every coin the export shows as held.",
  "exchange.provider_unknown": "Please choose BISON or Binance as the exchange.",
  "exchange.answer_invalid": "This answer does not fit this movement (incoming or outgoing).",
  "exchange.movement_done": "This movement is already confirmed or answered.",
  "exchange.unreconciled":
    "The exchange's balance differs from the imported movements. Is part of the export missing, or was a balance misread?",
  "exchange.withdrawal_history_missing":
    "Binance names a withdrawal's network and transaction hash only in the withdrawal history. Please upload it.",
  "exchange.time_zone_unknown":
    "A Binance export names its time zone only in its file name, and this name named none. Its times were read as UTC and may be off by hours, so that a withdrawal stands before the purchase that paid for it.",
  "exchange.withdrawal_history_no_match":
    "The withdrawal history holds no completed withdrawal of the same coin, within an hour before, whose amount plus fee equals the amount sent.",
  "exchange.deposit_question": "Is this deposit a bonus, a reward, or did it come from an own wallet?",
  "exchange.correction_not_unreconciled":
    "No balance differs for this export. A correction is only possible while a coin differs.",
  "exchange.correction_not_latest":
    "A later export has already been imported after this one. Please book the correction on the newest export, otherwise it changes that export's reconciliation unnoticed.",
  "exchange.correction_eur":
    "There is no correction for euros: euro amounts reconcile within rounding. Please check and re-enter the euro balance.",
  "exchange.correction_no_difference": "This coin matches the exchange's balance; there is nothing to correct.",
  "exchange.correction_stale":
    "The movements have changed since the balance was entered. Please re-enter the exchange's balance and then book the difference.",
  "app_secret.invalid":
    "This value is not saved: it is empty, too long (at most 512 characters) or holds characters that do not fit here (such as spaces in a key or an email address without @). Please copy the value unchanged from the provider's account.",
  "app_secret.url_invalid":
    "This is not a valid address. It must start with https:// (or http://) and contain a server name, as the provider gives it.",
  "wallet.key_unreadable":
    "The key stored under Settings › Access keys can no longer be read (the server has a new session key). Please enter the key there again.",
  "wallet.address_invalid":
    "This is not a valid address for the chosen chain. Please copy the public address unchanged from the wallet; the format is shown under the field.",
  "wallet.address_taken":
    "This address is already entered: on another own account, or on this wallet for every chosen chain. A retired address can be brought back in the list with “Reactivate” („Wieder aktivieren“).",
  "wallet.change_answered":
    "This question has already been answered or has changed, for example through another answer. Please reload the page and answer the current question.",
  "wallet.original_missing":
    "The data of this payment was not saved when it was fetched, so the question cannot be answered here. The payment stays as an open question on the wallet.",
  "transfer.units_differ":
    "Another quantity arrived on the other account than left here: part of the coins went to another address in the same transaction. So the two are not a transfer; the transaction is answered on the wallet's page.",
  "wallet.group_done": "This transaction is already answered or confirmed. Please reload the page.",
  "wallet.group_needs_answer": "This transaction needs an answer before it can be booked: please choose what it was.",
  "wallet.answer_unfit":
    "This answer does not fit the transaction (such as “Payment” for an incoming one, or “Spam” for the blockchain's own currency).",
  "holdings_export.year_open":
    "The holdings statement for 31 Dec can be created only from 1 January of the next year, once the day is over.",
  "holdings_export.nothing_held":
    "Up to 31 Dec of this year there is no confirmed movement on any wallet or exchange; there is nothing to show.",
  "document.delete_blocked.statement_import": "Transactions were imported from this statement; they stay unchanged in the account.",
  "document.delete_blocked.bank_statement": "It is a statement the bank delivered through the bank sync; it belongs in the yearly archive.",
  "document.delete_blocked.movement_import": "Depot or exchange movements were imported from this file; they stay unchanged.",
  "document.delete_blocked.asset_movement": "An imported depot or exchange movement names this document as its source.",
  "document.delete_blocked.holding_statement": "A depot holding (holdings report) rests on this document.",
  "document.delete_blocked.price": "A saved price rests on this document.",
  "document.delete_blocked.allocation": "A booking names this document as its receipt.",
  "document.delete_blocked.applied_proposal": "Values from its suggestion have already been applied.",
  "document.delete_blocked.link":
    "The document is linked. First remove every link below under “Links” („Verknüpfungen“), then it can be deleted.",
  "document.delete_blocked.linked_from_document":
    "Another document (e.g. a proof of payment) is linked to this one. First remove the link there.",
  "document.delete_blocked.acquisition_cost":
    "It is the receipt of an acquisition cost line. First give the line another receipt under the property's purchase.",
  "document.delete_blocked.price_split": "It proves a purchase price split of the property.",
  "document.delete_blocked.rent_schedule": "It proves a rent change.",
  "document.delete_blocked.tax_input": "A tax detail rests on this document.",
  "document.delete_blocked.income_input": "An income detail (e.g. from a tax assessment) rests on this document.",
  "document.delete_blocked.return_input":
    "An income tax detail (e.g. from a Lohnsteuerbescheinigung or a payslip) rests on this document.",
  "document.delete_blocked.tax_export": "It is part of a tax export already made (filing aid or yearly archive), which stays unchanged.",
  "document.delete_blocked.inbound_mail": "It came as the attachment of a forwarded email; that email's list stays unchanged.",
  "document.delete_blocked.other_proposal":
    "An open suggestion of another document names this one. First apply or discard that suggestion.",
  "document.delete_blocked.ai_call": "An AI reading of this document is named as a source elsewhere.",
};

export const AI_ERROR_TEXT_EN: Record<string, string> = {
  "ai.disabled":
    "Automatic reading is not set up: the AI is switched off on the server, or there is no readable Anthropic key (enter it under Settings › Access keys; if it says “Cannot be read” („Kann nicht gelesen werden“) there, enter it again). Then read the document again.",
  "ai.blocked_tenant_data": "Documents with tenant data are not sent to the AI until that is allowed.",
  "ai.unparsable": "The AI's answer could not be read even on the second attempt.",
  "ai.refused": "The AI refused to read it, or its answer was too long again on the second attempt with more room.",
  "ai.too_many_pages": "The document has too many pages to be read automatically.",
  "ai.unsupported_media_type": "This file type cannot be read automatically (e.g. HEIC); please upload it as PDF, JPEG or PNG.",
  "ai.api_error": "The AI service answered the request with an error.",
  "ai.unavailable": "The AI service could not be reached. Read it again later.",
  "ai.unreadable": "The file could not be opened (damaged or password-protected).",
  "ai.internal_error": "An internal error occurred while reading.",
  "document.adobe_form":
    "This PDF is an Adobe form and can only be shown in Adobe Reader. Open it there, print it as a PDF and upload the copy.",
  "ai.blocked_private_data":
    "This is a document of your depot. It goes to the AI only once “Categorise private accounts with AI” („Private Konten mit KI zuordnen“) is switched on under Settings; then press “Read again” („Neu auslesen“) here.",
  "proposal.bank_file_not_read":
    "This is a bank statement or transaction export, a credit card statement or a statement of the depot's cash account. Hausbuch reads the file itself, without AI, onto its account: with “Import to account” („Auf Konto importieren“) on this document's page or under To review, straight from the stored file, without uploading it again. If the account does not exist yet, “Create account and import” („Konto anlegen und einlesen“) creates it from the file.",
  "proposal.exchange_export_not_read":
    "This is an export from an exchange (BISON or Binance). Hausbuch reads it itself, without AI, onto the exchange's account: open the exchange's account under Accounts and upload the export there with “Upload the exchange's export” („Export der Börse hochladen“); if there is none yet, create an account of the kind “Exchange (crypto)” („Börse (Krypto)“).",
};
