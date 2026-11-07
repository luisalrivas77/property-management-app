"use client";

// Dokumente (§9.1): the vault. Upload by file picker or drag and drop; the same bytes a second
// time are not stored twice (200: "bereits vorhanden", D9). Each row opens the typing,
// linking and retention page.
import Link from "next/link";
import { useState } from "react";

import { api, ok } from "@/api/client";
import { documentKind, documentLabel, UploadControl, type Uploaded } from "@/components/documents";
import { formatDateTime } from "@/components/format";
import { Loading, Notice } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

const loadDocuments = () => ok(api.GET("/api/documents"));

export default function DocumentsPage() {
  const documents = useLoad(loadDocuments);
  const [uploads, setUploads] = useState<Uploaded[]>([]);

  return (
    <section>
      <div className="page-head">
        <h1>Dokumente</h1>
        <Link className="button-link" href="/capture">
          Dokument fotografieren
        </Link>
      </div>
      <UploadControl
        label="Dokumente hochladen"
        prefill
        onUploaded={(result) => {
          setUploads((current) => [result, ...current].slice(0, 5));
          documents.reload();
        }}
      />
      {uploads.map(({ document, existed, proposalId, aiUnavailable }) => (
        <Notice key={`${document.id}-${existed}-${proposalId ?? ""}`}>
          {existed ? "Bereits vorhanden: " : "Hochgeladen: "}
          <Link href={`/documents/${document.id}`}>{documentLabel(document)}</Link>
          {proposalId
            ? " – wird automatisch ausgelesen; den Vorschlag auf der Dokumentseite prüfen."
            : document.typing
              ? null
              : " – jetzt typisieren und verknüpfen."}
          {aiUnavailable ? " Automatisches Auslesen ist auf dem Server nicht eingeschaltet." : null}
        </Notice>
      ))}
      <h2>Archiv</h2>
      <Loading state={documents}>
        {(list) =>
          list.length === 0 ? (
            <p className="muted">Noch keine Dokumente.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Dokument</th>
                    <th>Art</th>
                    <th>Steuerjahr</th>
                    <th>Eingang</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((document) => (
                    <tr key={document.id}>
                      <td>
                        <Link href={`/documents/${document.id}`}>{documentLabel(document)}</Link>
                      </td>
                      <td>
                        <span className="tag">{documentKind(document)}</span>
                      </td>
                      <td>{document.typing?.tax_year ?? "–"}</td>
                      <td>{formatDateTime(document.received_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Loading>
    </section>
  );
}
