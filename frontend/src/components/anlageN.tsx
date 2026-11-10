"use client";

// Steuern › one tax year › Anlage N (phase-4b.md §10, D99): one owner's employment draft as
// GET /api/owners/{id}/anlage-n computes it on every read, the owner's Eingaben (append-only
// return_input rows), and in the right column the open points, the documents and the
// hand-over. The owner is ?eigentuemer=… (the first one when absent). Every euro figure is
// the backend's (principle 1): this screen picks, names and formats, it adds nothing up.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useId } from "react";

import { api, ok } from "@/api/client";
import { ApiError, ruleCodes } from "@/api/errors";
import type { Owner } from "@/api/schemas";
import type { components } from "@/api/types";

import { ReadUpload } from "./documents";
import { formatDate, formatDecimal, formatMoney } from "./format";
import { ANLAGE_N_STATUS, RETURN_INPUT_KIND, WARNING_TEXT } from "./labels";
import { BlockRows, hasBlocks } from "./anlageNBlocks";
import { EvidenceFold } from "./returnInputEvidence";
import { InputsPanel, type ReturnInput } from "./returnInputs";
import {
  ReturnEstimateCard,
  useReturnEstimate,
  type ReturnEstimate,
} from "./returnEstimate";
import { ExportCard, SourcesButton, type ExportButton } from "./tax";
import { WarningActionView, WhatToDo, resolveAction } from "./todos";
import {
  questionnaireHref,
  TaxTabs,
  taxFormHref,
  type TaxYearState,
} from "./taxYear";
import { Loading } from "./ui";
import { useLoad, type Loaded } from "./useLoad";

type AnlageNDraft = components["schemas"]["AnlageNDraft"];
type DraftLine = components["schemas"]["DraftLine"];
type DraftWarning = components["schemas"]["DraftWarning"];
type Payslip = components["schemas"]["Payslip"];
type WageCertificate = components["schemas"]["WageCertificate"];

const FOOTER = "Ein Entwurf zum Prüfen, keine Steuerberatung.";

const loadOwners = () => ok(api.GET("/api/owners"));
// The years with an Anlage N rule set (a draft is made only under the year's own one).
const loadYears = () =>
  ok(api.GET("/api/tax/anlage-n/years")).then((r) => r.years);
// The year's questions with their German text (the open ones are listed under Offene Punkte).
const loadQuestionnaire = (year: number) =>
  ok(
    api.GET("/api/tax/questionnaire", {
      params: { query: { tax_year: year } },
    }),
  );

/** The draft, or why there is none: no pay source yet, or no rule set for the year. */
type DraftState =
  { draft: AnlageNDraft } | { missing: "no_pay" | "no_rule_set" };

export function AnlageNScreen({
  year,
  ownerParam,
  propertyParam,
}: {
  year: number;
  ownerParam: string | null;
  propertyParam: string | null;
}) {
  const owners = useLoad(loadOwners);
  return (
    <Loading state={owners}>
      {(list) => {
        const owner = list.find((o) => o.id === ownerParam) ?? list[0];
        const state: TaxYearState = {
          year,
          propertyId: propertyParam,
          ownerId: owner?.id ?? null,
        };
        if (!owner) {
          return (
            <>
              <div className="page-head tax-head">
                <div>
                  <p className="eyebrow">
                    Steuerjahr {year} · Anlage N (Entwurf)
                  </p>
                  <h1>Anlage N</h1>
                </div>
              </div>
              <TaxTabs current="n" state={state} />
              <p className="muted">
                Noch kein Eigentümer.{" "}
                <Link href="/owners">Eigentümer anlegen</Link>, dann entsteht
                hier der Entwurf der Anlage N.
              </p>
            </>
          );
        }
        return (
          <OwnerScreen
            key={`${owner.id}-${year}`}
            owner={owner}
            owners={list}
            state={state}
          />
        );
      }}
    </Loading>
  );
}

function OwnerScreen({
  owner,
  owners,
  state,
}: {
  owner: Owner;
  owners: Owner[];
  state: TaxYearState;
}) {
  const year = state.year;
  const router = useRouter();
  const ids = useId();
  const estimate = useReturnEstimate(year);
  const loadDraft = useCallback(async (): Promise<DraftState> => {
    try {
      const draft = await ok(
        api.GET("/api/owners/{owner_id}/anlage-n", {
          params: { path: { owner_id: owner.id }, query: { tax_year: year } },
        }),
      );
      return { draft };
    } catch (error) {
      const codes =
        error instanceof ApiError && error.body ? ruleCodes(error.body) : [];
      if (codes.includes("anlage_n.no_pay")) return { missing: "no_pay" };
      if (codes.includes("tax.no_rule_set")) return { missing: "no_rule_set" };
      throw error;
    }
  }, [owner.id, year]);
  const draft = useLoad(loadDraft);
  const loadRows = useCallback(
    () =>
      ok(
        api.GET("/api/return-inputs", {
          params: { query: { tax_year: year, owner_id: owner.id } },
        }),
      ),
    [owner.id, year],
  );
  const rows = useLoad(loadRows);
  const reloadEstimate = estimate.reload;
  const reloadDraft = draft.reload;
  const reloadRows = rows.reload;
  // A saved input changes the draft and the whole-return estimate: reload all three.
  const changed = useCallback(() => {
    reloadRows();
    reloadDraft();
    reloadEstimate();
  }, [reloadRows, reloadDraft, reloadEstimate]);

  const years = useLoad(loadYears);
  // The rule set years, plus the year shown when it has none (it then says so).
  const yearOptions =
    years.data && !years.data.includes(year)
      ? [...years.data, year].sort((a, b) => a - b)
      : (years.data ?? [year]);

  return (
    <>
      <div className="page-head tax-head">
        <div>
          <p className="eyebrow">Steuerjahr {year} · Anlage N (Entwurf)</p>
          <h1>{owner.full_name}</h1>
        </div>
        <span className="spacer" />
        <div className="row tax-pickers">
          <label htmlFor={`${ids}-year`} className="inline">
            Steuerjahr
            <select
              id={`${ids}-year`}
              value={year}
              onChange={(event) =>
                router.push(
                  taxFormHref("n", {
                    ...state,
                    year: Number(event.target.value),
                  }),
                )
              }
            >
              {yearOptions.map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      <ReturnEstimateCard year={year} state={estimate} />
      <TaxTabs current="n" state={state} />
      {owners.length > 1 ? (
        <nav aria-label="Eigentümer" className="row tax-owner-switch">
          {owners.map((o) => (
            <Link
              key={o.id}
              href={taxFormHref("n", { ...state, ownerId: o.id })}
              aria-current={o.id === owner.id ? "page" : undefined}
              scroll={false}
            >
              {o.full_name}
            </Link>
          ))}
        </nav>
      ) : null}
      <Loading state={draft}>
        {(result) => (
          <DraftView
            result={result}
            owner={owner}
            year={year}
            rows={rows}
            estimate={estimate.data ?? null}
            onChanged={changed}
          />
        )}
      </Loading>
      <p className="muted tax-footer">{FOOTER}</p>
    </>
  );
}

function DraftView({
  result,
  owner,
  year,
  rows,
  estimate,
  onChanged,
}: {
  result: DraftState;
  owner: Owner;
  year: number;
  rows: Loaded<ReturnInput[]>;
  estimate: ReturnEstimate | null;
  onChanged: () => void;
}) {
  const draft = "draft" in result ? result.draft : null;
  const list = rows.data ?? [];
  if ("missing" in result && result.missing === "no_rule_set") {
    return (
      <p className="muted">
        Für {year} gibt es noch kein Regelwerk der Anlage N.
      </p>
    );
  }
  const warnings = (draft?.warnings ?? []).filter(
    (w) => w.code !== "anlage_n.form_provisional",
  );
  return (
    <div className="stack">
      {draft && draft.form.form_status === "provisional" ? (
        <div className="notice notice-info" role="status">
          <p>
            Formular {year} noch nicht veröffentlicht; Zeilen nach dem Formular{" "}
            {draft.form.form_year} (Regelwerk {draft.form.version}). Die Beträge
            rechnet der Entwurf trotzdem nach den Regeln des Jahres.
          </p>
        </div>
      ) : null}
      <div className="tax-layout">
        <div className="stack tax-main">
          {draft ? (
            <>
              <PayCard draft={draft} rows={list} />
              <CostsCard draft={draft} />
            </>
          ) : (
            <div className="notice notice-info" role="status">
              <p>
                Für {owner.full_name} ist für {year} noch kein Arbeitslohn
                erfasst. Die Lohnsteuerbescheinigung oder eine Gehaltsabrechnung
                rechts unter Unterlagen hochladen (sie wird automatisch
                ausgelesen), oder den erwarteten Bruttolohn auf der Seite{" "}
                <Link href="/owners">Eigentümer</Link> eintragen.
              </p>
              <div className="row todo-actions">
                <ReadUpload
                  label="Lohnsteuerbescheinigung hochladen"
                  onUploaded={onChanged}
                />
                <Link href="/owners#einkommen">
                  Erwarteten Bruttolohn eintragen
                </Link>
              </div>
            </div>
          )}
          {rows.error != null && rows.data === undefined ? (
            <p className="notice notice-error">
              Die Eingaben konnten nicht geladen werden.
            </p>
          ) : (
            <InputsPanel
              ownerId={owner.id}
              year={year}
              rows={list}
              onChanged={onChanged}
            />
          )}
        </div>
        <aside
          className="stack tax-side"
          aria-label="Offene Punkte und Unterlagen"
        >
          <OpenPoints
            warnings={warnings}
            estimate={estimate}
            ownerId={owner.id}
            year={year}
            onChanged={onChanged}
          />
          <Documents rows={list} year={year} />
          <AnlageNHandOver
            ownerId={owner.id}
            year={year}
            ready={draft !== null}
          />
        </aside>
      </div>
    </div>
  );
}

// --- Übergabe: the owner's field sheet (phase 4b D104) ---------------------------------------

const SHEET_BUTTONS: ExportButton[] = [
  { kind: "anlage_n_sheet", label: "Feldblatt (PDF)" },
];

function AnlageNHandOver({
  ownerId,
  year,
  ready,
}: {
  ownerId: string;
  year: number;
  ready: boolean;
}) {
  const loadHistory = useCallback(
    () =>
      ok(
        api.GET("/api/owners/{owner_id}/anlage-n/exports", {
          params: { path: { owner_id: ownerId }, query: { tax_year: year } },
        }),
      ),
    [ownerId, year],
  );
  const queue = useCallback(
    async (kind: ExportButton["kind"]) => {
      const queued = await ok(
        api.POST("/api/owners/{owner_id}/anlage-n/exports", {
          params: { path: { owner_id: ownerId } },
          body: { tax_year: year, kind },
        }),
      );
      return queued.export_id;
    },
    [ownerId, year],
  );
  return (
    <ExportCard
      key={`${ownerId}-${year}`}
      titleId="tax-n-handover"
      taxYear={year}
      buttons={SHEET_BUTTONS}
      disabled={!ready}
      note={
        ready
          ? "Das Feldblatt sagt, was in welche Zeile der Anlage N in ELSTER gehört, und nennt am Ende, was von Hand einzutragen ist."
          : "Das Feldblatt gibt es, sobald Arbeitslohn erfasst ist."
      }
      loadHistory={loadHistory}
      queue={queue}
    />
  );
}

// --- the form lines -------------------------------------------------------------------------

function formRef(line: DraftLine): string | null {
  const zeile =
    line.zeile == null
      ? null
      : line.zeile_from != null && line.zeile_from !== line.zeile
        ? `Z. ${line.zeile_from}–${line.zeile}`
        : `Z. ${line.zeile}`;
  const kennzahl = line.kennzahl ? `Kz. ${line.kennzahl}` : null;
  return [zeile, kennzahl].filter(Boolean).join(" · ") || null;
}

/** Lines whose amount is a count (days, km), not euros. */
const COUNT_LINE =
  /^(commute\.route_\d+\.(days|km|km_car|km_collective|km_other)|home_office\.days_[a-z_]+)$/;

function LineItem({ line, label }: { line: DraftLine; label?: string }) {
  const count = COUNT_LINE.test(line.key);
  return (
    <li className="tax-line">
      <div className="tax-line-main">
        <p className="tax-line-label">
          {label ?? line.label}
          {line.provisional ? (
            <span className="badge badge-warn">vorläufig</span>
          ) : null}
        </p>
        {formRef(line) ? <p className="tax-line-ref">{formRef(line)}</p> : null}
      </div>
      <div className="tax-line-amount">
        <span className="num">
          {line.amount == null
            ? "–"
            : count
              ? formatDecimal(line.amount)
              : formatMoney(line.amount)}
        </span>
        <SourcesButton refs={line.sources ?? []} title={label ?? line.label} />
      </div>
    </li>
  );
}

const MONTHS = [
  "Januar",
  "Februar",
  "März",
  "April",
  "Mai",
  "Juni",
  "Juli",
  "August",
  "September",
  "Oktober",
  "November",
  "Dezember",
];

function monthLabel(iso: string): string {
  const match = /^(\d{4})-(\d{2})/.exec(iso);
  return match
    ? `${MONTHS[Number(match[2]) - 1] ?? match[2]} ${match[1]}`
    : iso;
}

/** Where the gross pay comes from: the certificate, the latest payslip's projection, or the
 * expected pay typed on the Eigentümer page. */
function grossSource(draft: AnlageNDraft, rows: ReturnInput[]): string {
  switch (draft.gross_source) {
    case "certificate":
      return "aus der Lohnsteuerbescheinigung";
    case "payslip": {
      const slips = rows
        .filter(
          (row) => row.kind === "payslip" && row.tax_year === draft.tax_year,
        )
        .map((row) => row.value as Payslip)
        .sort((a, b) => a.period_month.localeCompare(b.period_month));
      const latest = slips[slips.length - 1];
      if (!latest) return "Hochrechnung aus Gehaltsabrechnung";
      const left = latest.months_remaining_paid;
      return `Hochrechnung aus Gehaltsabrechnung ${monthLabel(latest.period_month)} · noch ${left} ${left === 1 ? "Monat" : "Monate"}`;
    }
    case "expected":
      return "aus dem erwarteten Bruttolohn (Eigentümer)";
    default:
      return "kein Arbeitslohn erfasst";
  }
}

function PayCard({
  draft,
  rows,
}: {
  draft: AnlageNDraft;
  rows: ReturnInput[];
}) {
  const lines = (draft.lines ?? []).filter((line) =>
    line.key.startsWith("pay."),
  );
  return (
    <section className="card tax-lines" aria-label="Arbeitslohn">
      <div className="row">
        <h2>Arbeitslohn</h2>
        <span className="badge">{ANLAGE_N_STATUS[draft.status]}</span>
      </div>
      <p className="muted tax-subline">{grossSource(draft, rows)}</p>
      <ul className="tax-line-list">
        {lines.map((line) => (
          <LineItem key={line.key} line={line} />
        ))}
      </ul>
      <div className="tax-line tax-total">
        <p className="tax-line-label">
          Einkünfte aus nichtselbständiger Arbeit
        </p>
        <span className="num">{formatMoney(draft.result)}</span>
      </div>
    </section>
  );
}

const TOTAL_KEYS = new Set(["wk.itemised", "wk.allowance", "wk.applied"]);

/** "Arbeitsweg 1: Tage" for the route lines (commute.route_1.days …). */
function routeLabel(line: DraftLine): string {
  const match = /^commute\.route_(\d+)\./.exec(line.key);
  return match ? `Arbeitsweg ${match[1]}: ${line.label}` : line.label;
}

/** Which of the two the draft applied, by comparing the API's own figures (no arithmetic). */
function comparison(draft: AnlageNDraft): string {
  const unionZero = /^-?0*(\.0*)?$/.test(draft.wk_union);
  if (!unionZero) {
    return `Gewerkschaftsbeiträge (${formatMoney(draft.wk_union)}) zählen zusätzlich zum Arbeitnehmer-Pauschbetrag.`;
  }
  if (draft.wk_itemised === draft.wk_allowance)
    return "Einzelnachweis und Pauschbetrag sind gleich hoch.";
  if (draft.wk_applied === draft.wk_allowance) {
    return "Der Pauschbetrag ist höher als der Einzelnachweis: angesetzt wird der Pauschbetrag.";
  }
  return "Der Einzelnachweis ist höher als der Pauschbetrag: angesetzt wird der Einzelnachweis.";
}

function CostsCard({ draft }: { draft: AnlageNDraft }) {
  const all = draft.lines ?? [];
  const items = all.filter(
    (line) => !line.key.startsWith("pay.") && !TOTAL_KEYS.has(line.key),
  );
  const totals = ["wk.itemised", "wk.allowance", "wk.applied"]
    .map((key) => all.find((line) => line.key === key))
    .filter((line): line is DraftLine => line !== undefined);
  const blocks = draft.components;
  return (
    <section className="card tax-lines" aria-label="Werbungskosten">
      <h2>Werbungskosten</h2>
      {hasBlocks(blocks) ? (
        <BlockRows blocks={blocks} year={draft.tax_year} />
      ) : (
        <p className="muted">
          Noch keine Werbungskosten eingetragen; es gilt der
          Arbeitnehmer-Pauschbetrag.
        </p>
      )}
      <ul className="tax-line-list">
        {totals.map((line) => (
          <LineItem key={line.key} line={line} />
        ))}
      </ul>
      <p className="tax-subline">{comparison(draft)}</p>
      {items.length > 0 ? (
        <details className="tax-form-lines">
          <summary>Formularzeilen</summary>
          <ul className="tax-line-list">
            {items.map((line) => (
              <LineItem key={line.key} line={line} label={routeLabel(line)} />
            ))}
          </ul>
          <p className="muted tax-subline">
            Die Entfernungs- und die Homeoffice-Pauschale stehen nur als Tage
            und Kilometer auf dem Formular; ihr Betrag ist im Einzelnachweis
            enthalten.
          </p>
        </details>
      ) : null}
    </section>
  );
}

// --- right column ---------------------------------------------------------------------------

function OpenPoints({
  warnings,
  estimate,
  ownerId,
  year,
  onChanged,
}: {
  warnings: DraftWarning[];
  estimate: ReturnEstimate | null;
  ownerId: string;
  year: number;
  onChanged: () => void;
}) {
  const ctx = { year, ownerId };
  const sorted = [
    ...warnings.filter((w) => w.severity === "warning"),
    ...warnings.filter((w) => w.severity === "info"),
  ];
  const load = useCallback(() => loadQuestionnaire(year), [year]);
  const view = useLoad(load);
  // This person's open slots, then the household's (no owner, no property); the property
  // questions belong to the Anlage V tab.
  const open = (
    view.data?.progress.open ??
    estimate?.questionnaire.open ??
    []
  ).filter(
    (q) =>
      q.owner_id === ownerId || (q.owner_id == null && q.property_id == null),
  );
  const text = new Map(
    (view.data?.questions ?? []).map((q) => [q.key, q.question_de]),
  );
  return (
    <section className="card tax-open" aria-labelledby="tax-n-open">
      <h2 id="tax-n-open">Offene Punkte</h2>
      {sorted.length === 0 && open.length === 0 ? (
        <p className="muted">Keine offenen Punkte.</p>
      ) : (
        <ul className="tax-open-list">
          {open.map((q) => (
            <li
              key={`${q.key}-${q.owner_id ?? "household"}`}
              className="tax-open-item tax-open-info"
            >
              <span className="badge badge-info">
                {q.owner_id == null ? "Frage Haushalt" : "Frage"}
              </span>
              <p>{text.get(q.key) ?? "Jahresfrage offen"}</p>
              <p className="tax-open-actions">
                <Link
                  href={questionnaireHref(year, {
                    key: q.key,
                    ownerId: q.owner_id,
                  })}
                >
                  Beantworten
                </Link>
              </p>
            </li>
          ))}
          {sorted.map((warning, index) => (
            <li
              key={`${warning.code}-${index}`}
              className={`tax-open-item tax-open-${warning.severity}`}
            >
              <span
                className={
                  warning.severity === "warning"
                    ? "badge badge-warn"
                    : "badge badge-info"
                }
              >
                {warning.severity === "warning" ? "Prüfen" : "Hinweis"}
              </span>
              <p>{WARNING_TEXT[warning.code](warning.params ?? {})}</p>
              <WhatToDo warning={warning} />
              {resolveAction(warning, ctx) || (warning.refs ?? []).length > 0 ? (
                <div className="row tax-open-actions">
                  <WarningActionView
                    warning={warning}
                    ctx={ctx}
                    onChanged={onChanged}
                  />
                  {(warning.refs ?? []).length > 0 ? (
                    <SourcesButton
                      refs={warning.refs ?? []}
                      title="Offener Punkt"
                    />
                  ) : null}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** "2026-03" for every month from the first to the last payslip of one employer. */
function monthRange(first: string, last: string): string[] {
  const out: string[] = [];
  let y = Number(first.slice(0, 4));
  let m = Number(first.slice(5, 7));
  const endY = Number(last.slice(0, 4));
  const endM = Number(last.slice(5, 7));
  while (y < endY || (y === endY && m <= endM)) {
    out.push(`${y}-${String(m).padStart(2, "0")}`);
    m += 1;
    if (m > 12) {
      m = 1;
      y += 1;
    }
  }
  return out;
}

/** Unterlagen: the certificate per employer, the payslips per month (a missing month marked),
 * and the receipts linked to the owner's inputs. */
function Documents({ rows, year }: { rows: ReturnInput[]; year: number }) {
  const certificates = rows.filter(
    (row) => row.kind === "wage_certificate" && row.tax_year === year,
  );
  const slips = rows.filter(
    (row) => row.kind === "payslip" && row.tax_year === year,
  );
  const byEmployer = new Map<string, Map<string, ReturnInput>>();
  for (const row of slips) {
    const value = row.value as Payslip;
    const months =
      byEmployer.get(value.employer) ?? new Map<string, ReturnInput>();
    months.set(value.period_month.slice(0, 7), row);
    byEmployer.set(value.employer, months);
  }
  const receipts = rows.filter(
    (row) =>
      row.document_id &&
      row.kind !== "wage_certificate" &&
      row.kind !== "payslip",
  );

  return (
    <section
      className="card stack tax-n-documents"
      aria-labelledby="tax-n-documents"
    >
      <h2 id="tax-n-documents">Unterlagen</h2>
      <ReadUpload label="Lohnsteuerbescheinigung oder Gehaltsabrechnung hochladen" />
      <div>
        <p className="tax-tracked-head">Lohnsteuerbescheinigung</p>
        {certificates.length === 0 ? (
          <p className="muted">
            Noch keine für {year}; sie kommt meist bis Ende Februar {year + 1}.
          </p>
        ) : (
          <ul className="tax-estimate-list">
            {certificates.map((row) => {
              const value = row.value as WageCertificate;
              return (
                <li key={row.id}>
                  {value.employer} · {formatDate(value.period_from)} bis{" "}
                  {formatDate(value.period_to)}
                  {row.document_id ? (
                    <>
                      {" · "}
                      <Link href={`/documents/${row.document_id}`}>
                        Dokument öffnen
                      </Link>
                      <EvidenceFold returnInputId={row.id} />
                    </>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <div>
        <p className="tax-tracked-head">Gehaltsabrechnungen</p>
        {byEmployer.size === 0 ? (
          <p className="muted">Noch keine für {year}.</p>
        ) : (
          [...byEmployer.entries()].map(([employer, months]) => {
            const keys = [...months.keys()].sort();
            const range = monthRange(
              keys[0] ?? "",
              keys[keys.length - 1] ?? "",
            );
            return (
              <div key={employer}>
                <p className="tax-subline">{employer}</p>
                <ul className="tax-estimate-list tax-payslip-months">
                  {range.map((month) => {
                    const row = months.get(month);
                    return row ? (
                      <li key={month}>
                        {monthLabel(month)}
                        {row.document_id ? (
                          <>
                            {" · "}
                            <Link href={`/documents/${row.document_id}`}>
                              Dokument öffnen
                            </Link>
                            <EvidenceFold returnInputId={row.id} />
                          </>
                        ) : null}
                      </li>
                    ) : (
                      <li key={month}>
                        {monthLabel(month)}{" "}
                        <span className="badge badge-warn">fehlt</span>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })
        )}
      </div>
      {receipts.length > 0 ? (
        <div>
          <p className="tax-tracked-head">Belege zu den Eingaben</p>
          <ul className="tax-estimate-list">
            {receipts.map((row) => (
              <li key={row.id}>
                {RETURN_INPUT_KIND[row.kind]} ·{" "}
                <Link href={`/documents/${row.document_id}`}>
                  Dokument öffnen
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
