"use client";

// Documents shared by several screens (§9.1): upload, the document picker (Nebenkosten,
// Kaufpreisaufteilung, Mietstaffel; one component over GET /documents with an inline upload),
// and the "linked documents" panel of an entity page.
import Link from "next/link";
import { useCallback, useId, useRef, useState, type ChangeEvent } from "react";

import { api, ok } from "@/api/client";
import type { Document, LinkTarget, UploadBody } from "@/api/schemas";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { formatDate, shortId } from "./format";
import { DOCUMENT_TYPE } from "./labels";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";

/** Accepted by POST /documents (§4.7, Q8); the API decides, this only filters the picker. */
export const UPLOAD_ACCEPT =
  "application/pdf,image/jpeg,image/png,image/heic,image/webp,text/csv,text/plain,application/xml,text/xml,message/rfc822,.pdf,.jpg,.jpeg,.png,.heic,.webp,.csv,.txt,.xml,.eml";

export interface Uploaded {
  document: Document;
  /** The same bytes were already in the vault (200 instead of 201, D9). */
  existed: boolean;
}

/** POST /documents as multipart. The generated body type names `file` a string (OpenAPI's
 * `format: binary`); openapi-fetch sends a FormData body as is, so the FormData is passed
 * under that type. */
export async function uploadDocument(file: File): Promise<Uploaded> {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("source", "upload");
  const { data, response } = await api.POST("/api/documents", { body: form as unknown as UploadBody });
  return { document: data as Document, existed: response.status === 200 };
}

/** What a person recognises a document by: its title, file name, or hash prefix. */
export function documentLabel(document: Document): string {
  return document.typing?.title || document.original_filename || `Dokument ${shortId(document.file_sha256, 12)}`;
}

/** "Rechnung · 12.03.2024" or "nicht typisiert". */
export function documentKind(document: Document): string {
  if (!document.typing) return "nicht typisiert";
  const date = document.typing.document_date ? ` · ${formatDate(document.typing.document_date)}` : "";
  return `${DOCUMENT_TYPE[document.typing.type]}${date}`;
}

/** File input (plus drag and drop) that uploads each chosen file. */
export function UploadControl({ onUploaded, label = "Datei hochladen" }: { onUploaded: (result: Uploaded) => void; label?: string }) {
  const inputId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function upload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      for (const file of Array.from(files)) onUploaded(await uploadDocument(file));
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
        onChange={(event: ChangeEvent<HTMLInputElement>) => void upload(event.target.files)}
      />
      <p className="muted hint">PDF, Foto, CSV, XML oder E-Mail; Datei hierher ziehen oder auswählen.</p>
      <ErrorNotice error={error} />
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
          <select id={id} value={value ?? ""} onChange={(event) => onChange(event.target.value || null)} {...aria}>
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
        <button type="button" className="link" onClick={() => setUploading((open) => !open)} aria-expanded={uploading}>
          {uploading ? "Hochladen schließen" : "Neues Dokument hochladen"}
        </button>
        {note ? <span className="muted">{note}</span> : null}
      </div>
      {uploading ? (
        <UploadControl
          onUploaded={({ document, existed }) => {
            documents.reload();
            onChange(document.id);
            setNote(existed ? "Bereits vorhanden, ausgewählt." : "Hochgeladen und ausgewählt.");
            setUploading(false);
          }}
        />
      ) : null}
    </div>
  );
}

/** The documents linked to one entity, with link (pick or upload) and unlink (§4.7). */
export function LinkedDocuments({ targetType, targetId }: { targetType: LinkTarget; targetId: string }) {
  const load = useCallback(
    () => ok(api.GET("/api/documents", { params: { query: { target_type: targetType, target_id: targetId } } })),
    [targetType, targetId],
  );
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
    if (!window.confirm(`Verknüpfung mit „${documentLabel(document)}“ entfernen? Das Dokument bleibt im Archiv.`)) return;
    setError(null);
    try {
      await ok(
        api.DELETE("/api/documents/{document_id}/links/{target_type}/{target_id}", {
          params: { path: { document_id: document.id, target_type: targetType, target_id: targetId } },
        }),
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
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {list.map((document) => (
                    <tr key={document.id}>
                      <td>
                        <Link href={`/documents/${document.id}`}>{documentLabel(document)}</Link>
                      </td>
                      <td>{documentKind(document)}</td>
                      <td>{formatDate(document.received_at)}</td>
                      <td className="actions">
                        <a href={`/api/documents/${document.id}/original?disposition=inline`} target="_blank" rel="noopener">
                          Öffnen
                        </a>{" "}
                        <button type="button" className="link" onClick={() => void unlink(document)}>
                          Verknüpfung entfernen
                        </button>
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
          <button type="button" onClick={() => void link()} disabled={!picked || busy}>
            {busy ? "Verknüpft …" : "Verknüpfen"}
          </button>
        </div>
        <ErrorNotice error={error} />
      </fieldset>
    </div>
  );
}
