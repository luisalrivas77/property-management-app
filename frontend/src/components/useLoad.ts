"use client";

// Client-side data fetching for one screen (§9.3): run `load`, keep its result, re-run on
// `reload()` (after a mutation) or when `load` changes (wrap it in useCallback over the ids
// it reads). While a reload runs, the previous data stays on screen.
import { useCallback, useEffect, useState } from "react";

interface Result<T> {
  load: () => Promise<T>;
  version: number;
  data?: T;
  error?: unknown;
}

export interface Loaded<T> {
  /** The latest data of this `load`, possibly from before the current reload. */
  data: T | undefined;
  /** The error of the latest attempt, or null. */
  error: unknown;
  /** True until the latest attempt has finished. */
  loading: boolean;
  reload: () => void;
}

export function useLoad<T>(load: () => Promise<T>): Loaded<T> {
  const [version, setVersion] = useState(0);
  const [result, setResult] = useState<Result<T> | null>(null);

  useEffect(() => {
    let cancelled = false;
    load().then(
      (data) => {
        if (!cancelled) setResult({ load, version, data });
      },
      (error: unknown) => {
        if (!cancelled) setResult((previous) => ({ load, version, data: previous?.load === load ? previous.data : undefined, error }));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [load, version]);

  const reload = useCallback(() => setVersion((n) => n + 1), []);
  const same = result?.load === load;
  return {
    data: same ? result?.data : undefined,
    error: same && result?.version === version ? (result?.error ?? null) : null,
    loading: !(same && result?.version === version),
    reload,
  };
}
