// The owners page, app/(app)/owners/page.tsx: reached from Immobilien, Übersicht, Konten and
// Steuern › Eure Angaben since its menu entry left (phase 10 D266).
import { defineMessages } from "./core";

export const owners = defineMessages({
  de: {
    title: "Eigentümer",
    intro: "Die Personen, denen die Immobilien gehören. Die Anteile je Immobilie stehen dort unter „Eigentümer“.",
    noneYetBefore: "Noch keine Eigentümer. Unten unter ",
    createOwner: "Eigentümer anlegen",
    noneYetAfter: " die Personen eintragen.",
    colName: "Name",
    colTaxIdRef: "Steuer-ID-Referenz",
    colChurchTax: "Kirchensteuer",
    churchTaxYes: (state: string) => `ja (${state})`,
    churchTaxNo: "nein",
    edit: "Bearbeiten",
    income: "Einkommen",
    incomeMoved:
      "Das erwartete Bruttogehalt, Lohnsteuerbescheinigungen und Gehaltsabrechnungen, ein Bescheid, Kinder und Haushalt stehen unter Steuern › Eure Angaben, zusammen mit allem anderen, was die Anlagen lesen.",
    openYourDetails: "Eure Angaben öffnen ›",
    churchTaxNeedsState: "Für die Kirchensteuer bitte das Bundesland wählen.",
    saved: "Eigentümer gespeichert.",
    created: "Eigentümer angelegt.",
    nameDescription: "Der Name der Eigentümerin oder des Eigentümers, wie im Kaufvertrag oder Grundbuch.",
    taxIdRefHint: "Ein Verweis, z. B. „Steuer-ID Luis (Ordner Steuern)“, nicht die Nummer selbst.",
    taxIdRefDescription:
      "Ein Verweis darauf, wo Sie die steuerliche Identifikationsnummer finden; die Nummer selbst gehört nicht hierher.",
    state: "Bundesland",
    stateDescription:
      "Das Bundesland des Wohnsitzes. Es bestimmt den Kirchensteuersatz: 8 % in Bayern und Baden-Württemberg, sonst 9 %.",
    paysChurchTax: "Zahlt Kirchensteuer",
    save: "Speichern",
    create: "Anlegen",
    cancel: "Abbrechen",
  },
  en: {
    title: "Owners",
    intro: "The people who own the properties. The shares per property are listed there under “Owners”.",
    noneYetBefore: "No owners yet. Enter the people below under ",
    createOwner: "Add owner",
    noneYetAfter: ".",
    colName: "Name",
    colTaxIdRef: "Tax ID reference",
    colChurchTax: "Church tax",
    churchTaxYes: (state: string) => `yes (${state})`,
    churchTaxNo: "no",
    edit: "Edit",
    income: "Income",
    incomeMoved:
      "The expected gross pay, Lohnsteuerbescheinigungen (annual wage tax certificates) and Gehaltsabrechnungen (payslips), an assessment, children and household are under Taxes › Your details, with everything else the forms read.",
    openYourDetails: "Open your details ›",
    churchTaxNeedsState: "For church tax, please choose the federal state.",
    saved: "Owner saved.",
    created: "Owner added.",
    nameDescription: "The owner's name as in the purchase contract or the Grundbuch (land register).",
    taxIdRefHint: "A pointer, e.g. “Tax ID Alex (tax folder)”, not the number itself.",
    taxIdRefDescription: "A pointer to where you keep the tax identification number; the number itself does not belong here.",
    state: "Federal state",
    stateDescription:
      "The federal state of residence. It sets the church tax rate: 8% in Bavaria and Baden-Württemberg, 9% elsewhere.",
    paysChurchTax: "Pays church tax",
    save: "Save",
    create: "Add",
    cancel: "Cancel",
  },
});
