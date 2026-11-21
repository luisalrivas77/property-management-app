// Sign-in and enrollment: app/(public)/login/LoginForm.tsx, app/(public)/enroll/Enroll.tsx and
// the passkey ceremony errors of auth/passkeys.ts. A default passkey label is sent to the server
// as typed, so it is not here.
import { defineMessages } from "./core";

export const auth = defineMessages({
  de: {
    loginNotRecognised: "Dieser Passkey wurde nicht erkannt. Gehört er zu diesem Hausbuch?",
    loginTitle: "Anmelden",
    loginHint: "Mit dem Passkey auf diesem Gerät oder einem verbundenen Telefon.",
    waitingForPasskey: "Warte auf Passkey …",
    signInWithPasskey: "Mit Passkey anmelden",
    browserUnsupported: "Dieser Browser unterstützt keine Passkeys. Bitte einen aktuellen Browser verwenden.",
    noPasskeyYetBefore: "Noch kein Passkey? Die Einladung kommt als Link, der mit ",
    noPasskeyYetAfter: " beginnt.",
    enrollTitle: "Passkey einrichten",
    loading: "Lädt …",
    linkMissingBefore:
      "Diese Seite braucht einen Einladungslink. Bitte den vollständigen Link aus der Einladung öffnen (er enthält ",
    linkMissingAfter: ").",
    linkMalformed: "Der Einladungslink ist unvollständig. Bitte den ganzen Link kopieren und erneut öffnen.",
    enrollIntro:
      "Der Passkey ersetzt das Passwort. Er bleibt auf diesem Gerät (oder in iCloud / Google) und wird mit PIN, Fingerabdruck oder Gesicht freigegeben.",
    passkeyName: "Name des Passkeys",
    passkeyNameHint: "Zum Wiedererkennen in den Einstellungen, z. B. „iPhone von Luis“.",
    enrollSubmit: "Passkey einrichten",
    ceremonyAborted: "Der Vorgang wurde abgebrochen.",
    ceremonyAlreadyRegistered: "Auf diesem Gerät gibt es schon einen Passkey für Hausbuch.",
    ceremonyInvalidDomain: "Passkeys funktionieren nur über HTTPS oder auf localhost unter der eingestellten Adresse.",
    ceremonyUnsupported: "Dieses Gerät unterstützt die nötige Passkey-Art nicht (mit PIN oder Biometrie).",
    ceremonyNotAllowed: "Der Vorgang wurde abgebrochen oder ist abgelaufen.",
    ceremonyFailed: "Der Passkey-Vorgang im Browser ist fehlgeschlagen.",
  },
  en: {
    loginNotRecognised: "This passkey was not recognised. Does it belong to this Hausbuch?",
    loginTitle: "Sign in",
    loginHint: "With the passkey on this device or a connected phone.",
    waitingForPasskey: "Waiting for passkey …",
    signInWithPasskey: "Sign in with passkey",
    browserUnsupported: "This browser does not support passkeys. Please use an up-to-date browser.",
    noPasskeyYetBefore: "No passkey yet? The invitation comes as a link starting with ",
    noPasskeyYetAfter: ".",
    enrollTitle: "Set up passkey",
    loading: "Loading …",
    linkMissingBefore:
      "This page needs an invitation link. Please open the complete link from the invitation (it contains ",
    linkMissingAfter: ").",
    linkMalformed: "The invitation link is incomplete. Please copy the whole link and open it again.",
    enrollIntro:
      "The passkey replaces the password. It stays on this device (or in iCloud / Google) and is unlocked with a PIN, fingerprint or face.",
    passkeyName: "Passkey name",
    passkeyNameHint: "To recognise it in the settings, e.g. “Alex's iPhone”.",
    enrollSubmit: "Set up passkey",
    ceremonyAborted: "The process was cancelled.",
    ceremonyAlreadyRegistered: "This device already has a passkey for Hausbuch.",
    ceremonyInvalidDomain: "Passkeys only work over HTTPS or on localhost at the configured address.",
    ceremonyUnsupported: "This device does not support the required kind of passkey (with PIN or biometrics).",
    ceremonyNotAllowed: "The process was cancelled or has timed out.",
    ceremonyFailed: "The passkey process in the browser failed.",
  },
});
