// The app's message catalogue (phase 10 D284). German is the source and the default; English is
// written beside it by the coder who builds each screen, from glossary.md.
//
// Usage in a screen (one catalogue file per screen group, e.g. src/i18n/overview.ts):
//
//   // src/i18n/overview.ts
//   export const overview = defineMessages({
//     de: { title: "Übersicht", openItems: (n: number) => `${n} offen` },
//     en: { title: "Overview", openItems: (n: number) => `${n} open` }, // a missing key fails tsc
//   });
//
//   // in a client component
//   import { overview } from "@/i18n/overview";
//   import { useFormat, useT } from "@/i18n";
//   const t = useT(overview);
//   const f = useFormat();
//   <h1>{t.title}</h1> <p>{t.openItems(3)}</p> <b>{f.money(view.net_worth)}</b>
//
// Outside a component (a helper that builds a text): translate(overview).title.
import type { Catalogue, Messages, Translation } from "./core";
import { getLocale, useLocale } from "./locale";

export { defineMessages, LOCALE_TAG, LOCALES, type Catalogue, type Locale, type Messages, type Translation } from "./core";
export { formatter, useFormat, type Formatter } from "./format";
export { getLocale, localized, setLocale, useLocale } from "./locale";

/** The catalogue's dictionary in the chosen language. */
export function useT<D extends Messages>(catalogue: Catalogue<D>): Translation<D> {
  return catalogue[useLocale()];
}

/** The same, outside a component. */
export function translate<D extends Messages>(catalogue: Catalogue<D>): Translation<D> {
  return catalogue[getLocale()];
}
