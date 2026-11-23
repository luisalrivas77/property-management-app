// The English beside components/todos.tsx (phase 10 D284, step 4c). todos.tsx keeps its three
// German guides as they are (test_todo_guides.py reads them); this file holds, per code, only
// the words: a to-do's title and its Was tun, a checklist row's title, hint and Was tun, a
// reminder's Was tun, and the label of each link or upload button. The actions themselves stay
// in todos.tsx, so a target or a button never differs between the languages.
// test_i18n_frontend_catalogues.py checks that every German code has its English here.
// Tax and official German words stay German with a gloss (glossary.md).
import type { DocumentChecklistItem, DraftWarning } from "@/api/schemas";
import type { components } from "@/api/types";
import { formatter } from "@/i18n/format";

import { ACQUISITION_COST_KIND, ALLOCATION_CATEGORY, proofBill } from "./labels";

type Params = Record<string, string>;
type Reminder = components["schemas"]["Reminder"];

const money = (value: string | null | undefined) => formatter("en").money(value);
const date = (value: string | null | undefined) => formatter("en").date(value);

export interface WarningEn {
  title?: (params: Params) => string;
  text: (params: Params, warning?: DraftWarning) => string;
}

const NOTHING_FORM = "Nothing to do: as soon as the form is published, the app adjusts the lines.";

export const WARNING_EN: Record<string, WarningEn> = {
  year_end_straddle: {
    text: () => "Nothing to do: the app counts the payment in the right year.",
  },
  "afa.no_split": {
    title: () => "Building share of the purchase price missing",
    text: () =>
      "Only the building can be depreciated (AfA), not the land. For that the app needs the building's share of the purchase price; it is usually in the purchase contract (Kaufvertrag). If the contract has already been read, apply the split from there; otherwise upload the contract.",
  },
  "afa.cost_row_unpaid": {
    text: () =>
      "Nothing to do if you paid these costs from another account: they count anyway. If the payment came from the rental account, categorise it in To review as a purchase cost (Nebenkosten).",
  },
  "payment_proof.missing": {
    title: (p) => {
      const bill = proofBill(p);
      return `Proof of payment missing: ${bill.charAt(0).toUpperCase()}${bill.slice(1)}`;
    },
    text: (p) =>
      p.proof_offer
        ? `A proof of payment already uploaded${p.proof_offer_amount ? ` for ${money(p.proof_offer_amount)}` : ""} is not linked to any invoice yet and fits this one. “Link existing proof” („Vorhandenen Nachweis zuordnen“) links it to this invoice (you are asked first); if the proof shows the invoice was paid in another year, the app then proposes the right year. If it is not the right proof, upload the matching receipt.`
        : p.proof_rest
          ? `Part of it is proven; the proof of ${money(p.proof_rest)} is still missing. Upload the transfer receipt or the payment details of that payment as a PDF here; the invoice counts in the meantime anyway.`
          : p.proof_amount
            ? `The uploaded proof is for ${money(p.proof_amount)}, the invoice for ${money(p.amount)}. If it was paid in parts, upload the receipt of the other payment too; the invoice counts in the meantime anyway.`
            : "The invoice already counts; only the receipt that it was paid is missing. In your online banking, save the transfer receipt or the payment details of this one payment as a PDF and upload it here, not the whole statement. The app reads the date and amount; if the invoice was paid in another year, it proposes the right year.",
  },
  "tax.price_renovation_ask": {
    title: () => "Did the Finanzamt accept the renovation in the purchase price?",
    text: (p) =>
      `The ${p.tax_year ?? ""} income tax assessment (Einkommensteuerbescheid) is confirmed. Check in the assessment or its notes whether the renovation in the purchase price${p.amount ? ` (${money(p.amount)})` : ""} was accepted as maintenance costs (Erhaltungsaufwand). “Yes” ends the question. “No” counts the amount as part of the purchase price again, and so in the depreciation (AfA); the earlier drafts stay in the history.`,
  },
  "tax.price_renovation_not_applied": {
    title: () => "Renovation in the purchase price does not count",
    text: (p) =>
      p.reason === "before_handover"
        ? "According to the entry, the work begins before the handover; then it is the seller's own. Check and correct the period of the renovation, or withdraw the entry."
        : p.reason === "paid_before_handover_year"
          ? "The payment date is in a year before the year of the handover. Enter the day the rest of the purchase price was paid, or withdraw the entry."
          : "The amount is not below the building's share of the purchase price, which cannot be right. Check and correct the amount, or withdraw the entry.",
  },
  "afa.acquisition_cost_without_row": {
    title: () => "Purchase cost not entered",
    text: () =>
      "Under Nebenkosten (purchase costs), enter what this payment was for (for example notary, land register, broker or Grunderwerbsteuer), with the invoice amount. Then it counts.",
  },
  "afa.purchase_price_mismatch": {
    text: () =>
      "Compare the purchase price in the property details with the purchase contract. If it is right, there is nothing to do (the bank often pays part directly to the seller).",
  },
  "afa.useful_life_report": {
    text: () =>
      "Nothing to do in the app. When filling in ELSTER, the filing aid shows where the box for the expert report (Gutachten) goes; keep the report at hand in case the tax office asks for it.",
  },
  "afa.movable_asset_no_evidence": {
    text: () =>
      "An estimate is enough. If you have a basis for the value (quote, invoice, printout of the kitchen planner), upload it and choose it below as the fitted kitchen's receipt.",
  },
  "afa.movable_asset_exceeds_building": {
    title: () => "Fitted kitchen worth more than the building",
    text: () =>
      "The fitted kitchen's value is higher than the building share, which cannot be right. Check the value below at the fitted kitchen, or the purchase price split.",
  },
  "limit.exceeded": {
    text: () => "Nothing to do: the app already depreciates these renovation costs over the years.",
  },
  "limit.prior_year_amendment": {
    title: (p) => `Return ${p.exceeded_in_year ? `before ${p.exceeded_in_year}` : "of an earlier year"} changes`,
    text: () =>
      "If the tax return for that year has already been filed, send the tax office the new figures; the filing aid below shows them. If it has not been filed yet, there is nothing more to do.",
  },
  "limit.estimated_net": {
    text: (_p, warning) =>
      (warning?.refs ?? []).some((r) => r.kind === "tax_input")
        ? "These costs were paid from another account. For an exact calculation, enter the net amount from the invoice at this entry in the Jahresfragen; without it the app assumes 19 % VAT."
        : "For an exact calculation, upload the invoice for the payment and link it; without it the app assumes 19 % VAT.",
  },
  "limit.private_cost_undated": {
    title: (p) => `When was ${p.label ? `“${p.label}”` : "the repair"} done?`,
    text: () =>
      "This repair was paid from another account. In the first three years after the purchase, repairs count as costs at once only up to a limit; above it they are depreciated over the years. Without the date of the work, the app counts it to be safe. In the Jahresfragen, enter the date of the work from the invoice at this entry, and the net amount if the invoice shows it.",
  },
  "limit.private_cost_after_window": {
    title: (p) => `When was ${p.label ? `“${p.label}”` : "the repair"} done?`,
    text: () =>
      "This repair was paid from another account and is dated shortly after the end of the first three years after the purchase, often with the invoice date. If the work was done within those three years, it counts towards the 15 % limit. In the Jahresfragen, enter the date of service from the invoice at this entry.",
  },
  "anschaffungsnah.performance_date_check": {
    title: () => "When was the work done?",
    text: () => "Look up on the invoice when the work was carried out, and enter that period at the payment.",
  },
  "renovation.undecided": {
    title: () => "Classify the renovation",
    text: () =>
      "At the payment, say what was done: was something existing repaired or renewed (deductible at once), or was something new added that did not exist before (then depreciated over the years)?",
  },
  "interest.certificate_mismatch": {
    text: () =>
      "Usually nothing to do: an instalment around the turn of the year counts in the year it was paid for. If the difference is larger than one instalment, compare the interest certificate (Zinsbescheinigung) with the booked interest at the loan.",
  },
  "hausgeld.abrechnung_pending": {
    title: () => "Hausgeldabrechnung (owners' association statement) missing",
    text: () =>
      "Upload the year's Hausgeldabrechnung as soon as the property manager sends it (usually the following spring). The app reads what was spent from the reserve and includes it.",
  },
  "income.other_review": {
    title: (p) =>
      p.counted_as_rent === "1" ? "Counted as rent, but booked as other income" : "Income without a category",
    text: (p) =>
      p.counted_as_rent === "1"
        ? "For tax this payment already counts as rent. One click on “Book as rent” („Als Miete buchen“) books it as cold rent too."
        : "If it was rent, for example rent forwarded by the seller, choose “Book as rent” („Als Miete buchen“). Otherwise open the payment and say what it was, for example a deposit or a refund. Then it counts correctly.",
  },
  "income.pre_handover": {
    title: () => "Income before the handover",
    text: () =>
      "This income arrived before the flat was handed over to you. If you were already named as landlord in the lease in those months, it is your rent: then choose “Count as rent” („Als Miete zählen“).",
  },
  "income.living_area_missing": {
    title: () => "Living area of the flat missing",
    text: () =>
      "The living area of the whole flat is missing. Either upload the living-area calculation (Wohnflächenberechnung): Hausbuch reads it and proposes your flat's area to apply. Or enter the area here; it is in the purchase contract or the living-area calculation. The rooms' areas are not enough, because kitchen, bathroom and hall are missing.",
  },
  "umlagen.mixed_terms": {
    text: () =>
      "For each tenancy, check how the running costs (Nebenkosten) are agreed (prepayment, flat rate or included in the rent). If that is right, there is nothing to do.",
  },
  "reduced_rent.no_market_rent": {
    title: () => "Local comparable rent missing",
    text: () =>
      "Look up in the city's rent index (Mietspiegel) how much cold rent per m² is usual for such a flat, and enter the value at the unit. Then the app checks whether your rent is high enough for all costs to count.",
  },
  "reduced_rent.prognosis_needed": {
    text: () =>
      "Nothing to do in the app. First check the rent index value at the unit. If it is right and the tax office asks for a forecast, Planning shows the figures of the coming years.",
  },
  "reduced_rent.partial": {
    title: () => "Rent below half of the usual rent",
    text: () =>
      "First check the rent index value at the unit. If it is right, the tax office cuts the costs; the filing aid names the percentage to enter in ELSTER.",
  },
  "draft.open_lines_excluded": {
    title: (p) => `${p.count ?? "Some"} ${p.count === "1" ? "payment" : "payments"} not confirmed yet`,
    text: () => "Confirm the open payments in “To review”; then they are in the draft.",
  },
  "draft.form_provisional": { text: () => NOTHING_FORM },
  "draft.allocation_without_property": {
    title: () => "Payment without a property",
    text: () => "Open the payment in “To review” and assign it to the right property.",
  },
  "archive.finanzamt_copy_needed": {
    text: () =>
      "Only needed if the tax office asks for receipts: open the document in Adobe Reader, print it as a PDF and send that copy.",
  },
  "tax.travel_frequency": {
    text: () =>
      "Check the number of trips. If you were there almost every day, only half the flat rate applies; otherwise there is nothing to do.",
  },
  "tax.private_cost_paid_from_account": {
    title: () => "Invoice entered twice",
    text: () =>
      "The invoice was paid from the rental account and already counts with that booking. In the Jahresfragen, remove the entry among the costs paid from another account.",
  },
  "anlage_n.certificate_missing": {
    title: () => "Lohnsteuerbescheinigung (wage tax certificate) missing",
    text: () =>
      "Upload the Lohnsteuerbescheinigung as soon as the employer sends it (usually by the end of February). The app reads it; its figures then replace the estimate.",
  },
  "anlage_n.church_mismatch": {
    title: () => "Church tax does not match",
    text: () =>
      "Check on the Owners page whether church tax is set correctly. Whether church tax was deducted is on the Lohnsteuerbescheinigung.",
  },
  "anlage_n.tax_class_6": {
    text: () => "Nothing to do: the app enters the second job in its own column.",
  },
  "anlage_n.periods_overlap": {
    title: () => "Certificate entered twice?",
    text: () =>
      "Check whether the same certificate was entered twice or a corrected one replaces the old one. Open the extra one under Documents and withdraw it.",
  },
  "anlage_n.days_implausible": {
    title: () => "Working days do not add up",
    text: () =>
      "Under the inputs, check the home-office days and the days in the office: together at most as many as you worked in the year.",
  },
  "anlage_n.home_office_capped": {
    text: () => "Nothing to do: the tax office accepts no more in a year.",
  },
  "anlage_n.too_many_routes": {
    text: () => "Add the remaining routes by hand in ELSTER; the filing aid lists them all.",
  },
  "anlage_n.receipt_missing": {
    text: () =>
      "Only needed if the tax office asks: keep the invoice, or attach it as the receipt of the expense under the inputs.",
  },
  "anlage_n.form_provisional": { text: () => NOTHING_FORM },
  "anlage_n.payslip_gap": {
    title: () => "Payslip missing",
    text: () => "Upload the missing payslip; the app reads it.",
  },
  "anlage_n.no_pay_overruled": {
    title: () => "Undo “No employment income”",
    text: () =>
      "A Lohnsteuerbescheinigung or payslip exists for this year, and it counts. Undo the entry “No employment income” („Kein Arbeitslohn“) so it no longer contradicts it.",
  },
  "anlage_n.payslip_no_ytd": {
    text: () => "Nothing to do; it becomes more exact with the next payslip or the Lohnsteuerbescheinigung.",
  },
  "anlage_n.work_room_records": {
    text: () => "Keep the floor plan and the invoices; you do not need to upload anything.",
  },
  "anlage_n.work_room_centre": {
    text: () =>
      "Check: do you work almost only in this room? If not, remove the home study (Arbeitszimmer) under the inputs; then the home-office days count instead.",
  },
  "anlage_n.work_room_partial": {
    text: () => "For the home-office days, enter only days from the months without a home study.",
  },
  "anlage_n.first_workplace_check": {
    text: () => "Ask the employer which workplace it set as the first one, and enter the route there under the inputs.",
  },
  "return.progression_provisional": {
    title: () => "Notice of wage-replacement benefit missing",
    text: () =>
      "As soon as the notice of the Elterngeld, Krankengeld or Arbeitslosengeld has arrived, enter the amount under the inputs at the wage-replacement benefits (Lohnersatzleistungen).",
  },
  "return.benefit_possible_double": {
    title: () => "Wage-replacement benefit entered twice",
    text: () =>
      "The benefit is already on the Lohnsteuerbescheinigung. Remove your own entry under the inputs at the wage-replacement benefits.",
  },
  "return.benefit_booked": {
    title: (p) => `Enter wage-replacement benefits (Lohnersatzleistungen) ${p.tax_year ?? ""}`,
    text: (p) =>
      p.owner_id
        ? `${money(p.difference)} of Elterngeld, Mutterschaftsgeld, Krankengeld or Arbeitslosengeld arrived on the account and is not entered as a wage-replacement benefit yet. It raises the tax rate on the other income. The amount proposed is what arrived on the account. For Krankengeld the health insurer's certificate shows a higher amount: enter that one. Check the amount and save it at the wage-replacement benefits.`
        : `${money(p.difference)} of Elterngeld, Mutterschaftsgeld, Krankengeld or Arbeitslosengeld arrived on a joint account and is not entered as a wage-replacement benefit yet. It raises the tax rate on the other income. The amount proposed is what arrived on the account. For Krankengeld the health insurer's certificate shows a higher amount: enter that one. First choose the person who received the benefit, then check the amount and save it.`,
  },
  "return.children_mismatch": {
    text: () =>
      "Under Taxes › Your details, check at Children whether all children are entered. If so, there is nothing to do.",
  },
  "return.kindergeld_apply": {
    title: () => "Apply for Kindergeld",
    text: () =>
      "Apply for Kindergeld (child benefit) at the Familienkasse and then enter at the child from when it is paid.",
  },
  "return.child_over_18": {
    text: () =>
      "If the child is in training or at university, state that in ELSTER in the Anlage Kind; the app does not calculate it. Otherwise there is nothing to do.",
  },
  "return.childcare_until_14": {
    text: () => "For the childcare costs, enter only the costs up to the 14th birthday.",
  },
  "return.donation_over_cap": {
    text: () => "Nothing to do: the app counts only the allowed part.",
  },
  "return.filing_required": {
    text: () => "File the tax return by the deadline; the deadline is in the overview above.",
  },
  "return.questionnaire_open": {
    title: () => "Jahresfragen open",
    text: () => "Answer the open Jahresfragen (yearly questions); they usually take only a few minutes.",
  },
  "return.not_modelled": {
    text: () => "Add these parts by hand in ELSTER; the estimate does not know them.",
  },
  questionnaire_provisional: {
    text: () => "Nothing to do: as soon as the year's questions are settled, the app takes them over.",
  },
  "tax_export.questions_open": {
    title: () => "Jahresfragen open",
    text: () => "Answer the open Jahresfragen and then create the export again.",
  },
};

export interface RequirementEn {
  title: (item: DocumentChecklistItem, year: number) => string;
  hint: string;
  missing: string;
  expected?: string;
}

function scopeSuffix(item: DocumentChecklistItem): string {
  const scope = item.scope;
  if (scope.type === "loan") return scope.lender ? ` · ${scope.lender}` : "";
  if (scope.type === "tenancy") return ` · ${scope.unit_label ?? "Unit"}, from ${date(scope.tenancy_start)}`;
  return "";
}

export const REQUIREMENT_EN: Record<string, RequirementEn> = {
  purchase_contract: {
    title: () => "Purchase contract (Kaufvertrag)",
    hint: "Basis of the AfA and the speculation period",
    missing:
      "Upload the purchase contract from the notary and link it to the property. The app reads the purchase price, the dates and the building's share from it.",
  },
  price_split: {
    title: () => "Purchase price split",
    hint: "Building share as the AfA basis",
    missing:
      "Set which part of the purchase price falls on the building. It is usually in the purchase contract; otherwise the Federal Ministry of Finance's calculation aid (BMF-Arbeitshilfe) or an expert report helps.",
  },
  acquisition_cost_evidence: {
    title: (item) =>
      `Receipt ${item.scope.cost_kind ? ACQUISITION_COST_KIND[item.scope.cost_kind] : "purchase costs"} ${money(item.scope.cost_amount)}`,
    hint: "Purchase costs belong in the AfA basis",
    missing: "Upload the invoice for these purchase costs and choose it as the receipt under Nebenkosten.",
  },
  grundsteuerbescheid: {
    title: () => "Grundsteuerbescheid (property tax notice)",
    hint: "Applies until a new notice arrives",
    missing: "Upload the municipality's Grundsteuerbescheid and link it to the property.",
    expected: "Not due yet: the municipality usually sends it at the start of the year. Then upload and link it.",
  },
  loan_contract: {
    title: (item) => `Loan contract${scopeSuffix(item)}`,
    hint: "Interest is deductible (Werbungskosten), repayment is not",
    missing: "Upload the loan contract and link it to the loan.",
  },
  zinsbescheinigung: {
    title: (item, year) => `Interest certificate (Zinsbescheinigung) ${year}${scopeSuffix(item)}`,
    hint: "The bank usually sends it in January",
    missing:
      "Upload the bank's interest certificate and link it to the loan; the app compares the booked interest with it.",
    expected: "Not due yet: the bank usually sends it in January. Then upload it and link it to the loan.",
  },
  lease: {
    title: (item) => `Lease${scopeSuffix(item)}`,
    hint: "Basis of the rental income",
    missing: "Upload the signed lease and link it to the tenancy.",
  },
  handover_protocol: {
    title: (item) => `Handover protocol${scopeSuffix(item)}`,
    hint: "Condition of the flat at move-in",
    missing: "Upload the handover protocol from the move-in and link it to the tenancy.",
  },
  deposit_record: {
    title: (item) => `Deposit proof${scopeSuffix(item)}`,
    hint: "Deposit account, guarantee or the payment on the account",
    missing:
      "Upload a proof of the deposit (deposit account or guarantee) and link it to the tenancy. If the deposit arrived on the account, the payment is enough: assign it to this tenancy.",
  },
  wirtschaftsplan: {
    title: (_, year) => `Wirtschaftsplan (owners' association budget) ${year}`,
    hint: "For the exact split of the Hausgeld",
    missing:
      "Upload the owners' association's Wirtschaftsplan and link it to the property; the app splits the Hausgeld by it.",
    expected: "Not due yet: the property manager sends it before or at the start of the year. Then upload and link it.",
  },
  hausgeldabrechnung: {
    title: (_, year) => `Hausgeldabrechnung (owners' association statement) ${year}`,
    hint: "Usually arrives in the spring of the following year",
    missing: "Upload the Hausgeldabrechnung and link it to the property.",
    expected:
      "Not due yet: the property manager usually sends it in the spring of the next year. Then upload and link it.",
  },
  cost_receipt: {
    title: (item) =>
      `Receipt ${item.scope.category ? ALLOCATION_CATEGORY[item.scope.category] : "payment"} ${date(item.scope.booking_date)}`,
    hint: "Invoice for the payment, in case the tax office asks",
    missing: "Upload the invoice for this payment and link it to the payment as its receipt.",
  },
  market_rent: {
    title: (item) =>
      `Local comparable rent${item.scope.type === "unit" ? ` · ${item.scope.unit_label ?? "Unit"}` : ""}`,
    hint: "Rent index value to compare with the agreed rent",
    missing:
      "Look up in the city's rent index (Mietspiegel) how much cold rent per m² is usual, and enter the value at the unit, with the rent index as the receipt.",
  },
};

export interface ReminderEn {
  text: (reminder: Reminder) => string;
}

export const REMINDER_EN: Record<string, ReminderEn> = {
  "grundsteuer.instalment": {
    text: (r) =>
      r.label_code.endsWith("sepa_mandate")
        ? "Check on the account whether the municipality has collected the Grundsteuer; then “Done” („Erledigt“)."
        : "Transfer the Grundsteuer to the municipality (amount and reference are under Grundsteuer); then “Done” („Erledigt“).",
  },
  "checklist.due": {
    text: () => "Upload and link the document as soon as it arrives; then the date disappears by itself.",
  },
  "tax.return_deadline": {
    text: () =>
      "Under Taxes, settle the open items, download the filing aid and file the return in ELSTER; then “Done” („Erledigt“).",
  },
  "tax.questionnaire_open": {
    text: () => "Answer the Jahresfragen (yearly questions); they usually take only a few minutes.",
  },
  "tax.wage_certificate_missing": {
    text: () => "Upload the employer's Lohnsteuerbescheinigung on the Anlage N; the app reads it.",
  },
  "loan.fixed_rate_end": {
    text: () =>
      "Get an offer for the period after it from your own and from other banks, and enter the new rate at the loan.",
  },
  "loan.special_termination": {
    text: () =>
      "Only check: if interest rates are clearly lower today, refinancing can pay off. Otherwise there is nothing to do.",
  },
  "property.speculation_end": {
    text: () => "Nothing to do: from this day a gain from a sale stays tax-free.",
  },
  "tenancy.settlement_deadline": {
    text: () =>
      "Prepare the year's service-charge settlement and deliver it to the tenant before the deadline ends; after it no back-payment can be claimed.",
  },
  "tenancy.rent_missing": {
    text: () =>
      "Check on the account whether the rent did arrive (then categorise it in To review); otherwise ask the tenant.",
  },
  "bank.sync_stale": {
    text: () => "Open the account and start the sync again; often the approval has to be confirmed in the banking app.",
  },
  "bank.statement_due": {
    text: (r) =>
      `The statements in Hausbuch reach until ${date(String(r.label_args?.covered_until ?? ""))}; the next one is missing. Upload it with “Upload statement” („Kontoauszug hochladen“) on the account's page; then the date disappears by itself.`,
  },
  "crypto.holdings_statement": {
    text: (r) =>
      `Under Assets › Crypto, click “Create holdings statement 31.12.${String(r.label_args?.year ?? "")}” („Bestandsnachweis … erstellen“): Hausbuch records each wallet's and each exchange's holdings at the year's end with the open lots as a PDF and CSV. Then the date disappears by itself.`,
  },
};

/** The link and button labels of todos.tsx, by their German text. */
export const LABEL_EN: Record<string, string> = {
  "Arbeitsweg eintragen": "Enter the commute",
  "Arbeitszimmer prüfen": "Check the home study",
  "Bei der Einheit eintragen": "Enter at the unit",
  "Beim Darlehen verknüpfen": "Link at the loan",
  "Beim Mietverhältnis verknüpfen": "Link at the tenancy",
  "Beleg unter Nebenkosten zuordnen": "Assign the receipt under Nebenkosten",
  "Darlehen ansehen": "View loans",
  "Darlehen öffnen": "Open loan",
  "Eigentümer öffnen": "Open Owners",
  "Einbauküche prüfen": "Check the fitted kitchen",
  "Eingaben ansehen": "View inputs",
  "Gehaltsabrechnung hochladen": "Upload payslip",
  "Hausgeldabrechnung hochladen": "Upload Hausgeldabrechnung",
  "Homeoffice-Tage prüfen": "Check home-office days",
  "Jahresfragen beantworten": "Answer the Jahresfragen",
  "Kaufpreis ansehen": "View purchase price",
  "Kaufpreisaufteilung wählen": "Choose the purchase price split",
  "Kinder ansehen": "View children",
  "Kinderbetreuung ansehen": "View childcare",
  "Lohnersatzleistung eintragen": "Enter wage-replacement benefit",
  "Lohnersatzleistungen ansehen": "View wage-replacement benefits",
  "Lohnsteuerbescheinigung hochladen": "Upload Lohnsteuerbescheinigung",
  "Mietspiegel-Wert eintragen": "Enter the rent index value",
  "Mietspiegel-Wert prüfen": "Check the rent index value",
  "Mietverhältnis öffnen": "Open tenancy",
  "Nebenkosten ansehen": "View Nebenkosten",
  "Renovierung im Kaufpreis öffnen": "Open the renovation in the purchase price",
  "Renovierung im Kaufpreis prüfen": "Check the renovation in the purchase price",
  "Steuerjahr eintragen": "Enter the tax year",
  "Unter Dokumente verknüpfen": "Link under Documents",
  "Unter Nebenkosten eintragen": "Enter under Nebenkosten",
  "Unterlagen ansehen": "View documents",
  "Zahlung öffnen": "Open payment",
  "Zahlung öffnen und einordnen": "Open and classify the payment",
  "Zahlungsnachweis hochladen": "Upload proof of payment",
  "Zu prüfen öffnen": "Open To review",
  "Zur Ausfüllhilfe": "To the filing aid",
  "Zur Einbauküche": "To the fitted kitchen",
  "Angabe „Kein Arbeitslohn“ aufheben": "Undo “No employment income”",
};
