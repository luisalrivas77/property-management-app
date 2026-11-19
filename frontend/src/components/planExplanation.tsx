"use client";

// The KI panel of Planung › Haushalt (docs/phases/phase-9.md §8.6, §10, §14, D251–D257, step 5;
// docs/mockups/12-planning-household.png): the explanation of the household plan for the
// scenario the page shows. The paragraphs come rendered from GET /household-plan/explanation
// (every figure and name written by code into the validated text, or the app's own template
// text); this panel only shows them, with „Standardtext“ when the template is shown, „Zahlen
// haben sich seitdem geändert“ when today's facts differ from the stored ones, one plain
// sentence on why the template is shown, „Neu erklären“, and the footer of the mode the text
// was written in.
//
// „Neu erklären“ sends POST /household-plan/explanation, which queues one explain_plan run, and
// then reads the explanation again every 3 s until a stored row other than the one shown
// appears, for at most 60 s. While „Private Konten mit KI zuordnen“ is off no request may be
// built (D253): the button is then disabled and the panel says so, with the link to the switch
// (principle 7); a POST that answers `queued: false` says the same.
import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { longDate } from "./healthShared";
import { AI_SETTINGS_ANCHOR } from "./labels";
import { useLoad } from "./useLoad";

type PlanExplanation = components["schemas"]["PlanExplanation"];
type ExplanationReason = components["schemas"]["ExplanationReason"];
type AdviceMode = components["schemas"]["AdviceMode"];

const POLL_MS = 3_000;
const POLL_LIMIT_MS = 60_000;

const berlinDate = new Intl.DateTimeFormat("de-DE", { dateStyle: "long", timeZone: "Europe/Berlin" });

/** Why the template text is shown, in one sentence (null: nothing to say). */
function reasonText(reason: ExplanationReason | null | undefined, aiOn: boolean, stored: boolean): string | null {
  switch (reason) {
    case "consent_off":
      // Review finding 9 of step 4b: the newest row was written while the AI was off, and it is
      // on now.
      return aiOn ? "Beim letzten Mal war die KI ausgeschaltet. „Neu erklären“ fragt sie jetzt." : null;
    case "rejected":
      return "Der Text der KI hat die Prüfung der App nicht bestanden, deshalb steht hier der Text der App.";
    case "refused":
      return "Die KI hat keinen Text geliefert, deshalb steht hier der Text der App.";
    case "limit":
      return "Das KI-Limit dieses Monats ist aufgebraucht, deshalb steht hier der Text der App. „Neu erklären“ hilft erst wieder, wenn das Limit im neuen Monat frei ist oder erhöht wurde.";
    case "error":
      return "Beim Fragen der KI ist ein Fehler aufgetreten, deshalb steht hier der Text der App. „Neu erklären“ versucht es noch einmal.";
    default:
      return !stored && aiOn ? "Die KI hat zu diesen Zahlen noch nichts geschrieben; „Neu erklären“ fragt sie." : null;
  }
}

const FOOTER: Record<AdviceMode, string> = {
  own_use:
    "Alle Beträge rechnet die App aus euren Umsätzen; die KI formuliert nur. Sie nennt Beträge, Anlageklassen und eure bestehenden " +
    "Konten, nie ein neues Produkt (Modus: eigene Nutzung). Keine Anlageberatung.",
  generic:
    "Alle Beträge rechnet die App aus euren Umsätzen; die KI formuliert nur. Sie nennt Beträge und Anlageklassen, nennt keine " +
    "Produkte und keine Konten. Keine Anlageberatung.",
};

type Phase = "idle" | "waiting" | "timeout" | "off";

/** `scenarioId`: the scenario the page shows (the default in force), null for the defaults.
 * `asOf`: the plan's day, the date of a text rendered on read from today's facts. */
export function PlanExplanationPanel({ scenarioId, asOf }: { scenarioId: string | null; asOf: string }) {
  const load = useCallback(
    () =>
      Promise.all([
        ok(api.GET("/api/household-plan/explanation", { params: { query: scenarioId ? { scenario: scenarioId } : {} } })),
        ok(api.GET("/api/workspace/settings")),
      ]).then(([explanation, settings]) => ({ explanation, settings })),
    [scenarioId],
  );
  const state = useLoad(load);
  const [polled, setPolled] = useState<PlanExplanation | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [baseline, setBaseline] = useState<{ id: string | null; since: number } | null>(null);
  const [tick, setTick] = useState(0);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (phase !== "waiting" || baseline === null) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      ok(api.GET("/api/household-plan/explanation", { params: { query: scenarioId ? { scenario: scenarioId } : {} } })).then(
        (fresh) => {
          if (cancelled) return;
          if (fresh.id != null && fresh.id !== baseline.id) {
            setPolled(fresh);
            setPhase("idle");
          } else if (Date.now() - baseline.since >= POLL_LIMIT_MS) {
            setPhase("timeout");
          } else {
            setTick((n) => n + 1);
          }
        },
        (caught: unknown) => {
          if (cancelled) return;
          setError(caught);
          setPhase("idle");
        },
      );
    }, POLL_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [phase, baseline, tick, scenarioId]);

  const data = state.data;
  if (data === undefined) {
    return (
      <aside className="plan-ai" aria-labelledby="plan-ai-title">
        <PanelHead />
        {state.error != null ? <ErrorNotice error={state.error} /> : <p className="muted">Lädt …</p>}
      </aside>
    );
  }

  const shown = polled ?? data.explanation;
  const aiOn = data.settings.ai_private_accounts && data.settings.ai_private_accounts_active;
  const stored = shown.id != null;
  const template = shown.status === "template";
  const showOff = phase === "off" || !aiOn;
  const why = template ? reasonText(shown.reason, aiOn, stored) : null;
  const day = shown.created_at ? berlinDate.format(new Date(shown.created_at)) : longDate(asOf);

  async function explainAgain() {
    setError(null);
    if (!aiOn) {
      setPhase("off");
      return;
    }
    const since = Date.now();
    try {
      const answer = await ok(api.POST("/api/household-plan/explanation", { body: { scenario: scenarioId } }));
      if (!answer.queued) {
        // The switch was turned off since this page read it: read it again, and say so.
        setPhase("off");
        state.reload();
        return;
      }
      setBaseline({ id: shown.id ?? null, since });
      setPhase("waiting");
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <aside className="plan-ai" aria-labelledby="plan-ai-title">
      <PanelHead>
        {template ? <span className="badge badge-muted">Standardtext</span> : null}
        {shown.stale ? <span className="badge badge-warn">Zahlen haben sich seitdem geändert</span> : null}
      </PanelHead>
      <p className="plan-ai-meta">
        Erklärung vom {day} ·{" "}
        {template
          ? "Diesen Text hat die App selbst aus euren Zahlen geschrieben."
          : "Diesen Text hat die KI formuliert; jede Zahl und jeden Namen hat die App eingesetzt und den Text geprüft."}
      </p>
      {why ? (
        <p className="plan-ai-meta" data-testid="plan-ai-reason">
          {why}
        </p>
      ) : null}
      {shown.stale ? (
        <p className="plan-ai-meta">
          Die Zahlen dieser Seite sind seit dieser Erklärung anders; „Neu erklären“ schreibt sie mit den Zahlen von heute.
        </p>
      ) : null}
      <div className="plan-ai-text">
        {(shown.paragraphs ?? []).map((paragraph, index) => (
          <p key={`${paragraph.topic}-${index}`}>{paragraph.text}</p>
        ))}
      </div>
      {showOff ? (
        <p className="notice notice-warn" id="plan-ai-off" data-testid="plan-ai-off">
          Die KI ist ausgeschaltet, deshalb fragt „Neu erklären“ sie nicht. Einschalten unter{" "}
          <Link href={`/settings#${AI_SETTINGS_ANCHOR}`}>Einstellungen › „Private Konten mit KI zuordnen“</Link>.
        </p>
      ) : null}
      <div>
        <button
          type="button"
          className="secondary"
          disabled={!aiOn || phase === "waiting"}
          aria-describedby={showOff ? "plan-ai-off" : undefined}
          onClick={() => void explainAgain()}
        >
          Neu erklären
        </button>
      </div>
      {phase === "waiting" ? (
        <p className="plan-ai-meta" role="status">
          Die KI schreibt die Erklärung; sie erscheint hier, sobald die App sie geprüft hat.
        </p>
      ) : null}
      {phase === "timeout" ? (
        <p className="plan-ai-meta" role="status">
          Die Erklärung wird noch geschrieben. Schaut in ein paar Minuten noch einmal hier vorbei.
        </p>
      ) : null}
      <ErrorNotice error={error} />
      <p className="plan-ai-footer">{FOOTER[shown.mode]}</p>
    </aside>
  );
}

function PanelHead({ children }: { children?: ReactNode }) {
  return (
    <div className="plan-ai-head">
      <span className="ai-badge">KI</span>
      <h2 id="plan-ai-title">Erklärung zu euren Zahlen</h2>
      {children}
    </div>
  );
}
