// The message catalogue's types (phase 10 D284). German is the source: a catalogue's keys and
// message shapes are taken from its `de` dictionary, and its `en` dictionary must have exactly
// the same keys with the same shapes, so a missing or extra English key is a TypeScript error.
//
// A message is a string, or a function for a text with values in it („3 offen“ / „3 open“):
// the English function must take the same arguments as the German one.

export const LOCALES = ["de", "en"] as const;
export type Locale = (typeof LOCALES)[number];

/** BCP 47 tags for Intl and the <html lang> attribute. English dates read day-month-year. */
export const LOCALE_TAG: Record<Locale, string> = { de: "de-DE", en: "en-GB" };

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- any argument list; the English shape is checked against the German one below
export type Message = string | ((...args: any[]) => string);
export type Messages = Record<string, Message>;

/** The English dictionary for a German one: the same keys, a string for a string, the same
 * function type for a function. */
export type Translation<D extends Messages> = { [K in keyof D]: D[K] extends string ? string : D[K] };

export interface Catalogue<D extends Messages> {
  de: Translation<D>;
  en: Translation<D>;
}

/** One screen group's messages. The keys come from `de`; `en` is checked against them. */
export function defineMessages<D extends Messages>(catalogue: { de: D; en: NoInfer<Translation<D>> }): Catalogue<D> {
  // `de` is D itself; Translation<D> only widens its literal strings, so this is the same shape.
  return catalogue as Catalogue<D>;
}
