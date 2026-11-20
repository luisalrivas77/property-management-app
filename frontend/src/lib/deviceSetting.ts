// A setting kept on this device only (phase 10 D283, D284): read from and written to
// localStorage, which may be missing or throw (a private window, blocked site data), so every
// access is guarded and the default wins. Components read it with useSyncExternalStore, so the
// server render and the first client render agree (the default) and React re-renders once the
// stored value is known, without a hydration mismatch.
import { useSyncExternalStore } from "react";

export interface DeviceSetting<T extends string> {
  get(): T;
  set(value: T): void;
  subscribe(listener: () => void): () => void;
  readonly key: string;
  readonly fallback: T;
}

export function deviceSetting<T extends string>(key: string, allowed: readonly T[], fallback: T): DeviceSetting<T> {
  const listeners = new Set<() => void>();
  let cached: T | undefined;

  function read(): T {
    try {
      const stored = window.localStorage.getItem(key);
      return allowed.includes(stored as T) ? (stored as T) : fallback;
    } catch {
      return fallback;
    }
  }

  function onStorage(event: StorageEvent) {
    // Another tab changed it.
    if (event.key !== key) return;
    cached = read();
    listeners.forEach((listener) => listener());
  }

  return {
    key,
    fallback,
    get() {
      if (typeof window === "undefined") return fallback;
      cached ??= read();
      return cached;
    },
    set(value) {
      cached = value;
      try {
        if (value === fallback) window.localStorage.removeItem(key);
        else window.localStorage.setItem(key, value);
      } catch {
        // Not stored: the choice holds for this page view only.
      }
      listeners.forEach((listener) => listener());
    },
    subscribe(listener) {
      if (listeners.size === 0) window.addEventListener("storage", onStorage);
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0) window.removeEventListener("storage", onStorage);
      };
    },
  };
}

/** The setting's value in a component; the default during the server render and hydration. */
export function useDeviceSetting<T extends string>(setting: DeviceSetting<T>): T {
  return useSyncExternalStore(setting.subscribe, setting.get, () => setting.fallback);
}
