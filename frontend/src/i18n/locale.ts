// The app's language, chosen per device (phase 10 D284): Deutsch (the default, whatever the
// browser's language) or English, stored in localStorage. The API client sends it as
// Accept-Language with every request (src/api/client.ts), so the server can pick its English
// templates once step 4c builds them; until then the server ignores it.
import { deviceSetting, useDeviceSetting } from "@/lib/deviceSetting";

import { LOCALE_TAG, LOCALES, type Locale } from "./core";

export const localeSetting = deviceSetting<Locale>("hausbuch.locale", LOCALES, "de");

/** The language outside React (the API client, a function that builds a text). */
export function getLocale(): Locale {
  return localeSetting.get();
}

export function setLocale(locale: Locale): void {
  localeSetting.set(locale);
}

export function useLocale(): Locale {
  return useDeviceSetting(localeSetting);
}

/** A German record read in the chosen language on every access (a Proxy, so Object.keys,
 * entries and spreads follow too): the shape of the existing label maps stays, and only the
 * keys ever reach the API. */
export function localized<K extends string, V = string>(de: Record<K, V>, en: Record<K, V>): Record<K, V> {
  return new Proxy(de, { get: (target, key) => Reflect.get(getLocale() === "en" ? en : target, key) });
}

if (typeof window !== "undefined") {
  // <html lang> follows the choice, here and in another tab of this device (the root layout's
  // script sets it before the first paint).
  localeSetting.subscribe(() => {
    document.documentElement.lang = LOCALE_TAG[localeSetting.get()].slice(0, 2);
  });
}
