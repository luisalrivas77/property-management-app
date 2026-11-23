// Finanzielle Gesundheit (phase 10 §7.6, Luis 23 Nov 2026): the one word on Übersicht and the
// three questions behind it, on Übersicht's panel and on Haushalt's „Kennzahlen“
// (components/verdict.tsx). German first, English beside it (glossary.md). The questions, the
// headlines, the why and the reference are the server's (rules/verdict/verdict.yaml, English
// swapped in on read) and are shown as sent.
import { defineMessages } from "./core";

export const verdictText = defineMessages({
  de: {
    title: "Finanzielle Gesundheit",
    veryGood: "Sehr gut",
    good: "Gut",
    strained: "Angespannt",
    tooLittleData: "Noch zu wenig Daten",
    // what a dot means, read out beside it
    stateGood: "im guten Bereich",
    stateOk: "zwischen gut und angespannt",
    stateStrained: "angespannt",
    stateMissing: "noch keine Zahl",
    // the limits used
    goodFrom: (limit: string) => `gut ab ${limit}`,
    goodUpTo: (limit: string) => `gut bis ${limit}`,
    strainedBelow: (limit: string) => `angespannt unter ${limit}`,
    strainedAbove: (limit: string) => `angespannt über ${limit}`,
    reserveGood: (target: string | null) => (target ? `gut ab eurem Ziel von ${target}` : "gut ab eurem Reserveziel"),
    reserveStrained: (amount: string | null, share: string) =>
      amount ? `angespannt unter ${amount} (${share} des Ziels)` : `angespannt unter ${share} des Ziels`,
    standardLimit: "Standard-Grenze",
    yourLimit: "eure Grenze",
    // the figures
    savingsRate: "Sparquote",
    reserve: "Liquiditätsreserve",
    debtService: "Schuldendienstquote",
    reserveMonths: (months: string) => `reicht für ${months} Monate`,
    reserveTarget: (months: string) => `Ziel: ${months} Monate`,
    reference: "Woher die Grenzen kommen",
    changeLimits: "Grenzen ändern",
    toHousehold: "Zu Haushalt › Kennzahlen",
    waiting: "Was dafür noch fehlt",
  },
  en: {
    title: "Financial health",
    veryGood: "Very good",
    good: "Good",
    strained: "Strained",
    tooLittleData: "Not enough data yet",
    stateGood: "in the good range",
    stateOk: "between good and strained",
    stateStrained: "strained",
    stateMissing: "no figure yet",
    goodFrom: (limit: string) => `good from ${limit}`,
    goodUpTo: (limit: string) => `good up to ${limit}`,
    strainedBelow: (limit: string) => `strained below ${limit}`,
    strainedAbove: (limit: string) => `strained above ${limit}`,
    reserveGood: (target: string | null) => (target ? `good from your target of ${target}` : "good from your reserve target"),
    reserveStrained: (amount: string | null, share: string) =>
      amount ? `strained below ${amount} (${share} of the target)` : `strained below ${share} of the target`,
    standardLimit: "standard limit",
    yourLimit: "your limit",
    savingsRate: "Savings rate",
    reserve: "Cash reserve",
    debtService: "Debt service ratio",
    reserveMonths: (months: string) => `lasts ${months} months`,
    reserveTarget: (months: string) => `target: ${months} months`,
    reference: "Where the limits come from",
    changeLimits: "Change limits",
    toHousehold: "To Household › Key figures",
    waiting: "What is still missing",
  },
});
