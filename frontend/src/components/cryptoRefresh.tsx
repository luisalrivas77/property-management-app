"use client";

// „Jetzt aktualisieren“ (docs/phases/phase-10.md §7.7, D291), on Anlagen › Krypto and in the
// Übersicht's net-worth panel: POST /crypto/refresh queues one run per household (the wallets
// read again, then a current price per held coin), „läuft …“ while GET /crypto/refresh says it
// runs, then the caller reloads its figures. A press while a run waits or within two minutes of
// its end queues nothing (`queued` false) and shows that run. Every figure stays the server's;
// the browser only polls the state and formats the time.
import { useEffect, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { useFormat, useT } from "@/i18n";
import { assets } from "@/i18n/assets";
import { ErrorNotice } from "@/lib/ErrorNotice";

type RefreshState = components["schemas"]["CryptoRefreshState"];

const POLL_MS = 2000;

export function CryptoRefresh({ onRefreshed }: { onRefreshed: () => void }) {
  const t = useT(assets);
  const f = useFormat();
  const [state, setState] = useState<RefreshState | null>(null);
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);
  // Whether this view pressed or watched the newest run: only then is its failure news.
  const [watched, setWatched] = useState(false);
  const running = state?.status === "running";

  // A run started elsewhere (the other screen, another device) shows as running here too.
  useEffect(() => {
    let cancelled = false;
    ok(api.GET("/api/crypto/refresh")).then(
      (fresh) => {
        if (cancelled) return;
        setState(fresh);
        if (fresh.status === "running") setWatched(true);
      },
      (caught: unknown) => {
        if (!cancelled) setError(caught);
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!running) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      ok(api.GET("/api/crypto/refresh")).then(
        (fresh) => {
          if (cancelled) return;
          setState(fresh);
          setError(null);
          if (fresh.status === "running") setTick((n) => n + 1);
          else onRefreshed();
        },
        (caught: unknown) => {
          // Polling stops; a press asks again and resumes it.
          if (!cancelled) setError(caught);
        },
      );
    }, POLL_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [running, tick, onRefreshed]);

  async function press() {
    setBusy(true);
    setError(null);
    setRefused(false);
    try {
      const fresh = await ok(api.POST("/api/crypto/refresh"));
      setState(fresh);
      setWatched(true);
      setTick((n) => n + 1);
      if (!fresh.queued && fresh.status !== "running") setRefused(true);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  const status = running
    ? t.refreshRunning
    : watched && state?.status === "failed"
      ? t.refreshFailed
      : refused
        ? t.refreshJustRan
        : state?.spot_read_at
          ? t.refreshedAt(f.time(state.spot_read_at))
          : null;

  return (
    <div className="stack" style={{ gap: 4 }}>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button type="button" className="secondary" onClick={() => void press()} disabled={busy || (running && error == null)}>
          {t.refreshNow}
        </button>
        {status ? (
          <span className="muted hint" role="status" aria-live="polite">
            {status}
          </span>
        ) : null}
      </div>
      <ErrorNotice error={error} />
    </div>
  );
}
