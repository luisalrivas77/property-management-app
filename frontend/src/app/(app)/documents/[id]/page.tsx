"use client";

// Dokument (§9.1): the original (open inline or download; never modified, principle 6), the
// typing form (Typ, Untertyp, Titel, Datum, Steuerjahr, manual fields: a new extraction
// version when they change), the links (Zieltyp + picker, remove), and the retention result.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { DocumentDetail, DocumentSubtype, DocumentType, LinkTarget, ManualDocumentFields } from "@/api/schemas";
import { documentLabel } from "@/components/documents";
import { loadDirectory, targets, type Target } from "@/components/entities";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { formatDate, formatDateTime, shortId } from "@/components/format";
import { blankToNull, decimalInput, toSignedMoneyString, toYear } from "@/components/input";
import { DOCUMENT_SUBTYPE, DOCUMENT_TYPE, LINK_TARGET, options, RETENTION_CLASS, RETENTION_HOLD } from "@/components/labels";
import { Breadcrumbs, Facts, Loading, Notice } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { ErrorNotice } from "@/lib/ErrorNotice";

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const load = useCallback(() => ok(api.GET("/api/documents/{document_id}", { params: { path: { document_id: id } } })), [id]);
  const detail = useLoad(load);
  const directory = useLoad(loadDirectory);

  return (
    <section>
      <Loading state={detail}>
        {(data) => (
          <>
            <Breadcrumbs items={[{ href: "/documents", label: "Dokumente" }, { label: documentLabel(data.document) }]} />
            <h1>{documentLabel(data.document)}</h1>
            <Original detail={data} />
            <h2>Typisierung</h2>
            <TypingForm key={`${data.document.updated_at}-${data.extraction?.version ?? 0}`} detail={data} onSaved={detail.reload} />
            <h2>Verknüpfungen</h2>
            <Links detail={data} targets={directory.data ? targets(directory.data) : null} onChanged={detail.reload} />
            {directory.error ? <ErrorNotice error={directory.error} /> : null}
            <h2>Aufbewahrung</h2>
            <Retention detail={data} />
          </>
        )}
      </Loading>
    </section>
  );
}

function Original({ detail }: { detail: DocumentDetail }) {
  const { document } = detail;
  const base = `/api/documents/${document.id}/original`;
  return (
    <div className="stack">
      <Facts
        rows={[
          ["Datei", document.original_filename ?? "–"],
          ["Eingang", formatDateTime(document.received_at)],
          ["Prüfsumme (SHA-256)", <code key="sha">{shortId(document.file_sha256, 16)}…</code>],
        ]}
      />
      <div className="row">
        <a href={`${base}?disposition=inline`} target="_blank" rel="noopener" className="button-link secondary">
          Original öffnen
        </a>
        <a href={`${base}?disposition=attachment`} className="button-link secondary">
          Original herunterladen
        </a>
      </div>
      <p className="muted">Das Original bleibt unverändert; Typ, Felder und Verknüpfungen liegen als eigene Ebenen darüber.</p>
    </div>
  );
}

const TYPING_KEYS = [
  "typing",
  "typing.type",
  "typing.subtype",
  "typing.title",
  "typing.document_date",
  "typing.tax_year",
  "fields",
  "fields.amount_total",
  "fields.period_start",
  "fields.period_end",
  "fields.counterparty",
  "fields.reference",
  "fields.notes",
] as const;

function TypingForm({ detail, onSaved }: { detail: DocumentDetail; onSaved: () => void }) {
  const typing = detail.document.typing;
  const fields = detail.extraction?.fields;
  const [type, setType] = useState<DocumentType | "">(typing?.type ?? "");
  const [subtype, setSubtype] = useState<DocumentSubtype | "">(typing?.subtype ?? "");
  const [title, setTitle] = useState(typing?.title ?? "");
  const [documentDate, setDocumentDate] = useState(typing?.document_date ?? "");
  const [taxYear, setTaxYear] = useState(typing?.tax_year ? String(typing.tax_year) : "");
  const [amount, setAmount] = useState(decimalInput(fields?.amount_total));
  const [periodStart, setPeriodStart] = useState(fields?.period_start ?? "");
  const [periodEnd, setPeriodEnd] = useState(fields?.period_end ?? "");
  const [counterparty, setCounterparty] = useState(fields?.counterparty ?? "");
  const [reference, setReference] = useState(fields?.reference ?? "");
  const [notes, setNotes] = useState(fields?.notes ?? "");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const form = useFormErrors(TYPING_KEYS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    if (type === "") values.messages["typing.type"] = ["Bitte einen Typ wählen."];
    const year = values.optional("typing.tax_year", taxYear, toYear);
    const total = values.optional("fields.amount_total", amount, toSignedMoneyString);
    if (!values.ok || type === "" || year === undefined || total === undefined) return form.invalid(values.messages);
    const manual: ManualDocumentFields = {
      amount_total: total,
      period_start: periodStart || null,
      period_end: periodEnd || null,
      counterparty: blankToNull(counterparty),
      reference: blankToNull(reference),
      notes: blankToNull(notes),
    };
    // No extraction yet and nothing typed: send no fields (no empty version 1).
    const empty = Object.values(manual).every((value) => value === null);
    setBusy(true);
    setSaved(false);
    form.clear();
    try {
      await ok(
        api.PUT("/api/documents/{document_id}/typing", {
          params: { path: { document_id: detail.document.id } },
          body: {
            typing: { type, subtype: subtype || null, title: blankToNull(title), document_date: documentDate || null, tax_year: year },
            fields: empty && !detail.extraction ? null : manual,
          },
        }),
      );
      setSaved(true);
      onSaved();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit} noValidate>
      {saved ? <Notice>Gespeichert.</Notice> : null}
      <div className="grid">
        <Field id="doc-type" label="Typ" errors={form.of("typing.type")}>
          <select value={type} onChange={(e) => setType(e.target.value as DocumentType | "")} required>
            <option value="">– wählen –</option>
            {options(DOCUMENT_TYPE).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </Field>
        <Field id="doc-subtype" label="Untertyp (optional)" hint="Bestimmt die Aufbewahrung, z. B. Kaufvertrag." errors={form.of("typing.subtype")}>
          <select value={subtype} onChange={(e) => setSubtype(e.target.value as DocumentSubtype | "")}>
            <option value="">– keiner –</option>
            {options(DOCUMENT_SUBTYPE).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </Field>
        <Field id="doc-title" label="Titel (optional)" errors={form.of("typing.title")}>
          <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} autoComplete="off" />
        </Field>
        <Field id="doc-date" label="Datum des Dokuments (optional)" errors={form.of("typing.document_date")}>
          <input type="date" value={documentDate} onChange={(e) => setDocumentDate(e.target.value)} />
        </Field>
        <Field id="doc-tax-year" label="Steuerjahr (optional)" errors={form.of("typing.tax_year")}>
          <input type="text" inputMode="numeric" value={taxYear} onChange={(e) => setTaxYear(e.target.value)} maxLength={4} />
        </Field>
      </div>
      <fieldset className="stack">
        <legend>Felder (von Hand)</legend>
        {detail.extraction ? (
          <p className="muted">
            Version {detail.extraction.version} vom {formatDateTime(detail.extraction.created_at)}. Eine Änderung legt eine neue Version an.
          </p>
        ) : null}
        <div className="grid">
          <Field id="doc-amount" label="Betrag gesamt (€)" errors={form.of("fields.amount_total")}>
            <input type="text" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} autoComplete="off" />
          </Field>
          <Field id="doc-counterparty" label="Absender / Gegenpartei" errors={form.of("fields.counterparty")}>
            <input type="text" value={counterparty} onChange={(e) => setCounterparty(e.target.value)} autoComplete="off" />
          </Field>
          <Field id="doc-period-start" label="Zeitraum von" errors={form.of("fields.period_start")}>
            <input type="date" value={periodStart} onChange={(e) => setPeriodStart(e.target.value)} />
          </Field>
          <Field id="doc-period-end" label="Zeitraum bis" errors={form.of("fields.period_end")}>
            <input type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)} />
          </Field>
          <Field id="doc-reference" label="Referenz (Rechnungs-/Aktenzeichen)" errors={form.of("fields.reference")}>
            <input type="text" value={reference} onChange={(e) => setReference(e.target.value)} autoComplete="off" />
          </Field>
          <Field id="doc-notes" label="Notizen" errors={form.of("fields.notes")}>
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)} />
          </Field>
        </div>
      </fieldset>
      <Submit busy={busy}>Typisierung speichern</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}

function Links({ detail, targets: all, onChanged }: { detail: DocumentDetail; targets: Target[] | null; onChanged: () => void }) {
  const [targetType, setTargetType] = useState<LinkTarget>("property");
  const [targetId, setTargetId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const documentId = detail.document.id;

  function describe(type: LinkTarget, id: string): Target | undefined {
    return all?.find((target) => target.type === type && target.id === id);
  }

  async function link(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!targetId) return;
    setBusy(true);
    setError(null);
    try {
      await ok(
        api.POST("/api/documents/{document_id}/links", {
          params: { path: { document_id: documentId } },
          body: { target_type: targetType, target_id: targetId },
        }),
      );
      setTargetId("");
      onChanged();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  async function unlink(type: LinkTarget, id: string) {
    if (!window.confirm("Verknüpfung entfernen? Das Dokument bleibt im Archiv.")) return;
    setError(null);
    try {
      await ok(
        api.DELETE("/api/documents/{document_id}/links/{target_type}/{target_id}", {
          params: { path: { document_id: documentId, target_type: type, target_id: id } },
        }),
      );
      onChanged();
    } catch (caught) {
      setError(caught);
    }
  }

  const linked = new Set(detail.links.map((l) => `${l.target_type}:${l.target_id}`));
  const choices = (all ?? []).filter((target) => target.type === targetType && !linked.has(`${target.type}:${target.id}`));
  return (
    <div className="stack">
      {detail.links.length === 0 ? (
        <p className="muted">Noch nicht verknüpft. Ein Dokument belegt etwas: eine Immobilie, ein Mietverhältnis, ein Darlehen …</p>
      ) : (
        <ul>
          {detail.links.map((l) => {
            const target = describe(l.target_type, l.target_id);
            return (
              <li key={l.id}>
                {LINK_TARGET[l.target_type]}: {target ? <Link href={target.href}>{target.label}</Link> : <code>{shortId(l.target_id)}</code>}{" "}
                <span className="muted">(seit {formatDate(l.created_at)})</span>{" "}
                <button type="button" className="link" onClick={() => void unlink(l.target_type, l.target_id)}>
                  entfernen
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <form className="card stack" onSubmit={link}>
        <div className="grid">
          <Field id="link-type" label="Verknüpfen mit">
            <select
              value={targetType}
              onChange={(e) => {
                setTargetType(e.target.value as LinkTarget);
                setTargetId("");
              }}
            >
              {options(LINK_TARGET).map(([value, text]) => (
                <option key={value} value={value}>
                  {text}
                </option>
              ))}
            </select>
          </Field>
          <Field id="link-target" label={LINK_TARGET[targetType]}>
            <select value={targetId} onChange={(e) => setTargetId(e.target.value)} disabled={all === null}>
              <option value="">{all === null ? "Lädt …" : choices.length === 0 ? "– nichts zur Auswahl –" : "– wählen –"}</option>
              {choices.map((target) => (
                <option key={target.id} value={target.id}>
                  {target.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div>
          <button type="submit" disabled={!targetId || busy}>
            {busy ? "Verknüpft …" : "Verknüpfen"}
          </button>
        </div>
        <ErrorNotice error={error} />
      </form>
    </div>
  );
}

function Retention({ detail }: { detail: DocumentDetail }) {
  const { retention } = detail;
  return (
    <Facts
      rows={[
        ["Klasse", retention.retention_class ? RETENTION_CLASS[retention.retention_class] : "– (erst nach der Typisierung)"],
        [
          "Aufbewahren",
          retention.retain_until
            ? `bis ${formatDate(retention.retain_until)}`
            : retention.hold
              ? RETENTION_HOLD[retention.hold]
              : "–",
        ],
      ]}
    />
  );
}
