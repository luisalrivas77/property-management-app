// The fields each form of Steuern › „Eure Angaben“ renders, by the form names of
// GET /api/tax/{year}/field-help (phase 10 §7.8 (b), D294; rules/tax/field_help/<year>.yaml).
//
// Pure data with no import, so e2e/field-help.spec.ts can check it against the server's help:
// every field listed here must have a help entry. The renderers are typed by it (the
// questionnaire's FORMS in questionnaire.tsx, the input panel's KINDS in returnInputs.tsx, the
// wage certificate's read values in angaben.tsx), so a field a form renders that is missing here
// fails `tsc`, and a field listed here without help fails the e2e check. Key: the field `name`.

export const FORM_FIELDS = {
  wage_certificate: [
    "period_from",
    "period_to",
    "employer",
    "tax_class",
    "tax_class_factor",
    "church_tax_marked",
    "child_allowance_count",
    "gross",
    "income_tax",
    "soli",
    "church_tax",
    "church_tax_spouse",
    "replacement_benefits",
    "short_time_allowance",
    "employer_commute_tax_free",
    "employer_commute_flat_taxed",
    "pension_employer",
    "pension_employee",
    "pension_employee_professional",
    "subsidy_health",
    "subsidy_private_health",
    "subsidy_care",
    "health_employee",
    "care_employee",
    "unemployment_employee",
  ],
  owner: ["church_tax", "tax_state"],
  commute: [
    "kind",
    "workplace_label",
    "period_from",
    "period_to",
    "days_per_week",
    "absent_days",
    "days",
    "km",
    "km_car",
    "km_collective",
    "km_other",
    "public_transport_costs",
  ],
  home_office: ["days_other_workplace", "days_no_other_workplace"],
  work_room: ["months", "room_m2", "home_m2", "rent_year", "running_costs_year", "room_only_costs_year", "prefer"],
  work_equipment: ["label", "item_class", "gross", "net", "bought_on", "professional_share", "useful_life_months", "disposed_on"],
  telecom: ["bills_year", "months", "employer_reimbursed"],
  professional_association: ["name", "amount", "is_union"],
  training: ["label", "amount"],
  other_expense: ["expense_class", "label", "amount"],
  other_insurance: ["amount"],
  replacement_income: ["benefit", "amount", "period_from", "period_to"],
  vorsorge_override: ["pension", "health_care_basis", "other", "krankengeld_entitlement"],
  child: ["born_on", "kindergeld_from"],
  childcare_cost: ["child_id", "amount", "paid_to_account", "payee_label"],
  household_services: ["service_class", "labour_cost", "paid_to_account", "source", "payee_label"],
  special_expense: ["expense_class", "amount", "recipient_label"],
} as const;

export type HelpForm = keyof typeof FORM_FIELDS;
export type FormField<F extends HelpForm> = (typeof FORM_FIELDS)[F][number];

export function isHelpForm(form: string): form is HelpForm {
  return Object.prototype.hasOwnProperty.call(FORM_FIELDS, form);
}
