// Words every screen uses (buttons, states). Add here only what is truly shared; a screen's own
// texts go in its own file, so parallel coders never edit the same one.
import { defineMessages } from "./core";

export const common = defineMessages({
  de: {
    loading: "Lädt …",
    save: "Speichern",
    cancel: "Abbrechen",
    close: "Schließen",
    back: "Zurück",
    edit: "Bearbeiten",
    remove: "Entfernen",
    confirm: "Bestätigen",
    apply: "Übernehmen",
    open: "Öffnen",
    yes: "Ja",
    no: "Nein",
    none: "keine",
  },
  en: {
    loading: "Loading …",
    save: "Save",
    cancel: "Cancel",
    close: "Close",
    back: "Back",
    edit: "Edit",
    remove: "Remove",
    confirm: "Confirm",
    apply: "Apply",
    open: "Open",
    yes: "Yes",
    no: "No",
    none: "none",
  },
});
