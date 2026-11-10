"use client";

// Dokument (§9.1): the original (open inline or download; never modified, principle 6), the
// pre-fill proposal to review ("Vorschlag", phase-2.md §6.4, components/proposals.tsx), the
// typing form (Typ, Untertyp, Titel, Datum, Steuerjahr, hand fields: a new extraction
// version when they change; over an AI reading they show what it read and a change is written
// into the reading's own kind, phase-2.md D37), the links (Zieltyp + picker, remove), and the
// retention result. One form at a time (D132): while a proposal is pending review, its typing
// card carries these same fields and the typing form is replaced by a one-line pointer.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { DocumentDetail, DocumentSubtype, DocumentType, HandFields, LinkTarget, ManualDocumentFields } from "@/api/schemas";
import { documentLabel, NO_TAX_YEAR } from "@/components/documents";
import { loadDirectory, targets, type Target } from "@/components/entities";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { formatDate, formatDateTime, shortId } from "@/components/format";
import { blankToNull, decimalInput, toSignedMoneyString, toYear } from "@/components/input";
import { DOCUMENT_SUBTYPE, DOCUMENT_TYPE, LINK_TARGET, options, RETENTION_CLASS, RETENTION_HOLD } from "@/components/labels";
import { ProposalSection } from "@/components/proposals";
import { Breadcrumbs, Facts, Loading, Notice } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { ErrorNotice } from "@/lib/ErrorNotice";

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const load = useCallback(() => ok(api.GET("/api/documents/{document_id}", { params: { path: { document_id: id } } })), [id]);
  const detail = useLoad(load);
  const directory = useLoad(loadDirectory);
  // Lives here, not in TypingForm: the form remounts (its key) when the saved typing reloads.
  const [typingSaved, setTypingSaved] = useState(false);
  // D132: one form at a time. While a proposal is pending review, its typing card holds every
  // field this form offers, so the form is not shown; undefined while the proposals load.
  const [proposalPending, setProposalPending] = useState<boolean | undefined>(undefined);

  return (
    <section>
      <Loading state={detail}>
        {(data) => (
          <>
            <Breadcrumbs items={[{ href: "/documents", label: "Dokumente" }, { label: documentLabel(data.document) }]} />
            <h1>{documentLabel(data.document)}</h1>
            <Original detail={data} />
            <ProposalSection detail={data} directory={directory.data ?? null} onApplied={detail.reload} onPendingChange={setProposalPending} />
            <h2 id="typisierung">Typisierung</h2>
            {proposalPending ? (
              <p className="muted" role="note">
                Prüfen Sie den Vorschlag oben; nach dem Übernehmen oder Verwerfen können Sie hier Werte ändern.
              </p>
            ) : proposalPending === false ? (
              <>
                {typingSaved ? <Notice>Typisierung gespeichert.</Notice> : null}
                <TypingForm
                  key={`${data.document.updated_at}-${data.extraction?.version ?? 0}`}
                  detail={data}
                  onSubmit={() => setTypingSaved(false)}
                  onSaved={() => {
                    setTypingSaved(true);
                    detail.reload();
                  }}
                />
              </>
            ) : (
              <p className="muted">Lädt …</p>
            )}
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
          ["Dateityp", detail.media_type],
          ["Eingang", formatDateTime(document.received_at)],
          ["Prüfsumme (SHA-256)", <code key="sha">{shortId(document.file_sha256, 16)}…</code>],
        ]}
      />
      <div className="row">
        {/* Only PDF and images open in the browser (§4.7); anything else is always a download,
            so "öffnen" is offered only where it opens. */}
        {detail.opens_inline ? (
          <a href={`${base}?disposition=inline`} target="_blank" rel="noopener" className="button-link secondary">
            Original öffnen
          </a>
        ) : null}
        <a href={`${base}?disposition=attachment`} className="button-link secondary">
          Original herunterladen
        </a>
      </div>
      {detail.opens_inline ? null : <p className="muted">Dieser Dateityp wird nicht im Browser angezeigt, nur heruntergeladen.</p>}
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

type HandField = HandFields["available"][number];

/** The hand inputs → the fields to send, from the input texts (undefined: a field is invalid). */
function handValues(
  values: ReturnType<typeof collector>,
  inputs: { amount: string; periodStart: string; periodEnd: string; counterparty: string; reference: string; notes: string },
): ManualDocumentFields | undefined {
  const total = values.optional("fields.amount_total", inputs.amount, toSignedMoneyString);
  if (total === undefined) return undefined;
  return {
    amount_total: total,
    period_start: inputs.periodStart || null,
    period_end: inputs.periodEnd || null,
    counterparty: blankToNull(inputs.counterparty),
    reference: blankToNull(inputs.reference),
    notes: blankToNull(inputs.notes),
  };
}

function TypingForm({ detail, onSubmit, onSaved }: { detail: DocumentDetail; onSubmit: () => void; onSaved: () => void }) {
  const typing = detail.document.typing;
  // The hand fields as the API reads them from the current extraction (D37): a manual one's own
  // fields, or what an AI reading holds in the fields its kind has (only those are shown).
  const extraction = detail.extraction;
  const fields = detail.hand_fields.values;
  const available = new Set<HandField>(detail.hand_fields.available);
  const aiReading = extraction !== null && extraction.kind !== "manual";
  const [initialInputs] = useState(() => ({
    amount: decimalInput(fields.amount_total),
    periodStart: fields.period_start ?? "",
    periodEnd: fields.period_end ?? "",
    counterparty: fields.counterparty ?? "",
    reference: fields.reference ?? "",
    notes: fields.notes ?? "",
  }));
  const [type, setType] = useState<DocumentType | "">(typing?.type ?? "");
  const [subtype, setSubtype] = useState<DocumentSubtype | "">(typing?.subtype ?? "");
  // A Verwaltervertrag's Betrag holds its monthly fee (read since 10 Nov 2026), so it is named so.
  const isManagementContract = subtype === "management_contract" || extraction?.kind === "management_contract";
  const [title, setTitle] = useState(typing?.title ?? "");
  const [documentDate, setDocumentDate] = useState(typing?.document_date ?? "");
  const [taxYear, setTaxYear] = useState(typing?.tax_year ? String(typing.tax_year) : "");
  const [amount, setAmount] = useState(initialInputs.amount);
  const [periodStart, setPeriodStart] = useState(initialInputs.periodStart);
  const [periodEnd, setPeriodEnd] = useState(initialInputs.periodEnd);
  const [counterparty, setCounterparty] = useState(initialInputs.counterparty);
  const [reference, setReference] = useState(initialInputs.reference);
  const [notes, setNotes] = useState(initialInputs.notes);
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(TYPING_KEYS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    if (type === "") values.messages["typing.type"] = ["Bitte einen Typ wählen."];
    const year = values.optional("typing.tax_year", taxYear, toYear);
    const manual = handValues(values, { amount, periodStart, periodEnd, counterparty, reference, notes });
    if (!values.ok || type === "" || year === undefined || manual === undefined) return form.invalid(values.messages);
    // Fields only when the hand section differs from what it showed, compared after the same
    // conversion: an untouched section writes no version (no empty version 1 either, and never
    // one over an AI reading, D37).
    const initial = handValues(collector(), initialInputs);
    const changed = JSON.stringify(manual) !== JSON.stringify(initial);
    setBusy(true);
    onSubmit();
    form.clear();
    try {
      await ok(
        api.PUT("/api/documents/{document_id}/typing", {
          params: { path: { document_id: detail.document.id } },
          body: {
            typing: { type, subtype: subtype || null, title: blankToNull(title), document_date: documentDate || null, tax_year: year },
            fields: changed ? manual : null,
            // The version the hand fields were shown from: a re-read since then is refused
            // (document.extraction_stale, D37) instead of writing stale values as typed ones.
            shown_version: extraction?.version ?? 0,
          },
        }),
      );
      onSaved();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit} noValidate aria-label="Typisierung bearbeiten">
      <div className="grid">
        <Field
          id="doc-type"
          label="Typ"
          description="Welche Art Dokument das ist, z. B. Rechnung, Kontoauszug oder Bescheid. Danach richtet sich, welche Werte gelesen werden und wofür das Dokument in der Unterlagen-Checkliste zählt."
          errors={form.of("typing.type")}
        >
          <select value={type} onChange={(e) => setType(e.target.value as DocumentType | "")} required>
            <option value="">– wählen –</option>
            {options(DOCUMENT_TYPE).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </Field>
        <Field
          id="doc-subtype"
          label="Untertyp (optional)"
          hint="Bestimmt die Aufbewahrung, z. B. Kaufvertrag."
          description="Genauer als der Typ, z. B. Kaufvertrag, Mietvertrag oder Grundsteuerbescheid. Danach richten sich die Aufbewahrungsfrist und die Unterlagen-Checkliste."
          errors={form.of("typing.subtype")}
        >
          <select value={subtype} onChange={(e) => setSubtype(e.target.value as DocumentSubtype | "")}>
            <option value="">– keiner –</option>
            {options(DOCUMENT_SUBTYPE).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </Field>
        <Field
          id="doc-title"
          label="Titel (optional)"
          description="Ein kurzer Name, unter dem Sie das Dokument wiederfinden."
          errors={form.of("typing.title")}
        >
          <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} autoComplete="off" />
        </Field>
        <Field
          id="doc-date"
          label="Datum des Dokuments (optional)"
          description="Das Datum, das auf dem Dokument steht, z. B. das Rechnungs- oder Bescheiddatum. Ohne Steuerjahr zählt die Aufbewahrungsfrist ab dem Jahr dieses Datums."
          errors={form.of("typing.document_date")}
        >
          <input type="date" value={documentDate} onChange={(e) => setDocumentDate(e.target.value)} />
        </Field>
        {NO_TAX_YEAR.has(subtype) && taxYear === "" ? null : (
        <Field
          id="doc-tax-year"
          label="Steuerjahr (optional)"
          description="Das Jahr, für das das Dokument gilt, z. B. 2026 bei der Zinsbescheinigung 2026. Die Unterlagen-Checkliste ordnet Jahresunterlagen danach zu, und die Aufbewahrungsfrist zählt ab diesem Jahr."
          errors={form.of("typing.tax_year")}
        >
          <input type="text" inputMode="numeric" value={taxYear} onChange={(e) => setTaxYear(e.target.value)} maxLength={4} />
        </Field>
        )}
      </div>
      <fieldset className="stack">
        <legend>Felder (von Hand)</legend>
        {extraction && aiReading ? (
          <p className="muted">
            {available.size > 0
              ? `Die Werte stammen aus der KI-Auslesung${extraction.source === "ai" ? "" : ", von Hand geändert"} (Version ${extraction.version} vom ${formatDateTime(extraction.created_at)}). Eine Änderung legt eine neue Version an; die übrigen gelesenen Felder bleiben erhalten.`
              : `Für diese Dokumentart gibt es keine Felder von Hand; die KI-Auslesung (Version ${extraction.version}) bleibt unverändert.`}
          </p>
        ) : extraction ? (
          <p className="muted">
            Version {extraction.version} vom {formatDateTime(extraction.created_at)}. Eine Änderung legt eine neue Version an.
          </p>
        ) : null}
        {available.size > 0 ? (
          <div className="grid">
            {available.has("amount_total") ? (
              <Field
                id="doc-amount"
                label={isManagementContract ? "Vergütung pro Monat (€)" : "Betrag gesamt (€)"}
                description={
                  isManagementContract
                    ? "Die Verwaltervergütung je Monat mit MwSt. Hausbuch rechnet sie aus dem Nettobetrag laut Vertrag; steht dort „zzgl. MwSt.“ ohne Satz, mit 19 %. Gebucht wird sie aus den Zahlungen im Kontoauszug, nicht aus dem Vertrag."
                    : "Der Gesamtbetrag laut Dokument, bei einer Rechnung brutto. Bei Rechnungen und Bescheiden sucht Hausbuch damit die passende Zahlung im Kontoauszug."
                }
                errors={form.of("fields.amount_total")}
              >
                <input type="text" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} autoComplete="off" />
              </Field>
            ) : null}
            {available.has("counterparty") ? (
              <Field
                id="doc-counterparty"
                label="Absender / Gegenpartei"
                description="Wer das Dokument ausgestellt hat oder an wen gezahlt wird, z. B. Handwerker, Stadtkasse oder Hausverwaltung."
                errors={form.of("fields.counterparty")}
              >
                <input type="text" value={counterparty} onChange={(e) => setCounterparty(e.target.value)} autoComplete="off" />
              </Field>
            ) : null}
            {available.has("period_start") ? (
              <Field
                id="doc-period-start"
                label="Zeitraum von"
                description="Der Beginn des Zeitraums, für den das Dokument gilt, z. B. des Abrechnungsjahrs einer Hausgeldabrechnung."
                errors={form.of("fields.period_start")}
              >
                <input type="date" value={periodStart} onChange={(e) => setPeriodStart(e.target.value)} />
              </Field>
            ) : null}
            {available.has("period_end") ? (
              <Field
                id="doc-period-end"
                label="Zeitraum bis"
                description="Das Ende dieses Zeitraums."
                errors={form.of("fields.period_end")}
              >
                <input type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)} />
              </Field>
            ) : null}
            {available.has("reference") ? (
              <Field
                id="doc-reference"
                label="Referenz (Rechnungs-/Aktenzeichen)"
                description="Die Rechnungsnummer oder das Aktenzeichen laut Dokument. Steht sie im Verwendungszweck einer Überweisung, hilft das, die Zahlung diesem Beleg zuzuordnen."
                errors={form.of("fields.reference")}
              >
                <input type="text" value={reference} onChange={(e) => setReference(e.target.value)} autoComplete="off" />
              </Field>
            ) : null}
            {available.has("notes") ? (
              <Field
                id="doc-notes"
                label="Notizen"
                description="Freier Text für Sie."
                errors={form.of("fields.notes")}
              >
                <textarea value={notes} onChange={(e) => setNotes(e.target.value)} />
              </Field>
            ) : null}
          </div>
        ) : null}
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
          <Field
            id="link-type"
            label="Verknüpfen mit"
            description="Worauf sich das Dokument bezieht, z. B. die Immobilie, ein Darlehen oder ein Mietverhältnis. Die Unterlagen-Checkliste zählt ein Dokument erst, wenn es mit dem passenden Eintrag verknüpft ist."
          >
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
          <Field
            id="link-target"
            label={LINK_TARGET[targetType]}
            description="Der Eintrag, mit dem das Dokument verknüpft wird."
          >
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
