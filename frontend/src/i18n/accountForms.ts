// The account's forms and its page (phase 10 D284, step 4c): components/ownershipShares.tsx,
// app/(app)/accounts/[id]/AccountOwnership.tsx, app/(app)/accounts/AccountForm.tsx and
// app/(app)/accounts/[id]/page.tsx. German first, English beside it (glossary.md).
import { defineMessages } from "./core";

export const accountForms = defineMessages({
  de: {
    // --- ownershipShares.tsx ---
    sharesLegend: "Anteile (als Bruch oder Prozent, z. B. 1/2 oder 50 %; leer = kein Anteil)",
    shareDescription:
      "Der Anteil dieser Person am Konto, z. B. 1/2 bei einem gemeinsamen Konto, 1/1 bei einem eigenen. Alle Anteile zusammen müssen genau 1 (100 %) ergeben.",
    shareRequired: "Mindestens einen Anteil eingeben.",
    sharesNotOne: "Die Anteile müssen zusammen genau 1 (100 %) ergeben, z. B. 1/2 und 1/2.",

    // --- AccountOwnership.tsx ---
    owners: "Eigentümer",
    noOwnersBefore: "Noch keine Eigentümer erfasst. Zuerst unter ",
    noOwnersLink: "Eigentümer",
    noOwnersAfter: " anlegen.",
    setOwnersLead: "Eigentümer festlegen:",
    setOwnersText:
      "Wem gehört dieses Konto, und zu welchem Anteil? Ein gemeinsames Konto gehört meist beiden je zur Hälfte (1/2), ein eigenes Konto einer Person ganz (1/1).",
    changeShares: "Anteile ändern",
    reasonOptional: "Begründung (optional)",
    reasonHint: "z. B. „gemeinsames Konto“ oder „vom gemeinsamen Konto bezahlt“.",
    reasonDescription:
      "Warum das Konto diesen Personen zu diesen Anteilen gehört. Hausbuch speichert es mit den Anteilen, damit es später nachvollziehbar bleibt.",
    validFromOptional: "Gültig ab (optional)",
    validFromHint: "Leer lassen: ab heute.",
    validFromDescription: "Ab wann diese Anteile gelten. Die bisherigen Anteile bleiben im Protokoll erhalten.",
    saveShares: "Anteile speichern",
  },
  en: {
    sharesLegend: "Shares (as a fraction or percentage, e.g. 1/2 or 50%; empty = no share)",
    shareDescription:
      "This person's share of the account, e.g. 1/2 for a joint account, 1/1 for their own. All shares together must add up to exactly 1 (100%).",
    shareRequired: "Enter at least one share.",
    sharesNotOne: "The shares must add up to exactly 1 (100%), e.g. 1/2 and 1/2.",

    owners: "Owners",
    noOwnersBefore: "No owners recorded yet. Add them first under ",
    noOwnersLink: "Owners",
    noOwnersAfter: ".",
    setOwnersLead: "Set owners:",
    setOwnersText:
      "Who owns this account, and in what share? A joint account usually belongs to both, half each (1/2); an account of one person belongs to them in full (1/1).",
    changeShares: "Change shares",
    reasonOptional: "Reason (optional)",
    reasonHint: "e.g. “joint account” or “paid from the joint account”.",
    reasonDescription:
      "Why the account belongs to these people in these shares. Hausbuch stores it with the shares so it can be traced later.",
    validFromOptional: "Valid from (optional)",
    validFromHint: "Leave empty: from today.",
    validFromDescription: "From when these shares apply. The previous shares stay in the history.",
    saveShares: "Save shares",
  },
});
