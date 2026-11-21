"use client";

// Dokument (§9.1): the original (open inline or download; never modified, principle 6), the
// pre-fill proposal to review ("Vorschlag", phase-2.md §6.4, components/proposals.tsx), the
// typing form (Typ, Untertyp, Titel, Datum, Steuerjahr, hand fields: a new extraction
// version when they change; over an AI reading they show what it read and a change is written
// into the reading's own kind, phase-2.md D37), the links (Zieltyp + picker, remove), and the
// retention result. One form at a time (D132): while a proposal is pending review, its typing
// card carries these same fields and the typing form is replaced by a one-line pointer; when
// the proposal proposes a link, the links are shown read-only (no form, no remove) the same way.
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import { ruleCodeText } from "@/api/errors";
import type { DocumentDetail, DocumentSubtype, DocumentType, HandFields, LinkTarget, ManualDocumentFields } from "@/api/schemas";
import { DocumentExplanationBox, documentLabel, NO_TAX_YEAR, StatementImportsNotice } from "@/components/documents";
import { loadDirectory, targets, type Target } from "@/components/entities";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { shortId } from "@/components/format";
import { blankToNull, decimalInput, toSignedMoneyString, toYear } from "@/components/input";
import { DOCUMENT_SUBTYPE, DOCUMENT_TYPE, LINK_TARGET, options, RETENTION_CLASS, RETENTION_HOLD } from "@/components/labels";
import { ProposalSection } from "@/components/proposals";
import { Breadcrumbs, Facts, Loading, Notice } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { formatter, useT } from "@/i18n";
import { documentPage } from "@/i18n/documentPage";
import { ErrorNotice } from "@/lib/ErrorNotice";

// Shown dates and amounts follow the chosen language, read at call time (phase 10 D284).
const formatDate = (v: string | null | undefined) => formatter().date(v);
const formatDateTime = (v: string | null | undefined) => formatter().dateTime(v);

export default function DocumentPage() {
  const t = useT(documentPage);
  const { id } = useParams<{ id: string }>();
  const load = useCallback(() => ok(api.GET("/api/documents/{document_id}", { params: { path: { document_id: id } } })), [id]);
  const detail = useLoad(load);
  const directory = useLoad(loadDirectory);
  // Lives here, not in TypingForm: the form remounts (its key) when the saved typing reloads.
  const [typingSaved, setTypingSaved] = useState(false);
  // D132: one form at a time. While a proposal is pending review, its typing card holds every
  // field this form offers, so the form is not shown; undefined while the proposals load.
  const [proposalPending, setProposalPending] = useState<boolean | undefined>(undefined);
  // The same for the links: while the pending proposal proposes a link, its card links.
  const [linksPending, setLinksPending] = useState<boolean | undefined>(undefined);

  return (
    <section>
      <Loading state={detail}>
        {(data) => (
          <>
            <Breadcrumbs items={[{ href: "/documents", label: t.documents }, { label: documentLabel(data.document) }]} />
            <h1>{documentLabel(data.document)}</h1>
            <Original detail={data} />
            {/* Phase 6 D176 (3), step 4: a statement's original names the account it went to. */}
            <StatementImportsNotice imports={data.statement_imports} />
            {/* D177: while a suggestion waits, its card carries the same box. */}
            {proposalPending === false ? <DocumentExplanationBox explanation={data.explanation} /> : null}
            <ProposalSection
              detail={data}
              directory={directory.data ?? null}
              onApplied={detail.reload}
              onPendingChange={setProposalPending}
              onPendingLinksChange={setLinksPending}
            />
            <h2 id="typisierung">{t.typing}</h2>
            {proposalPending ? (
              <p className="muted" role="note">
                {t.reviewProposalFirst}
              </p>
            ) : proposalPending === false ? (
              <>
                {typingSaved ? <Notice>{t.typingSaved}</Notice> : null}
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
              <p className="muted">{t.loading}</p>
            )}
            <h2>{t.links}</h2>
            <Links
              detail={data}
              targets={directory.data ? targets(directory.data) : null}
              onChanged={detail.reload}
              editable={linksPending === false}
              pointer={linksPending === true}
            />
            {directory.error ? <ErrorNotice error={directory.error} /> : null}
            <h2>{t.retention}</h2>
            <Retention detail={data} />
            <h2>{t.delete}</h2>
            <DeleteDocument detail={data} />
          </>
        )}
      </Loading>
    </section>
  );
}

function Original({ detail }: { detail: DocumentDetail }) {
  const t = useT(documentPage);
  const { document } = detail;
  const base = `/api/documents/${document.id}/original`;
  return (
    <div className="stack">
      <Facts
        rows={[
          [t.file, document.original_filename ?? "–"],
          [t.fileType, detail.media_type],
          [t.received, formatDateTime(document.received_at)],
          [t.checksum, <code key="sha">{shortId(document.file_sha256, 16)}…</code>],
        ]}
      />
      <div className="row">
        {/* Only PDF and images open in the browser (§4.7); anything else is always a download,
            so "öffnen" is offered only where it opens. */}
        {detail.opens_inline ? (
          <a href={`${base}?disposition=inline`} target="_blank" rel="noopener" className="button-link secondary">
            {t.openOriginal}
          </a>
        ) : null}
        <a href={`${base}?disposition=attachment`} className="button-link secondary">
          {t.downloadOriginal}
        </a>
      </div>
      {detail.opens_inline ? null : <p className="muted">{t.downloadOnly}</p>}
      <p className="muted">{t.originalUnchanged}</p>
    </div>
  );
}

// Phase 7 (D182 (3)): a wallet sync's stored original is typed by code only; the picker never
// offers it (a document already typed so keeps its value).
const CODE_WRITTEN_SUBTYPES: ReadonlySet<DocumentSubtype> = new Set<DocumentSubtype>(["chain_sync"]);

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
  const t = useT(documentPage);
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
    if (type === "") values.messages["typing.type"] = [t.chooseType];
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
    <form className="card stack" onSubmit={submit} noValidate aria-label={t.editTyping}>
      <div className="grid">
        <Field
          id="doc-type"
          label={t.type}
          description={t.typeDescription}
          errors={form.of("typing.type")}
        >
          <select value={type} onChange={(e) => setType(e.target.value as DocumentType | "")} required>
            <option value="">{t.choose}</option>
            {options(DOCUMENT_TYPE).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </Field>
        <Field
          id="doc-subtype"
          label={t.subtype}
          hint={t.subtypeHint}
          description={t.subtypeDescription}
          errors={form.of("typing.subtype")}
        >
          <select value={subtype} onChange={(e) => setSubtype(e.target.value as DocumentSubtype | "")}>
            <option value="">{t.noSubtype}</option>
            {options(DOCUMENT_SUBTYPE)
              .filter(([value]) => !CODE_WRITTEN_SUBTYPES.has(value) || value === subtype)
              .map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </Field>
        <Field
          id="doc-title"
          label={t.title}
          description={t.titleDescription}
          errors={form.of("typing.title")}
        >
          <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} autoComplete="off" />
        </Field>
        <Field
          id="doc-date"
          label={t.documentDate}
          description={t.documentDateDescription}
          errors={form.of("typing.document_date")}
        >
          <input type="date" value={documentDate} onChange={(e) => setDocumentDate(e.target.value)} />
        </Field>
        {NO_TAX_YEAR.has(subtype) && taxYear === "" ? null : (
        <Field
          id="doc-tax-year"
          label={t.taxYear}
          description={t.taxYearDescription}
          errors={form.of("typing.tax_year")}
        >
          <input type="text" inputMode="numeric" value={taxYear} onChange={(e) => setTaxYear(e.target.value)} maxLength={4} />
        </Field>
        )}
      </div>
      <fieldset className="stack">
        <legend>{t.handFields}</legend>
        {extraction && aiReading ? (
          <p className="muted">
            {available.size > 0
              ? t.aiReadingValues(extraction.source !== "ai", extraction.version, formatDateTime(extraction.created_at))
              : t.aiReadingNoHandFields(extraction.version)}
          </p>
        ) : extraction ? (
          <p className="muted">{t.manualVersion(extraction.version, formatDateTime(extraction.created_at))}</p>
        ) : null}
        {available.size > 0 ? (
          <div className="grid">
            {available.has("amount_total") ? (
              <Field
                id="doc-amount"
                label={isManagementContract ? t.monthlyFee : t.totalAmount}
                description={isManagementContract ? t.monthlyFeeDescription : t.totalAmountDescription}
                errors={form.of("fields.amount_total")}
              >
                <input type="text" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} autoComplete="off" />
              </Field>
            ) : null}
            {available.has("counterparty") ? (
              <Field
                id="doc-counterparty"
                label={t.counterparty}
                description={t.counterpartyDescription}
                errors={form.of("fields.counterparty")}
              >
                <input type="text" value={counterparty} onChange={(e) => setCounterparty(e.target.value)} autoComplete="off" />
              </Field>
            ) : null}
            {available.has("period_start") ? (
              <Field
                id="doc-period-start"
                label={t.periodStart}
                description={t.periodStartDescription}
                errors={form.of("fields.period_start")}
              >
                <input type="date" value={periodStart} onChange={(e) => setPeriodStart(e.target.value)} />
              </Field>
            ) : null}
            {available.has("period_end") ? (
              <Field
                id="doc-period-end"
                label={t.periodEnd}
                description={t.periodEndDescription}
                errors={form.of("fields.period_end")}
              >
                <input type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)} />
              </Field>
            ) : null}
            {available.has("reference") ? (
              <Field
                id="doc-reference"
                label={t.reference}
                description={t.referenceDescription}
                errors={form.of("fields.reference")}
              >
                <input type="text" value={reference} onChange={(e) => setReference(e.target.value)} autoComplete="off" />
              </Field>
            ) : null}
            {available.has("notes") ? (
              <Field
                id="doc-notes"
                label={t.notes}
                description={t.notesDescription}
                errors={form.of("fields.notes")}
              >
                <textarea value={notes} onChange={(e) => setNotes(e.target.value)} />
              </Field>
            ) : null}
          </div>
        ) : null}
      </fieldset>
      <Submit busy={busy}>{t.saveTyping}</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}

/** The document's links, and a form to add or remove one when `editable`. Not editable while the
 * pending proposal proposes a link (D132, `pointer`: one line points to its card) or while the
 * proposals load: the card is then the one place to link, so the list is read-only. */
function Links({
  detail,
  targets: all,
  onChanged,
  editable,
  pointer,
}: {
  detail: DocumentDetail;
  targets: Target[] | null;
  onChanged: () => void;
  editable: boolean;
  pointer: boolean;
}) {
  const t = useT(documentPage);
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
    if (!window.confirm(t.confirmUnlink)) return;
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
        <p className="muted">{t.notLinkedYet}</p>
      ) : (
        <ul>
          {detail.links.map((l) => {
            const target = describe(l.target_type, l.target_id);
            return (
              <li key={l.id}>
                {LINK_TARGET[l.target_type]}:{" "}
                {target ? (
                  <Link href={target.href}>{target.label}</Link>
                ) : l.target_type === "document" ? (
                  <Link href={`/documents/${l.target_id}`}>{t.openDocument}</Link>
                ) : (
                  <code>{shortId(l.target_id)}</code>
                )}{" "}
                <span className="muted">{t.since(formatDate(l.created_at))}</span>
                {editable ? (
                  <>
                    {" "}
                    <button type="button" className="link" onClick={() => void unlink(l.target_type, l.target_id)}>
                      {t.unlink}
                    </button>
                  </>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      {pointer ? (
        <p className="muted" role="note">
          {t.linkPointer}
        </p>
      ) : null}
      {editable ? (
        <form className="card stack" onSubmit={link} aria-label={t.addLink}>
          <div className="grid">
            <Field
              id="link-type"
              label={t.linkWith}
              description={t.linkWithDescription}
            >
              <select
                value={targetType}
                onChange={(e) => {
                  setTargetType(e.target.value as LinkTarget);
                  setTargetId("");
                }}
              >
                {/* A link to a bill is made by its to-do's upload (phase 3 D143), not here. */}
                {options(LINK_TARGET)
                  .filter(([value]) => value !== "document")
                  .map(([value, text]) => (
                    <option key={value} value={value}>
                      {text}
                    </option>
                  ))}
              </select>
            </Field>
            <Field
              id="link-target"
              label={LINK_TARGET[targetType]}
              description={t.linkTargetDescription}
            >
              <select value={targetId} onChange={(e) => setTargetId(e.target.value)} disabled={all === null}>
                <option value="">{all === null ? t.loading : choices.length === 0 ? t.nothingToChoose : t.choose}</option>
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
              {busy ? t.linking : t.link}
            </button>
          </div>
          <ErrorNotice error={error} />
        </form>
      ) : null}
    </div>
  );
}

function Retention({ detail }: { detail: DocumentDetail }) {
  const t = useT(documentPage);
  const { retention } = detail;
  return (
    <Facts
      rows={[
        [t.retentionClass, retention.retention_class ? RETENTION_CLASS[retention.retention_class] : t.afterTyping],
        [
          t.retainFor,
          retention.retain_until
            ? t.until(formatDate(retention.retain_until))
            : retention.hold
              ? RETENTION_HOLD[retention.hold]
              : "–",
        ],
      ]}
    />
  );
}

/** D193: delete a document nothing depends on; otherwise say what still depends on it. */
function DeleteDocument({ detail }: { detail: DocumentDetail }) {
  const t = useT(documentPage);
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const reasons = detail.delete_blocked_by.map((code) => ruleCodeText(code) ?? code);

  async function remove() {
    if (!window.confirm(t.confirmDelete)) return;
    setBusy(true);
    setError(null);
    try {
      await ok(api.DELETE("/api/documents/{document_id}", { params: { path: { document_id: detail.document.id } } }));
      router.push("/documents");
    } catch (caught) {
      setError(caught);
      setBusy(false);
    }
  }

  if (!detail.deletable) {
    return reasons.length === 1 ? (
      <p className="muted" role="note">
        {t.cannotDelete(reasons[0] ?? "")}
      </p>
    ) : (
      <div className="stack" role="note">
        <p className="muted">{t.cannotDeleteList}</p>
        <ul className="muted">
          {reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      </div>
    );
  }
  return (
    <div className="stack">
      <p className="muted">{t.deletable}</p>
      <div>
        <button type="button" className="danger" onClick={remove} disabled={busy}>
          {busy ? t.deleting : t.deleteDocument}
        </button>
      </div>
      {error ? <ErrorNotice error={error} /> : null}
    </div>
  );
}
