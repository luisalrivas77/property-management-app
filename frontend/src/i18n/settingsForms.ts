// Einstellungen' forms in the detail panel (phase 10 step 4c): the passkeys in settings/page.tsx,
// Members, RenamePasskey, AiSettings, AdviceModeSettings, ReminderSettings, InboxAddress and
// AppSecrets. The rows and the group names are in settings.ts.
import { defineMessages } from "./core";

export const settingsForms = defineMessages({
  de: {
    // shared by the forms
    on: "Eingeschaltet",
    off: "Ausgeschaltet",
    copied: "Kopiert.",
    saving: "Speichert …",

    // page.tsx: passkeys
    passkeys: "Passkeys",
    name: "Name",
    lastUsed: "Zuletzt benutzt",
    thisSession: " (diese Sitzung)",
    created: "Angelegt ",
    synced: "synchronisiert",
    notSynced: "nicht synchronisiert",
    rename: "Umbenennen",
    removing: "Entfernt …",
    remove: "Entfernen",
    lastPasskey: "Der letzte Passkey kann nicht entfernt werden.",
    lastPasskeyHint:
      "Der letzte Passkey kann nicht entfernt werden. Am besten einen zweiten auf einem anderen Gerät anlegen.",
    removeCurrent: (label: string) => `Passkey „${label}“ entfernen? Damit endet auch diese Sitzung.`,
    removeOther: (label: string) => `Passkey „${label}“ entfernen? Sitzungen, die damit angemeldet wurden, enden.`,
    members: "Mitglieder",
    addPasskey: "Passkey hinzufügen",
    addPasskeyHint: "Zum Beispiel für den Laptop, nachdem der erste Passkey auf dem Telefon liegt.",
    waitingForPasskey: "Warte auf Passkey …",

    // RenamePasskey.tsx
    newName: "Neuer Name",

    // Members.tsx
    membersIntro: "Beide im Haushalt sehen und bearbeiten dasselbe; das Protokoll hält fest, wer was eingetragen hat.",
    member: "Mitglied",
    email: "E-Mail",
    passkey: "Passkey",
    you: " (du)",
    passkeySet: "eingerichtet",
    passkeyNone: "noch keiner (eingeladen)",
    openInvites: "Offene Einladungen",
    noOpenInvites: "Keine offenen Einladungen.",
    invited: "Eingeladen",
    linkValidUntil: "Link gültig bis",
    revoke: "Zurückziehen",
    revokeQuestion: (name: string) => `Einladung für ${name} zurückziehen? Der Link funktioniert dann nicht mehr.`,
    inviteMember: "Mitglied einladen",
    emailLogin: "E-Mail (Anmeldename)",
    inviteHint: "Es wird keine E-Mail verschickt: der Einladungslink erscheint hier, einmalig, zum Weitergeben.",
    inviting: "Lädt ein …",
    invite: "Einladen",
    linkFor: (name: string, until: string) => `Einladungslink für ${name}, gültig bis ${until}. Er wird `,
    onlyNow: "nur jetzt",
    linkOnce: "angezeigt und funktioniert einmal.",
    copyLink: "Link kopieren",
    hide: "Ausblenden",
    copyLinkFailed: "Kopieren nicht möglich; bitte den Link markieren.",

    // AiSettings.tsx
    tenantDocuments: "Mieterdokumente von der KI lesen lassen",
    tenantDocumentsText:
      "Mietverträge, Übergabeprotokolle, Kautionsnachweise und Briefe von Mietern enthalten personenbezogene Daten Ihrer Mieter. " +
      "Ist dies eingeschaltet, werden diese Dokumente zum Auslesen an Anthropic (Claude API) gesendet und in den USA verarbeitet, " +
      "auf Grundlage des Auftragsverarbeitungsvertrags mit EU-Standardvertragsklauseln. Anthropic verwendet sie nicht zum Training " +
      "und löscht sie innerhalb von 30 Tagen. Schalten Sie es nur ein, wenn Ihre Datenschutzhinweise an die Mieter Anthropic als " +
      "Auftragsverarbeiter nennen. Sie können es jederzeit wieder ausschalten; bereits gelesene Dokumente bleiben gelesen.",
    autoAccept: "KI-Zuordnungen automatisch bestätigen",
    autoAcceptText:
      "Ist die KI sich sicher, wird ein Umsatz ohne Rückfrage gebucht. Miete, Nebenkosten-Vorauszahlung, Darlehensraten, Hausgeld, " +
      "Überweisungen zwischen Ihren eigenen Konten und aufgeteilte Beträge legt die KI trotzdem immer zur Prüfung vor. " +
      "Empfohlen erst ab dem zweiten importierten Monat.",
    privateAccounts: "Private Konten mit KI zuordnen",
    privateAccountsText:
      "Ist dies eingeschaltet, schlägt die KI für Umsätze Ihrer privaten Konten eine Haushaltskategorie vor, wenn keine Regel und " +
      "kein Abgleich sie erklärt. Gesendet werden nur Name der Gegenseite, Verwendungszweck, Betrag, Datum, die letzten vier Stellen " +
      "der IBAN und die Kontoart, nie der Name des Kontos oder seiner Eigentümer; verarbeitet bei Anthropic (Claude API) in den USA wie " +
      "die übrigen Dokumente. Umsätze können Gehalt, Einkäufe und Zahlungen an Kirche, Gewerkschaft, Apotheke oder " +
      "Arzt. Wer es einschaltet, stimmt damit für den Haushalt zu; ein anderes Mitglied kann seine Zustimmung hier dazusetzen. " +
      "Solange es ausgeschaltet ist, kommen solche Umsätze mit allen Haushaltskategorien zur Auswahl unter „Zu prüfen“.",
    privateActive: "Eingeschaltet: die KI ordnet private Umsätze zu.",
    privateNoConsent: "Eingeschaltet, aber noch ohne Zustimmung. Bitte einmal aus- und wieder einschalten.",
    youFormal: " (Sie)",
    consented: "hat zugestimmt",
    consentMissing: "Zustimmung fehlt",
    iConsent: "Ich stimme zu",

    // AdviceModeSettings.tsx
    adviceMode: "Hinweis-Modus",
    ownUse: "Eigene Nutzung",
    ownUseText:
      "Die KI-Erklärung unter Planung › Haushalt nennt Beträge, Anlageklassen und eure bestehenden Konten, erkannten Sparpläne " +
      "und Anlagen, nie ein neues Produkt. Die Wege zum Reserveziel zeigen auch Wege, in denen einzelne Sparpläne pausieren.",
    generic: "Allgemein",
    genericText:
      "Die KI-Erklärung nennt nur Beträge und Anlageklassen, keine Konten und keine Produkte. Die Wege zum Reserveziel zeigen nur " +
      "„Reserve zuerst“ und „Alles weiter wie bisher“, und die Sparpläne stehen nur zusammen als eine Summe da.",
    adviceSame:
      "Die Kennzahlen, ihre Ampeln und die Rechnung zu eurem Vermögen sind in beiden Modi gleich. In keinem Modus ist es eine Anlageberatung.",
    savedAs: (label: string) => `Gespeichert: ${label}`,

    // ReminderSettings.tsx
    reminders: "Erinnerungen per E-Mail",
    remindersText:
      "Steht ein Termin an, zum Beispiel eine Grundsteuer-Rate, eine fehlende Miete oder die Abgabe der Steuererklärung, " +
      "bekommt jedes Mitglied morgens eine E-Mail mit allen fälligen Terminen, höchstens eine am Tag. Sie nennt nur die Art " +
      "des Termins, das Datum und die Bezeichnung der Wohnung oder Einheit, nie einen Betrag oder den Namen eines Mieters; " +
      "die Einzelheiten stehen auf der Übersicht.",
    remindersUnavailable:
      "Der E-Mail-Versand ist noch nicht eingerichtet: dafür unter „Zugangsschlüssel“ (unten, Bereich E-Mail) den Schlüssel, " +
      "das Projekt und die Absenderadresse von Scaleway Transactional Email eintragen. Bis dahin wird keine E-Mail verschickt; " +
      "die Erinnerungen stehen weiter auf der Übersicht und unter „Zu prüfen“.",

    // InboxAddress.tsx
    inboxNotSet:
      "Der Posteingang per Mail ist noch nicht eingerichtet. Dafür unter „Zugangsschlüssel“ (unten, Bereich E-Mail) das " +
      "Gmail-Konto, sein App-Passwort, die Weiterleitungsadresse und das Label eintragen. Danach steht hier die Adresse, an die " +
      "Sie Rechnungen und Kontoauszüge weiterleiten.",
    copyAddress: "Adresse kopieren",
    copyAddressFailed: "Kopieren nicht möglich; bitte die Adresse markieren.",
    inboxNote: (label: string) =>
      `Nur Anhänge werden übernommen; der Text der Mail wird nicht gespeichert. Die App liest nur das Label ${label} und verändert ` +
      "nichts im Postfach. Mieterunterlagen besser direkt hochladen, nicht weiterleiten.",

    // AppSecrets.tsx
    groupAi: "KI",
    groupChains: "Blockchains & Kurse",
    groupBank: "Bank",
    groupMail: "E-Mail",
    groupElster: "ELSTER",
    sourceApp: "In der App gespeichert",
    sourceContainer: "Aus der Server-Konfiguration",
    sourceNone: "Nicht gesetzt",
    sourceUnreadable: "Kann nicht gelesen werden",
    secretsIntro:
      "Schlüssel und Zugangsdaten für die Dienste, die Hausbuch nutzt. Ein hier eingetragener Wert gilt vor dem Wert aus der " +
      "Server-Konfiguration und wird verschlüsselt gespeichert. Ein Schlüssel wird nach dem Speichern nie wieder angezeigt; " +
      "„Löschen“ nimmt wieder den Wert aus der Server-Konfiguration, falls dort einer steht.",
    deleteSecret: (label: string) =>
      `„${label}“ aus der App löschen? Danach gilt der Wert aus der Server-Konfiguration, falls dort einer steht.`,
    unreadable: "Schlüssel kann nicht gelesen werden, bitte neu eintragen.",
    typedOn: (when: string) => `Eingetragen am ${when}`,
    change: "Ändern",
    enter: "Eintragen",
    delete: "Löschen",
    orVariable: (variable: string) => `Alternativ als Variable ${variable} in der Server-Konfiguration.`,
  },
  en: {
    on: "On",
    off: "Off",
    copied: "Copied.",
    saving: "Saving …",

    passkeys: "Passkeys",
    name: "Name",
    lastUsed: "Last used",
    thisSession: " (this session)",
    created: "Created ",
    synced: "synced",
    notSynced: "not synced",
    rename: "Rename",
    removing: "Removing …",
    remove: "Remove",
    lastPasskey: "The last passkey cannot be removed.",
    lastPasskeyHint: "The last passkey cannot be removed. Best add a second one on another device.",
    removeCurrent: (label) => `Remove passkey “${label}”? This also ends this session.`,
    removeOther: (label) => `Remove passkey “${label}”? Sessions signed in with it end.`,
    members: "Members",
    addPasskey: "Add a passkey",
    addPasskeyHint: "For example for the laptop, once the first passkey is on the phone.",
    waitingForPasskey: "Waiting for the passkey …",

    newName: "New name",

    membersIntro: "Both members of the household see and edit the same data; the log records who entered what.",
    member: "Member",
    email: "Email",
    passkey: "Passkey",
    you: " (you)",
    passkeySet: "set up",
    passkeyNone: "none yet (invited)",
    openInvites: "Open invitations",
    noOpenInvites: "No open invitations.",
    invited: "Invited",
    linkValidUntil: "Link valid until",
    revoke: "Withdraw",
    revokeQuestion: (name) => `Withdraw the invitation for ${name}? The link then stops working.`,
    inviteMember: "Invite a member",
    emailLogin: "Email (sign-in name)",
    inviteHint: "No email is sent: the invitation link appears here, once, for you to pass on.",
    inviting: "Inviting …",
    invite: "Invite",
    linkFor: (name, until) => `Invitation link for ${name}, valid until ${until}. It is shown `,
    onlyNow: "only now",
    linkOnce: "and works once.",
    copyLink: "Copy link",
    hide: "Hide",
    copyLinkFailed: "Copying is not possible; please select the link.",

    tenantDocuments: "Let the AI read tenant documents",
    tenantDocumentsText:
      "Leases, handover reports, Kaution (rent deposit) proofs and letters from tenants hold your tenants' personal data. " +
      "When this is on, these documents are sent to Anthropic (Claude API) to be read and are processed in the USA, " +
      "under the data processing agreement with the EU standard contractual clauses. Anthropic does not use them for training " +
      "and deletes them within 30 days. Only switch it on if your privacy notice to the tenants names Anthropic as a " +
      "processor. You can switch it off again at any time; documents already read stay read.",
    autoAccept: "Confirm AI categorisations automatically",
    autoAcceptText:
      "When the AI is sure, a transaction is booked without asking. Rent, service charge prepayments, loan payments, Hausgeld " +
      "(monthly payment to the owners' association), transfers between your own accounts and split amounts are still always " +
      "put to you for review. Recommended only from the second imported month.",
    privateAccounts: "Categorise private accounts with AI",
    privateAccountsText:
      "When this is on, the AI suggests a household category for transactions on your private accounts when no rule and " +
      "no match explains them. Only the counterparty's name, the payment reference, the amount, the date, the last four digits " +
      "of the IBAN and the kind of account are sent, never the name of the account or its owners; processed at Anthropic " +
      "(Claude API) in the USA like the other documents. Transactions can reveal salary, purchases and payments to a church, " +
      "trade union, pharmacy or doctor. Whoever switches it on consents for the household; another member can add their " +
      "consent here. While it is off, such transactions come up under “To review” with every household category to choose from.",
    privateActive: "On: the AI categorises private transactions.",
    privateNoConsent: "On, but without consent yet. Please switch it off and on again once.",
    youFormal: " (you)",
    consented: "has consented",
    consentMissing: "consent missing",
    iConsent: "I consent",

    adviceMode: "Advice mode",
    ownUse: "Own use",
    ownUseText:
      "The AI explanation under Planning › Household names amounts, asset classes and your existing accounts, detected savings " +
      "plans and investments, never a new product. The paths to the reserve target also show paths in which single savings plans pause.",
    generic: "General",
    genericText:
      "The AI explanation names only amounts and asset classes, no accounts and no products. The paths to the reserve target " +
      "show only “Reserve first” and “Everything as before”, and the savings plans appear only together as one sum.",
    adviceSame:
      "The key figures, their marks and the calculation of your net worth are the same in both modes. In neither mode is it investment advice.",
    savedAs: (label) => `Saved: ${label}`,

    reminders: "Reminders by email",
    remindersText:
      "When a date is coming up, for example a Grundsteuer (property tax) instalment, a missing rent or filing the tax return, " +
      "each member gets one email in the morning with every date due, at most one a day. It names only the kind of date, " +
      "the date and the name of the flat or unit, never an amount or a tenant's name; the details are on the Overview.",
    remindersUnavailable:
      "Sending email is not set up yet: for that, enter the key, the project and the sender address of Scaleway " +
      "Transactional Email under “Access keys” (below, Email section). Until then no email is sent; the reminders stay " +
      "on the Overview and under “To review”.",

    inboxNotSet:
      "The inbox by email is not set up yet. For that, enter the Gmail account, its app password, the forwarding address and " +
      "the label under “Access keys” (below, Email section). The address you forward invoices and statements to then appears here.",
    copyAddress: "Copy address",
    copyAddressFailed: "Copying is not possible; please select the address.",
    inboxNote: (label) =>
      `Only attachments are taken; the text of the mail is not stored. The app reads only the label ${label} and changes ` +
      "nothing in the mailbox. Tenant papers are better uploaded directly than forwarded.",

    groupAi: "AI",
    groupChains: "Blockchains & prices",
    groupBank: "Bank",
    groupMail: "Email",
    groupElster: "ELSTER",
    sourceApp: "Stored in the app",
    sourceContainer: "From the server configuration",
    sourceNone: "Not set",
    sourceUnreadable: "Cannot be read",
    secretsIntro:
      "Keys and credentials for the services Hausbuch uses. A value entered here takes precedence over the value from the " +
      "server configuration and is stored encrypted. A key is never shown again after it is saved; " +
      "“Delete” goes back to the value from the server configuration, if there is one.",
    deleteSecret: (label) => `Delete “${label}” from the app? The value from the server configuration then applies, if there is one.`,
    unreadable: "The key cannot be read, please enter it again.",
    typedOn: (when) => `Entered on ${when}`,
    change: "Change",
    enter: "Enter",
    delete: "Delete",
    orVariable: (variable) => `Alternatively as the variable ${variable} in the server configuration.`,
  },
});
