// The detail panel's state in the URL hash (phase 10 D267): `#d-<kind>-<id>`, so the browser's
// back button closes the panel, a link can open it, and a reload keeps it open. Only hashes that
// start with `#d-` are read or written here; the app's other hashes (an anchor on Einstellungen,
// an enrollment `#token=`) are left alone.
//
// Opening pushes a history entry (or replaces it when another panel is already open, so
// switching rows never piles up entries); closing goes back when this page view pushed the entry
// and otherwise (a link opened the panel) replaces the URL without the hash. pushState never
// fires hashchange, so every write here also notifies the subscribers itself. Next.js accepts a
// native pushState/replaceState and keeps its own router state on the entry.
import { useSyncExternalStore } from "react";

const PREFIX = "#d-";
const KIND = /^[a-z][a-z0-9]*$/;
const CHANGED = "hausbuch:detail-hash";

/** The URL this page view pushed for the open panel, so close() knows whether to go back. */
let pushedUrl: string | null = null;

function notify(): void {
  window.dispatchEvent(new Event(CHANGED));
}

function subscribe(listener: () => void): () => void {
  window.addEventListener("hashchange", listener);
  window.addEventListener("popstate", listener);
  window.addEventListener(CHANGED, listener);
  return () => {
    window.removeEventListener("hashchange", listener);
    window.removeEventListener("popstate", listener);
    window.removeEventListener(CHANGED, listener);
  };
}

function detailHash(): string {
  const hash = window.location.hash;
  return hash.startsWith(PREFIX) ? hash : "";
}

/** The hash of the open panel (`#d-…`), or "" when none is open. */
export function useDetailHash(): string {
  return useSyncExternalStore(subscribe, detailHash, () => "");
}

export function detailHref(kind: string, id: string): string {
  if (!KIND.test(kind)) throw new Error(`detail kind must be lower-case letters and digits: ${kind}`);
  return `${PREFIX}${kind}-${encodeURIComponent(id)}`;
}

/** The id the hash holds for this kind, or null. */
export function detailIdOf(hash: string, kind: string): string | null {
  const head = `${PREFIX}${kind}-`;
  if (!hash.startsWith(head)) return null;
  try {
    return decodeURIComponent(hash.slice(head.length)) || null;
  } catch {
    return null;
  }
}

export function openDetail(kind: string, id: string): void {
  const target = detailHref(kind, id);
  if (window.location.hash === target) return;
  if (detailHash() !== "") {
    window.history.replaceState(null, "", target);
    if (pushedUrl !== null) pushedUrl = window.location.href;
  } else {
    window.history.pushState(null, "", target);
    pushedUrl = window.location.href;
  }
  notify();
}

export function closeDetail(): void {
  if (detailHash() === "") return;
  if (pushedUrl === window.location.href) {
    pushedUrl = null;
    window.history.back(); // popstate notifies
    return;
  }
  pushedUrl = null;
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  notify();
}
