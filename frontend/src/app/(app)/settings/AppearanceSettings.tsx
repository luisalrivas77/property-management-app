"use client";

// Einstellungen › „Darstellung & Sprache“ (phase 10 D283, D284, mockup 24): light or dark and
// the language, both stored on this device only (localStorage), so each phone or computer keeps
// its own. The page's palette and language change at once; the root layout's script applies
// them before the first paint on the next load.
import { useId } from "react";

import { setLocale, useLocale, useT } from "@/i18n";
import { settings } from "@/i18n/settings";
import { setTheme, useTheme } from "@/lib/theme";

import "./appearance.css";

export function AppearanceSettings() {
  const t = useT(settings);
  const theme = useTheme();
  const locale = useLocale();
  const id = useId();

  return (
    <section className="card stack" aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`}>{t.appearanceGroup}</h2>
      <fieldset className="choice-group">
        <legend>{t.appearance}</legend>
        <label className="choice">
          <input type="radio" name={`${id}-theme`} checked={theme === "system"} onChange={() => setTheme("system")} />
          <span>
            {t.themeSystem}
            <span className="muted choice-hint">{t.themeSystemHint}</span>
          </span>
        </label>
        <label className="choice">
          <input type="radio" name={`${id}-theme`} checked={theme === "light"} onChange={() => setTheme("light")} />
          <span>{t.themeLight}</span>
        </label>
        <label className="choice">
          <input type="radio" name={`${id}-theme`} checked={theme === "dark"} onChange={() => setTheme("dark")} />
          <span>{t.themeDark}</span>
        </label>
        <p className="muted choice-note">{t.deviceOnly}</p>
      </fieldset>
      <fieldset className="choice-group">
        <legend>{t.language}</legend>
        <label className="choice">
          <input type="radio" name={`${id}-locale`} checked={locale === "de"} onChange={() => setLocale("de")} />
          <span lang="de">{t.languageGerman}</span>
        </label>
        <label className="choice">
          <input type="radio" name={`${id}-locale`} checked={locale === "en"} onChange={() => setLocale("en")} />
          <span lang="en">{t.languageEnglish}</span>
        </label>
        <p className="muted choice-note">{t.languageNote}</p>
      </fieldset>
    </section>
  );
}
