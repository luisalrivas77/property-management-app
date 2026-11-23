"use client";

// „Jetzt aktualisieren“ (docs/phases/phase-10.md §7.7, D291), on Anlagen › Krypto and in the
// Übersicht's net-worth panel: POST /crypto/refresh queues one run per household (a current
// price per held coin first, then the wallets read again), „läuft …“ while GET /crypto/refresh
// says it runs, „Kurse aktualisiert – Wallets werden geprüft …“ once it says the prices are
// stored (`prices_done`, when the caller reloads its figures a first time), then the caller
// reloads them again at the end. A press while a run waits or within two minutes of
// its end queues nothing (`queued` false) and shows that run. A succeeded run that left coins
// without a current price, or wallet addresses unread, says so (principle 7), for a run this
// view watched or one that finished today (Berlin); never for an older one. Every figure stays
// the server's; the browser only polls the state and formats the time.
import { useEffect, useRef, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { useFormat, useT } from "@/i18n";
import { assets } from "@/i18n/assets";
import { ErrorNotice } from "@/lib/ErrorNotice";

type RefreshState = components["schemas"]["CryptoRefreshState"];

const POLL_MS = 2000;

function berlinDay(moment: string | Date): string {
  return new Date(moment).toLocaleDateString("sv-SE", { timeZone: "Europe/Berlin" });
}

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
  // The run whose stored prices the caller has already reloaded (once per run).
  const pricesShownFor = useRef<number | null>(null);
  const running = state?.status === "running";
  const pricesDone = running && state?.prices_done === true;
  const jobId = state?.job_id ?? null;

  useEffect(() => {
    if (!pricesDone || jobId == null || pricesShownFor.current === jobId) return;
    pricesShownFor.current = jobId;
    onRefreshed();
  }, [pricesDone, jobId, onRefreshed]);

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
    ? pricesDone
      ? t.refreshCheckingWallets
      : t.refreshRunning
    : watched && state?.status === "failed"
      ? t.refreshFailed
      : refused
        ? t.refreshJustRan
        : state?.spot_read_at
          ? t.refreshedAt(f.time(state.spot_read_at))
          : null;
  const current =
    state?.status === "succeeded" &&
    (watched || (state.finished_at != null && berlinDay(state.finished_at) === berlinDay(new Date())));
  const gaps = current && state
    ? [
        state.wallets_aborted
          ? t.refreshWalletsAborted
          : (state.wallets_failed ?? 0) > 0
            ? t.refreshWalletsFailed(state.wallets_failed ?? 0)
            : null,
        (state.unpriced ?? 0) > 0 ? t.refreshUnpriced(state.unpriced ?? 0) : null,
      ].filter((line): line is string => line != null)
    : [];

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
      {gaps.map((line) => (
        <span key={line} className="muted hint">
          {line}
        </span>
      ))}
      <ErrorNotice error={error} />
    </div>
  );
}
