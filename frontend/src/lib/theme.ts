// Light or dark, chosen per device (phase 10 D283): „Wie das Gerät“ (the default, no attribute,
// globals.css follows prefers-color-scheme), „Hell“ (data-theme="light") or „Dunkel“
// (data-theme="dark") on <html>. The root layout's DEVICE_SETTINGS_SCRIPT applies the stored choice before
// the first paint, so a page never flashes in the other palette.
import { deviceSetting, useDeviceSetting } from "./deviceSetting";

export const THEMES = ["system", "light", "dark"] as const;
export type Theme = (typeof THEMES)[number];

export const themeSetting = deviceSetting<Theme>("hausbuch.theme", THEMES, "system");

function apply(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") delete root.dataset.theme;
  else root.dataset.theme = theme;
}

export function setTheme(theme: Theme): void {
  themeSetting.set(theme);
  apply(theme);
}

export function useTheme(): Theme {
  return useDeviceSetting(themeSetting);
}

if (typeof window !== "undefined") {
  // A choice made in another tab of this device applies here too.
  themeSetting.subscribe(() => apply(themeSetting.get()));
}
