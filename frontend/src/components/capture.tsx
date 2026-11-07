"use client";

// Beleg fotografieren (docs/phases/phase-2.md §9, D30; docs/mockups/06-mobile-capture.png): the
// phone's camera through a file input, one upload per photo with source = photo, and the list
// of the documents this browser session captured, each with its pre-fill proposal's status.
//
// - The upload leaves the form field `ai` out, so the server's default decides (as the
//   Posteingang's upload does): with its AI on it queues a read and answers with the proposal.
// - Each row loads its document's newest proposal (GET /documents/{id}/proposals) and polls
//   GET /proposals/{id} every 2 s while it is queued or running; polling stops on any other
//   status and on unmount. The review itself is the document page's (Vorschlag, §6.4).
// - What the mockup's card shows comes from the proposal only: the recognized type (typing
//   record, else the proposal's kind), the document date, the suggested property (the
//   `link:property` record) and, for a Nebenkosten bill, its cost kind and amount. Amounts are
//   the backend's, only reformatted (principle 1).
// - The list survives going to a document page and back: the captured documents are kept in
//   sessionStorage (this tab only; a convenience, nothing depends on it).
import Link from "next/link";
import { useCallback, useEffect, useId, useRef, useState, type ChangeEvent } from "react";

import { api, ok } from "@/api/client";
import { aiErrorText } from "@/api/errors";
import type { Document, DocumentUploadResult, Property, Proposal, ProposalRecord, ProposalStatus, UploadBody } from "@/api/schemas";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { documentLabel } from "./documents";
import { formatDate, formatDateTime, formatMoney } from "./format";
import { ACQUISITION_COST_KIND, DOCUMENT_TYPE, EXTRACTION_KIND } from "./labels";
import { useLoad } from "./useLoad";

/** Photos only (§9): what Claude reads of the camera's output. HEIC is not offered (D30). */
export const CAPTURE_ACCEPT = "image/jpeg,image/png";

const POLL_MS = 2_000;
const IN_FLIGHT: readonly ProposalStatus[] = ["queued", "running"];
const STORAGE_KEY = "hausbuch.capture.documents";

interface Captured {
  key: string;
  document: Document;
  /** The same bytes were already in the vault (200 instead of 201, D9). */
  existed: boolean;
}

interface Pending {
  key: string;
  fileName: string;
  /** Null while the upload runs. */
  error: unknown;
}

function isUploadResult(data: Document | DocumentUploadResult): data is DocumentUploadResult {
  return "document" in data;
}

/** POST /documents as multipart with source = photo (§9). Without `ai` the server's default
 * decides; the proposal is found by the row, so only the document is kept here. */
async function uploadPhoto(file: File): Promise<{ document: Document; existed: boolean }> {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("source", "photo");
  const { data, response } = await api.POST("/api/documents", { body: form as unknown as UploadBody });
  if (!data) throw new Error("POST /documents answered without a body");
  return { document: isUploadResult(data) ? data.document : data, existed: response.status === 200 };
}

function readStored(): Captured[] {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as Captured[]).filter((entry) => entry && typeof entry.document?.id === "string") : [];
  } catch {
    return [];
  }
}

function writeStored(entries: Captured[]): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // private mode or blocked storage: the list then lasts as long as the page
  }
}

const loadProperties = () => ok(api.GET("/api/properties"));

// --- the document's newest proposal, polled while it is read -----------------------------

function useNewestProposal(documentId: string) {
  const [proposal, setProposal] = useState<Proposal | null | undefined>(undefined);
  const [error, setError] = useState<unknown>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    ok(api.GET("/api/documents/{document_id}/proposals", { params: { path: { document_id: documentId } } })).then(
      (list) => {
        if (cancelled) return;
        setProposal(list[0] ?? null);
        setError(null);
      },
      (caught: unknown) => {
        if (!cancelled) setError(caught);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [documentId, version]);

  // Each answer is a new object, so the effect re-arms after every poll while in flight; a
  // failed poll stops it (the error offers a retry, which reloads the list).
  useEffect(() => {
    if (!proposal || !IN_FLIGHT.includes(proposal.status)) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      ok(api.GET("/api/proposals/{proposal_id}", { params: { path: { proposal_id: proposal.id } } })).then(
        (next) => {
          if (!cancelled) setProposal(next);
        },
        (caught: unknown) => {
          if (!cancelled) setError(caught);
        },
      );
    }, POLL_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [proposal]);

  const retry = useCallback(() => {
    setError(null);
    setVersion((n) => n + 1);
  }, []);
  return { proposal, error, retry };
}

// --- what the proposal says ------------------------------------------------------------

function record(proposal: Proposal, key: string): ProposalRecord | undefined {
  return (proposal.records ?? []).find((entry) => entry.key === key);
}

function stringValue(entry: ProposalRecord | undefined, field: string): string | null {
  const value = entry?.fields?.[field]?.value;
  return typeof value === "string" && value ? value : null;
}

/** "Rechnung" from the typing record, else from the kind of reading. */
function recognizedType(proposal: Proposal): string | null {
  const type = stringValue(record(proposal, "typing"), "type");
  if (type && type in DOCUMENT_TYPE) return DOCUMENT_TYPE[type as keyof typeof DOCUMENT_TYPE];
  return proposal.kind && proposal.kind !== "manual" ? EXTRACTION_KIND[proposal.kind] : null;
}

function costRecord(proposal: Proposal): ProposalRecord | undefined {
  return (proposal.records ?? []).find((entry) => entry.key.startsWith("cost:"));
}

/** The status pill: its text and the .badge variant. */
function statusPill(proposal: Proposal | null): { text: string; variant: string } {
  if (proposal === null) return { text: "Nicht ausgelesen", variant: "badge-muted" };
  switch (proposal.status) {
    case "queued":
      return { text: "Wartet auf das Auslesen", variant: "badge-muted" };
    case "running":
      return { text: "Wird ausgelesen", variant: "badge-muted" };
    case "ready":
      return { text: "Vorschlag bereit", variant: "" };
    case "applied":
      return { text: "Übernommen", variant: "" };
    case "rejected":
      return { text: "Verworfen", variant: "badge-muted" };
    case "superseded":
      return { text: "Ersetzt", variant: "badge-muted" };
    case "blocked_tenant_data":
      return { text: "Mieterdaten", variant: "badge-warn" };
    case "failed":
      return { text: "Nicht lesbar", variant: "badge-danger" };
  }
}

function Recognized({ proposal, properties }: { proposal: Proposal; properties: Property[] | undefined }) {
  const type = recognizedType(proposal);
  const typing = record(proposal, "typing");
  const title = stringValue(typing, "title");
  const date = stringValue(typing, "document_date");
  const propertyId = record(proposal, "link:property")?.target_id;
  const property = propertyId ? properties?.find((entry) => entry.id === propertyId) : undefined;
  const cost = costRecord(proposal);
  const costKind = cost ? cost.key.slice("cost:".length) : null;
  const amount = stringValue(cost, "amount");
  if (!type && !title && !property && !cost) return null;

  return (
    <div className="capture-recognized">
      {type ? (
        <p className="allocation-source">
          <span className="ai-badge">KI</span>
          Erkannt als {type}
        </p>
      ) : null}
      {title || date || amount ? (
        <div className="capture-facts">
          <div>
            {title ? <p className="capture-title">{title}</p> : null}
            {date ? <p className="muted capture-date">vom {formatDate(date)}</p> : null}
          </div>
          {amount ? <p className="capture-amount">{formatMoney(amount)}</p> : null}
        </div>
      ) : null}
      {property || costKind ? (
        <div className="capture-chips">
          {property ? <span className="capture-chip">{property.label}</span> : null}
          {costKind ? (
            <span className="capture-chip">Nebenkosten: {ACQUISITION_COST_KIND[costKind as keyof typeof ACQUISITION_COST_KIND] ?? costKind}</span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** One captured document: its name, when it came in, the proposal's status and what it found. */
function CapturedRow({ entry, properties }: { entry: Captured; properties: Property[] | undefined }) {
  const { document, existed } = entry;
  const { proposal, error, retry } = useNewestProposal(document.id);
  const pill = proposal === undefined ? null : statusPill(proposal);
  const href = `/documents/${document.id}`;

  let detail = null;
  if (proposal === null) {
    detail = <p className="muted capture-note">{aiErrorText("ai.disabled")} Auf der Seite des Dokuments von Hand typisieren.</p>;
  } else if (proposal && IN_FLIGHT.includes(proposal.status)) {
    detail = <p className="muted capture-note">Die Liste aktualisiert sich von selbst.</p>;
  } else if (proposal?.status === "failed") {
    detail = <p className="capture-note">Konnte nicht gelesen werden: {aiErrorText(proposal.error_code)}</p>;
  } else if (proposal?.status === "blocked_tenant_data") {
    detail = <p className="capture-note">{aiErrorText("ai.blocked_tenant_data")} Bitte von Hand eingeben.</p>;
  }

  return (
    <li className="capture-item">
      <div className="capture-head">
        <div className="capture-name">
          <Link href={href}>{documentLabel(document)}</Link>
          <p className="muted capture-date">
            {existed ? "Bereits vorhanden · " : null}Eingang {formatDateTime(document.received_at)}
          </p>
        </div>
        {pill ? <span className={`badge ${pill.variant}`.trim()}>{pill.text}</span> : <span className="muted">Lädt …</span>}
      </div>
      {proposal ? <Recognized proposal={proposal} properties={properties} /> : null}
      {detail}
      {proposal?.status === "ready" ? (
        <Link className="button-link capture-review" href={href}>
          Vorschlag prüfen
        </Link>
      ) : null}
      {error != null ? (
        <div>
          <ErrorNotice error={error} />
          <button type="button" className="secondary" onClick={retry}>
            Erneut versuchen
          </button>
        </div>
      ) : null}
    </li>
  );
}

/** The capture button and the session's list. */
export function CaptureScreen() {
  const inputId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [captured, setCaptured] = useState<Captured[]>([]);
  const [pending, setPending] = useState<Pending[]>([]);
  const [busy, setBusy] = useState(false);
  const properties = useLoad(loadProperties);
  const counter = useRef(0);

  // sessionStorage exists only in the browser, so the stored list is read after mount.
  useEffect(() => {
    const stored = readStored();
    if (stored.length > 0) setCaptured(stored);
  }, []);

  function nextKey(): string {
    counter.current += 1;
    return `${Date.now()}-${counter.current}`;
  }

  async function upload(files: FileList | null) {
    if (!files || files.length === 0) return;
    const chosen = Array.from(files).map((file) => ({ file, key: nextKey() }));
    if (input.current) input.current.value = "";
    setBusy(true);
    setPending((current) => [...chosen.map(({ file, key }) => ({ key, fileName: file.name, error: null })), ...current]);
    // One photo after the other: a phone's upload is the slow part, and a failed photo must
    // not stop the ones after it (each keeps its own error).
    for (const { file, key } of chosen) {
      try {
        const { document, existed } = await uploadPhoto(file);
        setPending((current) => current.filter((entry) => entry.key !== key));
        setCaptured((current) => {
          const next = [{ key, document, existed }, ...current];
          writeStored(next);
          return next;
        });
      } catch (caught) {
        setPending((current) => current.map((entry) => (entry.key === key ? { ...entry, error: caught } : entry)));
      }
    }
    setBusy(false);
  }

  function dismiss(key: string) {
    setPending((current) => current.filter((entry) => entry.key !== key));
  }

  const empty = captured.length === 0 && pending.length === 0;

  return (
    <div className="capture">
      <div className="capture-action">
        <input
          ref={input}
          id={inputId}
          className="visually-hidden capture-input"
          type="file"
          accept={CAPTURE_ACCEPT}
          capture="environment"
          multiple
          disabled={busy}
          onChange={(event: ChangeEvent<HTMLInputElement>) => void upload(event.target.files)}
        />
        <label htmlFor={inputId} className={busy ? "capture-button busy" : "capture-button"}>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true">
            <path d="M4 8h3l2-3h6l2 3h3v11H4z" />
            <circle cx="12" cy="13" r="3.5" />
          </svg>
          {busy ? "Lädt hoch …" : "Beleg fotografieren"}
        </label>
        <p className="muted hint">Jedes Foto wird ein eigenes Dokument (JPEG oder PNG). Mehrseitige Belege besser als PDF hochladen.</p>
      </div>

      <section className="card capture-session" aria-labelledby="capture-session-title">
        <h2 id="capture-session-title">In dieser Sitzung</h2>
        {empty ? (
          <p className="muted">Noch keine Fotos. Fotografierte Belege erscheinen hier mit dem Stand des Auslesens.</p>
        ) : (
          <ul className="capture-list" aria-label="Fotografierte Belege">
            {pending.map((entry) => (
              <li key={entry.key} className="capture-item">
                <div className="capture-head">
                  <span className="capture-name">{entry.fileName || "Foto"}</span>
                  {entry.error == null ? <span className="badge badge-muted">Lädt hoch</span> : <span className="badge badge-danger">Fehler</span>}
                </div>
                {entry.error != null ? (
                  <div>
                    <ErrorNotice error={entry.error} />
                    <button type="button" className="link" onClick={() => dismiss(entry.key)}>
                      Ausblenden
                    </button>
                  </div>
                ) : null}
              </li>
            ))}
            {captured.map((entry) => (
              <CapturedRow key={entry.key} entry={entry} properties={properties.data} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
