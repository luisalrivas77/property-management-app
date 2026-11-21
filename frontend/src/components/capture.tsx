"use client";

// Beleg fotografieren (docs/phases/phase-2.md §9; docs/mockups/06-mobile-capture.png): the
// phone's camera through a file input, the photos as the pages of one document, and the list
// of the documents this browser session captured, each with its pre-fill proposal's status.
//
// - Photos go into a page strip first ("Seite hinzufügen" adds more; each page can be moved,
//   turned or removed). Each is drawn upright (its EXIF orientation applied) on a canvas, long
//   edge at most 2000 px, and re-encoded as JPEG at 0.85, so a 10-page document stays a few MB,
//   inside the upload limit and Claude's PDF limits. Re-encoding also drops the camera's
//   metadata (location, device).
// - "↻" turns a page a quarter clockwise (8 Nov 2026): over paper lying flat the phone guesses
//   the orientation, and its EXIF can be wrong. The page's JPEG is drawn rotated onto a canvas
//   with width and height swapped and re-encoded at the same quality; the old thumbnail URL is
//   revoked. The strip's buttons wait while it turns.
// - "Als ein Dokument hochladen" builds one PDF from the strip, a page per photo in strip order
//   and of its photo's aspect ratio, and uploads it with source = photo. A single photo goes the
//   same way, as a one-page PDF, so every capture is shaped alike. The PDF carries no dates or
//   producer (updateMetadata: false): the same photos give the same bytes, so the vault's
//   duplicate check (D9) still recognises a repeated upload. pdf-lib loads only on upload.
// - The upload leaves the form field `ai` out, so the server's default decides (as the
//   review page's upload does): with its AI on it queues a read and answers with the proposal.
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
import { formatter, useT } from "@/i18n";
import { capture as captureText } from "@/i18n/documentPage";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { documentLabel } from "./documents";
import { ACQUISITION_COST_KIND, DOCUMENT_TYPE, EXTRACTION_KIND } from "./labels";
import { useLoad } from "./useLoad";

// Shown dates and amounts follow the chosen language, read at call time (phase 10 D284).
const formatDate = (v: string | null | undefined) => formatter().date(v);
const formatDateTime = (v: string | null | undefined) => formatter().dateTime(v);
const formatMoney = (v: string | null | undefined) => formatter().money(v);

/** Photos only (§9): what Claude reads of the camera's output. HEIC is not offered (D30). */
export const CAPTURE_ACCEPT = "image/jpeg,image/png";

/** Pages per document: enough for any bill or letter, and a PDF of a few MB. */
export const MAX_PAGES = 20;
const MAX_EDGE_PX = 2000;
const JPEG_QUALITY = 0.85;
/** A page's long edge in PDF points: A4's long side (297 mm), so a page prints at its size. */
const PAGE_LONG_EDGE_PT = 842;

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
async function uploadCapture(file: File): Promise<{ document: Document; existed: boolean }> {
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
function statusPill(proposal: Proposal | null, t: typeof captureText.de): { text: string; variant: string } {
  if (proposal === null) return { text: t.notRead, variant: "badge-muted" };
  switch (proposal.status) {
    case "queued":
      return { text: t.queued, variant: "badge-muted" };
    case "running":
      return { text: t.running, variant: "badge-muted" };
    case "ready":
      return { text: t.ready, variant: "" };
    case "applied":
      return { text: t.applied, variant: "" };
    case "rejected":
      return { text: t.rejected, variant: "badge-muted" };
    case "superseded":
      return { text: t.superseded, variant: "badge-muted" };
    case "blocked_tenant_data":
      return { text: t.tenantData, variant: "badge-warn" };
    case "failed":
      return { text: t.failed, variant: "badge-danger" };
  }
}

function Recognized({ proposal, properties }: { proposal: Proposal; properties: Property[] | undefined }) {
  const t = useT(captureText);
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
          <span className="ai-badge">{t.aiBadge}</span>
          {t.recognizedAs(type)}
        </p>
      ) : null}
      {title || date || amount ? (
        <div className="capture-facts">
          <div>
            {title ? <p className="capture-title">{title}</p> : null}
            {date ? <p className="muted capture-date">{t.dated(formatDate(date))}</p> : null}
          </div>
          {amount ? <p className="capture-amount">{formatMoney(amount)}</p> : null}
        </div>
      ) : null}
      {property || costKind ? (
        <div className="capture-chips">
          {property ? <span className="capture-chip">{property.label}</span> : null}
          {costKind ? (
            <span className="capture-chip">{t.costChip(ACQUISITION_COST_KIND[costKind as keyof typeof ACQUISITION_COST_KIND] ?? costKind)}</span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** One captured document: its name, when it came in, the proposal's status and what it found. */
function CapturedRow({ entry, properties }: { entry: Captured; properties: Property[] | undefined }) {
  const { document, existed } = entry;
  const t = useT(captureText);
  const { proposal, error, retry } = useNewestProposal(document.id);
  const pill = proposal === undefined ? null : statusPill(proposal, t);
  const href = `/documents/${document.id}`;

  let detail = null;
  if (proposal === null) {
    detail = <p className="muted capture-note">{aiErrorText("ai.disabled")} {t.typeByHand}</p>;
  } else if (proposal && IN_FLIGHT.includes(proposal.status)) {
    detail = <p className="muted capture-note">{t.refreshesItself}</p>;
  } else if (proposal?.status === "failed") {
    detail = <p className="capture-note">{t.couldNotRead(aiErrorText(proposal.error_code))}</p>;
  } else if (proposal?.status === "blocked_tenant_data") {
    detail = <p className="capture-note">{aiErrorText("ai.blocked_tenant_data")} {t.enterByHand}</p>;
  }

  return (
    <li className="capture-item">
      <div className="capture-head">
        <div className="capture-name">
          <Link href={href}>{documentLabel(document)}</Link>
          <p className="muted capture-date">
            {existed ? t.alreadyThere : null}
            {t.received(formatDateTime(document.received_at))}
          </p>
        </div>
        {pill ? <span className={`badge ${pill.variant}`.trim()}>{pill.text}</span> : <span className="muted">{t.loading}</span>}
      </div>
      {proposal ? <Recognized proposal={proposal} properties={properties} /> : null}
      {detail}
      {proposal?.status === "ready" ? (
        <Link className="button-link capture-review" href={href}>
          {t.reviewSuggestion}
        </Link>
      ) : null}
      {error != null ? (
        <div>
          <ErrorNotice error={error} />
          <button type="button" className="secondary" onClick={retry}>
            {t.retry}
          </button>
        </div>
      ) : null}
    </li>
  );
}

// --- pages: photo → upright, downscaled JPEG → one PDF ------------------------------------

interface StripPage {
  key: string;
  jpeg: Blob;
  width: number;
  height: number;
  /** An object URL of `jpeg`, for the thumbnail; revoked when the page leaves the strip. */
  url: string;
}

/** A photo as the page it becomes: drawn upright (EXIF orientation), long edge at most
 * 2000 px, on white (a PNG's transparency), re-encoded as JPEG at 0.85. */
async function preparePage(file: File): Promise<Omit<StripPage, "key" | "url">> {
  const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  try {
    const scale = Math.min(1, MAX_EDGE_PX / Math.max(bitmap.width, bitmap.height));
    const width = Math.max(1, Math.round(bitmap.width * scale));
    const height = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("no 2d canvas");
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, width, height);
    context.drawImage(bitmap, 0, 0, width, height);
    return { jpeg: await canvasJpeg(canvas), width, height };
  } finally {
    bitmap.close();
  }
}

/** A page turned 90° clockwise: its JPEG drawn rotated on a canvas with width and height
 * swapped, re-encoded as JPEG at 0.85 like every page. */
async function rotatePage(page: Pick<StripPage, "jpeg" | "width" | "height">): Promise<Omit<StripPage, "key" | "url">> {
  const bitmap = await createImageBitmap(page.jpeg);
  try {
    const width = page.height;
    const height = page.width;
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("no 2d canvas");
    // A source point (x, y) lands at (width - y, x): a quarter turn clockwise.
    context.translate(width, 0);
    context.rotate(Math.PI / 2);
    context.drawImage(bitmap, 0, 0, page.width, page.height);
    return { jpeg: await canvasJpeg(canvas), width, height };
  } finally {
    bitmap.close();
  }
}

function canvasJpeg(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("canvas.toBlob failed"))), "image/jpeg", JPEG_QUALITY),
  );
}

/** One PDF, a page per photo in this order, each page of its photo's aspect ratio. */
export async function buildPdf(pages: readonly Pick<StripPage, "jpeg" | "width" | "height">[]): Promise<Blob> {
  const { PDFDocument } = await import("pdf-lib");
  const pdf = await PDFDocument.create({ updateMetadata: false });
  for (const page of pages) {
    const image = await pdf.embedJpg(await page.jpeg.arrayBuffer());
    const scale = PAGE_LONG_EDGE_PT / Math.max(page.width, page.height);
    const width = page.width * scale;
    const height = page.height * scale;
    pdf.addPage([width, height]).drawImage(image, { x: 0, y: 0, width, height });
  }
  const bytes = await pdf.save();
  return new Blob([bytes.slice().buffer], { type: "application/pdf" });
}

/** "Beleg 2026-11-07 14-05.pdf": phones name every photo alike (image.jpg), so the time. */
function captureName(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `Beleg ${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}-${pad(now.getMinutes())}.pdf`;
}

/** The page strip and the session's list. */
export function CaptureScreen() {
  const t = useT(captureText);
  const inputId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [captured, setCaptured] = useState<Captured[]>([]);
  const [pending, setPending] = useState<Pending[]>([]);
  const [pages, setPages] = useState<StripPage[]>([]);
  const [preparing, setPreparing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const properties = useLoad(loadProperties);
  const counter = useRef(0);
  const pagesRef = useRef(pages);

  // sessionStorage exists only in the browser, so the stored list is read after mount.
  useEffect(() => {
    const stored = readStored();
    if (stored.length > 0) setCaptured(stored);
  }, []);

  // The thumbnails' object URLs are freed when the page is left.
  useEffect(() => {
    pagesRef.current = pages;
  }, [pages]);
  useEffect(() => () => pagesRef.current.forEach((page) => URL.revokeObjectURL(page.url)), []);

  function nextKey(): string {
    counter.current += 1;
    return `${Date.now()}-${counter.current}`;
  }

  async function addPages(files: FileList | null) {
    if (!files || files.length === 0) return;
    const chosen = Array.from(files);
    if (input.current) input.current.value = "";
    setNotice(null);
    const room = MAX_PAGES - pages.length;
    const taken = chosen.slice(0, Math.max(0, room));
    const messages: string[] = [];
    if (taken.length < chosen.length) {
      messages.push(t.tooManyPages(MAX_PAGES, chosen.length - taken.length));
    }
    setPreparing(true);
    let unreadable = 0;
    for (const file of taken) {
      try {
        const page = await preparePage(file);
        const entry: StripPage = { ...page, key: nextKey(), url: URL.createObjectURL(page.jpeg) };
        setPages((current) => [...current, entry]);
      } catch {
        unreadable += 1;
      }
    }
    if (unreadable > 0) {
      messages.push(t.unreadable(unreadable));
    }
    setPreparing(false);
    if (messages.length > 0) setNotice(messages.join(" "));
  }

  function move(index: number, by: -1 | 1) {
    setPages((current) => {
      const page = current[index];
      const target = index + by;
      if (!page || target < 0 || target >= current.length) return current;
      const next = current.filter((_, at) => at !== index);
      next.splice(target, 0, page);
      return next;
    });
  }

  /** Every strip button is disabled while this runs, so the page cannot leave the strip. */
  async function rotate(key: string) {
    const page = pages.find((entry) => entry.key === key);
    if (!page) return;
    setNotice(null);
    setRotating(true);
    try {
      const turned = await rotatePage(page);
      const url = URL.createObjectURL(turned.jpeg);
      setPages((current) => current.map((entry) => (entry.key === key ? { ...entry, ...turned, url } : entry)));
      URL.revokeObjectURL(page.url);
    } catch {
      setNotice(t.rotateFailed);
    } finally {
      setRotating(false);
    }
  }

  function remove(key: string) {
    setNotice(null);
    setPages((current) => {
      const page = current.find((entry) => entry.key === key);
      if (page) URL.revokeObjectURL(page.url);
      return current.filter((entry) => entry.key !== key);
    });
  }

  function clearStrip() {
    pages.forEach((page) => URL.revokeObjectURL(page.url));
    setPages([]);
    setNotice(null);
  }

  async function upload() {
    if (pages.length === 0) return;
    const key = nextKey();
    const fileName = captureName();
    setUploading(true);
    setNotice(null);
    setPending((current) => [{ key, fileName, error: null }, ...current]);
    try {
      const pdf = await buildPdf(pages);
      const { document, existed } = await uploadCapture(new File([pdf], fileName, { type: "application/pdf" }));
      setPending((current) => current.filter((entry) => entry.key !== key));
      setCaptured((current) => {
        const next = [{ key, document, existed }, ...current];
        writeStored(next);
        return next;
      });
      clearStrip();
    } catch (caught) {
      // The strip stays, so the same pages can be sent again.
      setPending((current) => current.map((entry) => (entry.key === key ? { ...entry, error: caught } : entry)));
    } finally {
      setUploading(false);
    }
  }

  function dismiss(key: string) {
    setPending((current) => current.filter((entry) => entry.key !== key));
  }

  const busy = preparing || uploading || rotating;
  const full = pages.length >= MAX_PAGES;
  const empty = captured.length === 0 && pending.length === 0;
  const buttonText = preparing ? t.preparing : pages.length === 0 ? t.takePhoto : t.addPage;

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
          disabled={busy || full}
          onChange={(event: ChangeEvent<HTMLInputElement>) => void addPages(event.target.files)}
        />
        <label
          htmlFor={inputId}
          className={["capture-button", pages.length > 0 ? "secondary" : "", busy || full ? "busy" : ""].filter(Boolean).join(" ")}
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true">
            <path d="M4 8h3l2-3h6l2 3h3v11H4z" />
            <circle cx="12" cy="13" r="3.5" />
          </svg>
          {buttonText}
        </label>
        <p className="muted hint">
          {full ? t.pagesReached(MAX_PAGES) : t.onePhotoPerPage}
        </p>
      </div>

      {notice ? (
        <div className="notice notice-warn" role="alert">
          <p>{notice}</p>
        </div>
      ) : null}

      {pages.length > 0 ? (
        <section className="card capture-strip" aria-labelledby="capture-strip-title">
          <h2 id="capture-strip-title">
            {t.pageCount(pages.length)}
            <span className="muted">{t.atMost(MAX_PAGES)}</span>
          </h2>
          <ol className="capture-pages" aria-label={t.pages}>
            {pages.map((page, index) => (
              <li key={page.key} className="capture-page">
                {/* eslint-disable-next-line @next/next/no-img-element -- a local object URL, nothing for next/image to optimise */}
                <img src={page.url} alt={t.page(index + 1)} />
                <span className="capture-page-number">{t.page(index + 1)}</span>
                <div className="capture-page-actions">
                  <button
                    type="button"
                    className="secondary"
                    aria-label={t.pageForward(index + 1)}
                    disabled={busy || index === 0}
                    onClick={() => move(index, -1)}
                  >
                    ←
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    aria-label={t.pageBack(index + 1)}
                    disabled={busy || index === pages.length - 1}
                    onClick={() => move(index, 1)}
                  >
                    →
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    aria-label={t.pageRotate(index + 1)}
                    disabled={busy}
                    onClick={() => void rotate(page.key)}
                  >
                    ↻
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    aria-label={t.pageRemove(index + 1)}
                    disabled={busy}
                    onClick={() => remove(page.key)}
                  >
                    ✕
                  </button>
                </div>
              </li>
            ))}
          </ol>
          <div className="capture-strip-actions">
            <button type="button" disabled={busy} onClick={() => void upload()}>
              {uploading ? t.uploading : t.uploadAsOne}
            </button>
            <button type="button" className="link" disabled={busy} onClick={clearStrip}>
              {t.discardAll}
            </button>
          </div>
        </section>
      ) : null}

      <section className="card capture-session" aria-labelledby="capture-session-title">
        <h2 id="capture-session-title">{t.thisSession}</h2>
        {empty ? (
          <p className="muted">{t.noneYet}</p>
        ) : (
          <ul className="capture-list" aria-label={t.uploadedReceipts}>
            {pending.map((entry) => (
              <li key={entry.key} className="capture-item">
                <div className="capture-head">
                  <span className="capture-name">{entry.fileName}</span>
                  {entry.error == null ? <span className="badge badge-muted">{t.uploadingPill}</span> : <span className="badge badge-danger">{t.error}</span>}
                </div>
                {entry.error != null ? (
                  <div>
                    <ErrorNotice error={entry.error} />
                    <button type="button" className="link" onClick={() => dismiss(entry.key)}>
                      {t.dismiss}
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
