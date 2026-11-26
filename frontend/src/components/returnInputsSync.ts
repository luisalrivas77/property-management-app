"use client";

// Steuern › Eure Angaben shows the same return inputs in two places (phase 10 §7.8 (a), D293):
// the guide's proposals at the top („Übernehmen“ writes a row) and the typing blocks below. Each
// reads its own data, so a row saved in one place must reload the other, or a proposal still
// offers an amount a row typed below already covers and one more tap would count it twice.
// The screen provides one version; every part that writes bumps it and every part that reads
// reloads when it moves. Outside the provider (Eigentümer, the Jahresfragen) a write reloads its
// own part only, as before.
import { createContext, useCallback, useContext, useEffect, useRef } from "react";

export interface ReturnInputsSyncValue {
  version: number;
  bump: () => void;
  active: boolean;
}

export const ReturnInputsSync = createContext<ReturnInputsSyncValue>({ version: 0, bump: () => {}, active: false });

/** Reloads `reload` whenever another part of the screen saved a return input, and returns the
 * function a write calls: inside the provider it moves the shared version (which reloads this
 * part too), outside it reloads this part. `reload` must be stable (useLoad's is). */
export function useReturnInputsSync(reload: () => void): () => void {
  const { version, bump, active } = useContext(ReturnInputsSync);
  const seen = useRef(version);
  useEffect(() => {
    if (seen.current === version) return;
    seen.current = version;
    reload();
  }, [version, reload]);
  return useCallback(() => (active ? bump() : reload()), [active, bump, reload]);
}
