"use client";

// Documents shared by several screens (§9.1): upload, the document picker (Nebenkosten,
// Kaufpreisaufteilung, Mietstaffel; one component over GET /documents with an inline upload),
// and the "linked documents" panel of an entity page (on a property or unit page it also lists
// the documents of its units, tenancies and loans).
import Link from "next/link";
import { useCallback, useId, useRef, useState, type ChangeEvent } from "react";

import { api, ok } from "@/api/client";
import type {
  Document,
  DocumentExplanation,
  DocumentSubtype,
  DocumentUploadResult,
  LinkTarget,
  LinkTargetRef,
  ProposalStatus,
  RelatedDocument,
  UploadBody,
} from "@/api/schemas";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { formatDate, shortId } from "./format";
import { DOCUMENT_TYPE, LINK_TARGET } from "./labels";
import { Loading, Notice } from "./ui";
import { useLoad } from "./useLoad";

// Subtypes whose retention runs from ownership or tenancy end dates, not a tax year
// (backend rules/retention.py _CLASS_BY_SUBTYPE), and whose checklist items ignore the tax
// year: the Steuerjahr field is hidden for them while it is empty (Luis, 7 Nov 2026), on the
// typing form and on the proposal's typing card. A stored year stays visible so it can be
// seen and cleared.
const NO_TAX_YEAR_SUBTYPES: readonly DocumentSubtype[] = [
  "purchase_contract",
  "price_split_evidence",
  "appraisal",
  "loan_contract",
  "lease",
  "handover_protocol",
  "deposit_record",
  // the document catalogue's O and M subtypes (phase-2.md §6.2.1, D130, D131)
  "land_register_extract",
  "declaration_of_division",
  "land_charge_deed",
  "energy_certificate",
  "building_records",
  "insurance_policy",
  "management_contract",
  "service_contract",
  "weg_minutes",
  "repayment_schedule",
  "loan_terms_change",
  "lease_amendment",
  "rent_increase",
  "termination_notice",
  "tenant_self_disclosure",
  "landlord_confirmation",
  "operating_costs_statement",
  "tenant_correspondence",
];
export const NO_TAX_YEAR: ReadonlySet<string> = new Set<string>(
  NO_TAX_YEAR_SUBTYPES,
);

/** Accepted by POST /documents (§4.7, Q8); the API decides, this only filters the picker. */
export const UPLOAD_ACCEPT =
  "application/pdf,image/jpeg,image/png,image/heic,image/webp,text/csv,text/plain,application/xml,text/xml,message/rfc822,.pdf,.jpg,.jpeg,.png,.heic,.webp,.csv,.txt,.xml,.eml";

export interface Uploaded {
  document: Document;
  /** The same bytes were already in the vault (200 instead of 201, D9). */
  existed: boolean;
  /** The pre-fill proposal the server queued, or the pending one for bytes already in the
   * vault; the document page polls it (phase-2.md §6.1). Null without one. */
  proposalId: string | null;
  proposalStatus: ProposalStatus | null;
  /** The upload asked for automatic reading but the server answered without a proposal: its
   * AI is switched off (HAUSBUCH_AI_ENABLED), so the document is typed by hand. */
  aiUnavailable: boolean;
}

export interface UploadOptions {
  /** Read the document with the AI (pre-fill, §6.1). True leaves the form field `ai` out, so
   * the server's default (on when its AI is enabled) decides, and a server without AI never
   * queues a read that could only fail; false sends `ai=false` (phase 1's upload). */
  autoRead: boolean;
  /** The uploader says the document holds tenant data: the privacy gate (D16) blocks the
   * read until the workspace allows tenant documents. */
  containsTenantData: boolean;
  /** Phase 3 D143: the upload is the payment proof of this bill (a document id); the server
   * types it „Zahlungsnachweis“, links it to the bill and its property, and reads it without
   * the classifier. */
  proofFor?: string;
}

const MANUAL_UPLOAD: UploadOptions = {
  autoRead: false,
  containsTenantData: false,
};

/** POST /documents answers with the Document, or with a DocumentUploadResult (the document
 * plus the proposal to poll) when it read the upload with AI; a Document has no `document`. */
function isUploadResult(
  data: Document | DocumentUploadResult,
): data is DocumentUploadResult {
  return "document" in data;
}

/** POST /documents as multipart. The generated body type names `file` a string (OpenAPI's
 * `format: binary`); openapi-fetch sends a FormData body as is, so the FormData is passed
 * under that type. Without options the upload is phase 1's: no automatic reading. */
export async function uploadDocument(
  file: File,
  options: UploadOptions = MANUAL_UPLOAD,
): Promise<Uploaded> {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("source", "upload");
  if (!options.autoRead) form.append("ai", "false");
  if (options.containsTenantData) form.append("contains_tenant_data", "true");
  if (options.proofFor) form.append("proof_for", options.proofFor);
  const { data, response } = await api.POST("/api/documents", {
    body: form as unknown as UploadBody,
  });
  if (!data) throw new Error("POST /documents answered without a body");
  const existed = response.status === 200;
  if (isUploadResult(data)) {
    return {
      document: data.document,
      existed,
      proposalId: data.proposal_id ?? null,
      proposalStatus: data.proposal_status ?? null,
      aiUnavailable: options.autoRead && !data.proposal_id,
    };
  }
  return {
    document: data,
    existed,
    proposalId: null,
    proposalStatus: null,
    aiUnavailable: options.autoRead,
  };
}

/** What a person recognises a document by: its title, file name, or hash prefix. */
export function documentLabel(document: Document): string {
  return (
    document.typing?.title ||
    document.original_filename ||
    `Dokument ${shortId(document.file_sha256, 12)}`
  );
}

/** "Rechnung · 12.03.2024" or "nicht typisiert". */
export function documentKind(document: Document): string {
  if (!document.typing) return "nicht typisiert";
  const date = document.typing.document_date
    ? ` · ${formatDate(document.typing.document_date)}`
    : "";
  return `${DOCUMENT_TYPE[document.typing.type]}${date}`;
}

/**
 * File input (plus drag and drop) that uploads each chosen file. With `prefill` it offers
 * "Automatisch auslesen" (on by default) and "Enthält Mieterdaten" (phase-2.md §6.1, D16);
 * without it (the inline upload of a form typed by hand) nothing is read automatically.
 */
export function UploadControl({
  onUploaded,
  label = "Datei hochladen",
  prefill = false,
  proofFor,
}: {
  onUploaded: (result: Uploaded) => void;
  label?: string;
  prefill?: boolean;
  /** Phase 3 D143: upload the payment proof of this bill (UploadOptions.proofFor). */
  proofFor?: string;
}) {
  const inputId = useId();
  const autoReadId = useId();
  const tenantDataId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [autoRead, setAutoRead] = useState(true);
  const [containsTenantData, setContainsTenantData] = useState(false);

  async function upload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true);
    setError(null);
    const options: UploadOptions = prefill
      ? {
          autoRead,
          containsTenantData: autoRead && containsTenantData,
          proofFor,
        }
      : { ...MANUAL_UPLOAD, proofFor };
    try {
      for (const file of Array.from(files))
        onUploaded(await uploadDocument(file, options));
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div
      className={over ? "dropzone over" : "dropzone"}
      onDragOver={(event) => {
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        void upload(event.dataTransfer.files);
      }}
    >
      <label htmlFor={inputId}>{busy ? "Lädt hoch …" : label}</label>
      <input
        ref={input}
        id={inputId}
        type="file"
        accept={UPLOAD_ACCEPT}
        multiple
        disabled={busy}
        onChange={(event: ChangeEvent<HTMLInputElement>) =>
          void upload(event.target.files)
        }
      />
      <p className="muted hint">
        PDF, Foto, CSV, XML oder E-Mail; Datei hierher ziehen oder auswählen.
      </p>
      {prefill ? (
        <div className="row upload-options">
          <label className="inline" htmlFor={autoReadId}>
            <input
              id={autoReadId}
              type="checkbox"
              checked={autoRead}
              disabled={busy}
              onChange={(e) => setAutoRead(e.target.checked)}
            />
            Automatisch auslesen
          </label>
          <label className="inline" htmlFor={tenantDataId}>
            <input
              id={tenantDataId}
              type="checkbox"
              checked={containsTenantData}
              disabled={busy || !autoRead}
              onChange={(e) => setContainsTenantData(e.target.checked)}
            />
            Enthält Mieterdaten
          </label>
        </div>
      ) : null}
      <ErrorNotice error={error} />
    </div>
  );
}

/**
 * Upload documents to be read automatically from a page that uses their figures (phase-4b.md
 * §10, D129): the Dokumente upload with "Automatisch auslesen" on, behind a button; each result
 * links to its document page, where the suggestion is checked, and to Zu prüfen.
 */
export function ReadUpload({
  label,
  onUploaded,
  proofFor,
}: {
  label: string;
  onUploaded?: () => void;
  /** Phase 3 D143: the upload is the payment proof of this bill (a document id). */
  proofFor?: string;
}) {
  const [open, setOpen] = useState(false);
  const [uploads, setUploads] = useState<Uploaded[]>([]);
  return (
    <div className="stack">
      <div className="row">
        <button
          type="button"
          className="link"
          onClick={() => setOpen((current) => !current)}
          aria-expanded={open}
        >
          {open ? "Hochladen schließen" : label}
        </button>
      </div>
      {open ? (
        <UploadControl
          label={label}
          prefill
          proofFor={proofFor}
          onUploaded={(result) => {
            setUploads((current) => [result, ...current].slice(0, 5));
            onUploaded?.();
          }}
        />
      ) : null}
      {uploads.map(({ document, existed, proposalId, aiUnavailable }) => (
        <Notice key={`${document.id}-${existed}-${proposalId ?? ""}`}>
          {existed ? "Bereits vorhanden: " : "Hochgeladen: "}
          <Link href={`/documents/${document.id}`}>
            {documentLabel(document)}
          </Link>
          {proposalId ? (
            <>
              {" – wird automatisch ausgelesen. Der Vorschlag erscheint unter "}
              <Link href="/inbox">Zu prüfen</Link>
              {"; jeden Wert bestätigen, dann zählt er."}
            </>
          ) : null}
          {aiUnavailable
            ? " Automatisches Auslesen ist auf dem Server nicht eingeschaltet."
            : null}
        </Notice>
      ))}
    </div>
  );
}

const loadDocuments = () => ok(api.GET("/api/documents"));

/**
 * Choose one document from the vault, or upload a new one inline (§9.1). Controlled: `value`
 * is a document id or null. Accepts the id / aria props a <Field> passes to its input.
 */
export function DocumentPicker({
  value,
  onChange,
  id,
  ...aria
}: {
  value: string | null;
  onChange: (documentId: string | null) => void;
  id?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}) {
  const documents = useLoad(loadDocuments);
  const [uploading, setUploading] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  return (
    <div className="stack">
      <Loading state={documents}>
        {(list) => (
          <select
            id={id}
            value={value ?? ""}
            onChange={(event) => onChange(event.target.value || null)}
            {...aria}
          >
            <option value="">– kein Dokument –</option>
            {list.map((document) => (
              <option key={document.id} value={document.id}>
                {documentLabel(document)} ({documentKind(document)})
              </option>
            ))}
          </select>
        )}
      </Loading>
      <div className="row">
        <button
          type="button"
          className="link"
          onClick={() => setUploading((open) => !open)}
          aria-expanded={uploading}
        >
          {uploading ? "Hochladen schließen" : "Neues Dokument hochladen"}
        </button>
        {note ? <span className="muted">{note}</span> : null}
      </div>
      {uploading ? (
        <UploadControl
          onUploaded={({ document, existed }) => {
            documents.reload();
            onChange(document.id);
            setNote(
              existed
                ? "Bereits vorhanden, ausgewählt."
                : "Hochgeladen und ausgewählt.",
            );
            setUploading(false);
          }}
        />
      ) : null}
    </div>
  );
}

/** The documents of one target: rolled up from its children for a property or a unit,
 * otherwise only those linked directly (each then has the one direct link as its `via`). */
async function loadLinked(
  targetType: LinkTarget,
  targetId: string,
): Promise<RelatedDocument[]> {
  if (targetType === "property") {
    return ok(
      api.GET("/api/properties/{property_id}/documents", {
        params: { path: { property_id: targetId } },
      }),
    );
  }
  if (targetType === "unit") {
    return ok(
      api.GET("/api/units/{unit_id}/documents", {
        params: { path: { unit_id: targetId } },
      }),
    );
  }
  const documents = await ok(
    api.GET("/api/documents", {
      params: { query: { target_type: targetType, target_id: targetId } },
    }),
  );
  return documents.map((document) => ({
    document,
    via: [{ target_type: targetType, target_id: targetId }],
  }));
}

/** The pages of the children a document can be rolled up through. */
const VIA_HREF: Partial<Record<LinkTarget, (id: string) => string>> = {
  unit: (id) => `/units/${id}`,
  tenancy: (id) => `/tenancies/${id}`,
  loan: (id) => `/loans/${id}`,
};

function isDirect(
  ref: LinkTargetRef,
  targetType: LinkTarget,
  targetId: string,
): boolean {
  return ref.target_type === targetType && ref.target_id === targetId;
}

/** "direkt" for the page's own link, else the child's kind, linking to its page. */
function ViaLinks({
  via,
  targetType,
  targetId,
}: {
  via: LinkTargetRef[];
  targetType: LinkTarget;
  targetId: string;
}) {
  return (
    <>
      {via.map((ref, index) => {
        const separator = index > 0 ? ", " : null;
        const key = `${ref.target_type}:${ref.target_id}`;
        if (isDirect(ref, targetType, targetId)) {
          return <span key={key}>{separator}direkt</span>;
        }
        const href = VIA_HREF[ref.target_type];
        return (
          <span key={key}>
            {separator}
            {href ? (
              <Link href={href(ref.target_id)}>
                {LINK_TARGET[ref.target_type]}
              </Link>
            ) : (
              LINK_TARGET[ref.target_type]
            )}
          </span>
        );
      })}
    </>
  );
}

/**
 * The documents linked to one entity, with link (pick or upload) and unlink (§4.7). On a
 * property or unit page the list also holds the documents of its children, with a "Verknüpft
 * über" column; only a direct link can be removed here, the others on the child's page.
 */
export function LinkedDocuments({
  targetType,
  targetId,
}: {
  targetType: LinkTarget;
  targetId: string;
}) {
  const load = useCallback(
    () => loadLinked(targetType, targetId),
    [targetType, targetId],
  );
  const rollsUp = targetType === "property" || targetType === "unit";
  const linked = useLoad(load);
  const pickerId = useId();
  const [picked, setPicked] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function link() {
    if (!picked) return;
    setBusy(true);
    setError(null);
    try {
      await ok(
        api.POST("/api/documents/{document_id}/links", {
          params: { path: { document_id: picked } },
          body: { target_type: targetType, target_id: targetId },
        }),
      );
      setPicked(null);
      linked.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  async function unlink(document: Document) {
    if (
      !window.confirm(
        `Verknüpfung mit „${documentLabel(document)}“ entfernen? Das Dokument bleibt im Archiv.`,
      )
    )
      return;
    setError(null);
    try {
      await ok(
        api.DELETE(
          "/api/documents/{document_id}/links/{target_type}/{target_id}",
          {
            params: {
              path: {
                document_id: document.id,
                target_type: targetType,
                target_id: targetId,
              },
            },
          },
        ),
      );
      linked.reload();
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <div className="stack">
      <Loading state={linked}>
        {(list) =>
          list.length === 0 ? (
            <p className="muted">Noch keine Dokumente verknüpft.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Dokument</th>
                    <th>Art</th>
                    <th>Eingang</th>
                    {rollsUp ? <th>Verknüpft über</th> : null}
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {list.map(({ document, via }) => (
                    <tr key={document.id}>
                      <td>
                        <Link href={`/documents/${document.id}`}>
                          {documentLabel(document)}
                        </Link>
                      </td>
                      <td>
                        <span className="tag">{documentKind(document)}</span>
                      </td>
                      <td>{formatDate(document.received_at)}</td>
                      {rollsUp ? (
                        <td>
                          <ViaLinks
                            via={via}
                            targetType={targetType}
                            targetId={targetId}
                          />
                        </td>
                      ) : null}
                      <td className="actions">
                        <a
                          href={`/api/documents/${document.id}/original?disposition=inline`}
                          target="_blank"
                          rel="noopener"
                        >
                          Öffnen
                        </a>
                        {via.some((ref) =>
                          isDirect(ref, targetType, targetId),
                        ) ? (
                          <>
                            {" "}
                            <button
                              type="button"
                              className="link"
                              onClick={() => void unlink(document)}
                            >
                              Verknüpfung entfernen
                            </button>
                          </>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Loading>
      <fieldset className="stack">
        <legend>Dokument verknüpfen</legend>
        <label htmlFor={pickerId}>Dokument</label>
        <DocumentPicker id={pickerId} value={picked} onChange={setPicked} />
        <div>
          <button
            type="button"
            onClick={() => void link()}
            disabled={!picked || busy}
          >
            {busy ? "Verknüpft …" : "Verknüpfen"}
          </button>
        </div>
        <ErrorNotice error={error} />
      </fieldset>
    </div>
  );
}

// A depot document in plain words (phase-6.md D177): what it is, what it means for the
// account (filled by code from the document's own figures), and what the app does with it.
// The same box on the document page and on its suggestion card, never both at once.
export function DocumentExplanationBox({ explanation }: { explanation: DocumentExplanation | null | undefined }) {
  if (!explanation) return null;
  return (
    <aside className="notice notice-info explanation" aria-label="Was ist dieses Dokument?">
      <p>
        <strong>Was ist das?</strong> {explanation.was}
      </p>
      {explanation.bedeutung ? (
        <p>
          <strong>Was es für das Konto heißt:</strong> {explanation.bedeutung}
        </p>
      ) : null}
      <p>
        <strong>Was die App damit tut:</strong> {explanation.aktion}
      </p>
    </aside>
  );
}
