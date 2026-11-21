"use client";

// A return input's evidence (phase 4b follow-up, gap 3): the Beleg link of a row with a
// document, and for a row applied from a pre-fill proposal each value's page and quote, as the
// review showed them (GET /api/return-inputs/{id}/evidence, read on opening). Read-only: it
// shows what the document says and whether the value was changed on apply, never a figure.
import Link from "next/link";
import { useCallback, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { useT } from "@/i18n";
import { evidence as messages } from "@/i18n/returnInputs";

import { payFieldLabel } from "./labels";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";

type ReturnInputEvidence = components["schemas"]["ReturnInputEvidence"];

/** "Beleg": the row's document page (the link the row lists had only as a badge before). */
export function BelegLink({ documentId }: { documentId: string | null | undefined }) {
  const t = useT(messages);
  if (!documentId) return null;
  return <Link href={`/documents/${documentId}`}>{t.receipt}</Link>;
}

/** "Fundstellen": a fold-out that loads the row's evidence when first opened. */
export function EvidenceFold({ returnInputId }: { returnInputId: string }) {
  const t = useT(messages);
  const [open, setOpen] = useState(false);
  return (
    <details className="evidence-fold" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>{t.sources}</summary>
      {open ? <EvidenceList returnInputId={returnInputId} /> : null}
    </details>
  );
}

function EvidenceList({ returnInputId }: { returnInputId: string }) {
  const load = useCallback(
    () => ok(api.GET("/api/return-inputs/{return_input_id}/evidence", { params: { path: { return_input_id: returnInputId } } })),
    [returnInputId],
  );
  const state = useLoad(load);
  return <Loading state={state}>{(evidence) => <EvidenceView evidence={evidence} />}</Loading>;
}

function EvidenceView({ evidence }: { evidence: ReturnInputEvidence }) {
  const t = useT(messages);
  if (evidence.scrubbed) {
    return (
      <p className="muted">
        {t.scrubbed} <BelegLink documentId={evidence.document_id} />
      </p>
    );
  }
  if (!evidence.proposal_id) {
    return <p className="muted">{t.byHand}</p>;
  }
  const original =
    evidence.document_id && evidence.opens_inline ? `/api/documents/${evidence.document_id}/original?disposition=inline` : null;
  const values = evidence.values ?? [];
  if (values.length === 0) return <p className="muted">{t.noValues}</p>;
  return (
    <ul className="evidence-list">
      {values.map((value) => (
        <li key={value.field}>
          <span className="evidence-field">
            {payFieldLabel(value.field)}
            {value.decision === "edited" ? <span className="badge badge-info">{t.edited}</span> : null}
            {value.decision === "cleared" ? <span className="badge">{t.cleared}</span> : null}
          </span>
          {value.page != null && value.quote != null ? (
            <div className="source-quote">
              <blockquote>„{value.quote}“</blockquote>
              {original ? (
                <a href={`${original}#page=${value.page}`} target="_blank" rel="noopener">
                  {t.openOriginal(value.page)}
                </a>
              ) : (
                <span className="muted">{t.page(value.page)}</span>
              )}
            </div>
          ) : (
            <span className="muted">{t.noSource}</span>
          )}
        </li>
      ))}
    </ul>
  );
}
