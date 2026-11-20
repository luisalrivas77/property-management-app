// Einstellungen (mockup 24). Step 2 added „Darstellung & Sprache“ (D283, D284); the screen's other
// texts are added by the coder who rebuilds it.
import { defineMessages } from "./core";

export const settings = defineMessages({
  de: {
    appearanceGroup: "Darstellung & Sprache",
    appearance: "Darstellung",
    themeSystem: "Wie das Gerät",
    themeSystemHint: "hell oder dunkel, wie das Telefon oder der Computer eingestellt ist",
    themeLight: "Hell",
    themeDark: "Dunkel",
    language: "Sprache",
    languageGerman: "Deutsch",
    languageEnglish: "English",
    deviceOnly: "Gilt für dieses Gerät.",
    languageNote:
      "Gilt für dieses Gerät. Formulare fürs Finanzamt, Briefe an Mieter und das Jahresarchiv bleiben auf Deutsch.",
  },
  en: {
    appearanceGroup: "Appearance & language",
    appearance: "Appearance",
    themeSystem: "Like the device",
    themeSystemHint: "light or dark, as the phone or computer is set",
    themeLight: "Light",
    themeDark: "Dark",
    language: "Language",
    languageGerman: "Deutsch",
    languageEnglish: "English",
    deviceOnly: "Applies to this device.",
    languageNote:
      "Applies to this device. Forms for the Finanzamt (tax office), letters to tenants and the yearly archive stay in German.",
  },
});
