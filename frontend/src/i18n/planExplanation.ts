// The explanation panel of Planung › Haushalt (components/planExplanation.tsx). Only the app's
// own words around the text: the paragraphs come from the server as stored (the model's text,
// or the app's template text in the language asked for) and are shown as sent.
import { defineMessages } from "./core";

export const planExplanation = defineMessages({
  de: {
    title: "Erklärung zu euren Zahlen",
    aiBadge: "KI",
    templateBadge: "Standardtext",
    staleBadge: "Zahlen haben sich seitdem geändert",
    explainedOn: (day: string) => `Erklärung vom ${day} ·`,
    writtenByApp: "Diesen Text hat die App selbst aus euren Zahlen geschrieben.",
    writtenByAi: "Diesen Text hat die KI formuliert; jede Zahl und jeden Namen hat die App eingesetzt und den Text geprüft.",
    reasonConsentOff: "Beim letzten Mal war die KI ausgeschaltet. „Neu erklären“ fragt sie jetzt.",
    reasonRejected: "Der Text der KI hat die Prüfung der App nicht bestanden, deshalb steht hier der Text der App.",
    reasonRefused: "Die KI hat keinen Text geliefert, deshalb steht hier der Text der App.",
    reasonLimit:
      "Das KI-Limit dieses Monats ist aufgebraucht, deshalb steht hier der Text der App. „Neu erklären“ hilft erst wieder, wenn das Limit im neuen Monat frei ist oder erhöht wurde.",
    reasonError:
      "Beim Fragen der KI ist ein Fehler aufgetreten, deshalb steht hier der Text der App. „Neu erklären“ versucht es noch einmal.",
    reasonNotYet: "Die KI hat zu diesen Zahlen noch nichts geschrieben; „Neu erklären“ fragt sie.",
    staleNote: "Die Zahlen dieser Seite sind seit dieser Erklärung anders; „Neu erklären“ schreibt sie mit den Zahlen von heute.",
    offNote: "Die KI ist ausgeschaltet, deshalb fragt „Neu erklären“ sie nicht. Einschalten unter",
    offLink: "Einstellungen › „Private Konten mit KI zuordnen“",
    explainAgain: "Neu erklären",
    waiting: "Die KI schreibt die Erklärung; sie erscheint hier, sobald die App sie geprüft hat.",
    timeout: "Die Erklärung wird noch geschrieben. Schaut in ein paar Minuten noch einmal hier vorbei.",
    footerOwnUse:
      "Alle Beträge rechnet die App aus euren Umsätzen; die KI formuliert nur. Sie nennt Beträge, Anlageklassen und eure bestehenden " +
      "Konten, nie ein neues Produkt (Modus: eigene Nutzung). Keine Anlageberatung.",
    footerGeneric:
      "Alle Beträge rechnet die App aus euren Umsätzen; die KI formuliert nur. Sie nennt Beträge und Anlageklassen, nennt keine " +
      "Produkte und keine Konten. Keine Anlageberatung.",
  },
  en: {
    title: "Explanation of your figures",
    aiBadge: "AI",
    templateBadge: "Standard text",
    staleBadge: "Figures have changed since",
    explainedOn: (day: string) => `Explanation of ${day} ·`,
    writtenByApp: "The app wrote this text itself from your figures.",
    writtenByAi: "The AI worded this text; the app inserted every figure and every name and checked the text.",
    reasonConsentOff: "Last time the AI was switched off. “Explain again” asks it now.",
    reasonRejected: "The AI's text did not pass the app's check, so the app's own text is shown here.",
    reasonRefused: "The AI returned no text, so the app's own text is shown here.",
    reasonLimit:
      "This month's AI limit is used up, so the app's own text is shown here. “Explain again” only helps again once the limit is free in the new month or has been raised.",
    reasonError: "Something went wrong while asking the AI, so the app's own text is shown here. “Explain again” tries once more.",
    reasonNotYet: "The AI has not written anything about these figures yet; “Explain again” asks it.",
    staleNote: "The figures on this page have changed since this explanation; “Explain again” rewrites it with today's figures.",
    offNote: "The AI is switched off, so “Explain again” does not ask it. Switch it on under",
    offLink: "Settings › “Categorise private accounts with AI”",
    explainAgain: "Explain again",
    waiting: "The AI is writing the explanation; it appears here as soon as the app has checked it.",
    timeout: "The explanation is still being written. Check back here in a few minutes.",
    footerOwnUse:
      "The app calculates every amount from your transactions; the AI only does the wording. It names amounts, asset classes and your " +
      "existing accounts, never a new product (mode: own use). Not investment advice.",
    footerGeneric:
      "The app calculates every amount from your transactions; the AI only does the wording. It names amounts and asset classes, no " +
      "products and no accounts. Not investment advice.",
  },
});
