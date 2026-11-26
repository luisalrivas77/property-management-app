"use client";

// Steuern › one tax year › Eure Angaben (phase 10 D281, mockup 22; taxInputs.tsx is the property's
// tax inputs of Anlage V): every fact the household
// types or uploads that the forms read, in one place. Per owner the church tax and Bundesland
// (edited on Eigentümer); the household's pay inputs (income_input: the expected gross pay and
// deductions, a Bescheid's zvE, the typed rate, „Kein Arbeitslohn“, the pay documents) with
// „Kinder und Haushalt“ (components/income.tsx); one owner's Werbungskosten and
// Lohnersatzleistungen (return_input, components/returnInputs.tsx), the owner picked by
// ?eigentuemer=…; the Jahresfragen as a card into /tax/<year>/fragen; and per property the
// seller's renovation inside the purchase price (phase 3 D287, components/taxInputs.tsx). Both stores stay as
// they were (no migration); Anlage N and Anlage V only show the result. The inputs' own
// components keep their German texts (shared with /owners and the questionnaire).
import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

import { api, ok } from "@/api/client";
import type { Owner, Property } from "@/api/schemas";
import { useT } from "@/i18n";
import { tax as messages } from "@/i18n/tax";
import { taxInputsText } from "@/i18n/taxInputs";

import { todayIso } from "./format";
import { IncomeSection } from "./income";
import { STATE_CODE } from "./labels";
import { InputsPanel } from "./returnInputs";
import { ReturnEstimateCard, useReturnEstimate } from "./returnEstimate";
import { ReturnInputsSync, useReturnInputsSync } from "./returnInputsSync";
import { AngabenPoints } from "./taxGuide";
import { PRICE_RENOVATION_ANCHOR, PriceRenovationSection } from "./taxInputs";
import { questionnaireHref, TaxTabs, taxFormHref, type TaxYearState } from "./taxYear";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";

import "./tax.css";

const loadOwners = () => ok(api.GET("/api/owners"));
const loadYears = () => ok(api.GET("/api/tax/anlage-n/years")).then((r) => r.years);
const loadProperties = () => ok(api.GET("/api/properties"));

export function TaxInputsScreen({
  year,
  ownerParam,
  propertyParam,
}: {
  year: number;
  ownerParam: string | null;
  propertyParam: string | null;
}) {
  const t = useT(messages);
  const owners = useLoad(loadOwners);
  // One version for the guide's proposals and the typing blocks (components/returnInputsSync.ts).
  const [version, setVersion] = useState(0);
  const sync = useMemo(() => ({ version, bump: () => setVersion((n) => n + 1), active: true }), [version]);
  return (
    <ReturnInputsSync.Provider value={sync}>
    <Loading state={owners}>
      {(list) => {
        const owner = list.find((o) => o.id === ownerParam) ?? list[0] ?? null;
        const state: TaxYearState = { year, propertyId: propertyParam, ownerId: owner?.id ?? null };
        return (
          <>
            <TaxInputsHead state={state} />
            {/* The points of this step from the guide (phase 10 D290): what the rules found
                about the owners and the household, each with its button. */}
            <AngabenPoints year={year} />
            {owner ? (
              <TaxInputs key={`${owner.id}-${year}`} owner={owner} owners={list} state={state} />
            ) : (
              <p className="muted">
                {t.noOwner} <Link href="/owners#eigentuemer-anlegen">{t.createOwner}</Link>
              </p>
            )}
            <p className="muted tax-footer">{t.footer}</p>
          </>
        );
      }}
    </Loading>
    </ReturnInputsSync.Provider>
  );
}

/** The year chips: two years back to next year (the range the Eigentümer page offered, so next
 * year's expected pay and a carried Bescheid stay one tap away), the years with a rule set and the
 * year shown. */
function angabenYears(thisYear: number, ruleYears: readonly number[], shown: number): number[] {
  const years = new Set<number>([thisYear - 2, thisYear - 1, thisYear, thisYear + 1, ...ruleYears, shown]);
  return [...years].sort((a, b) => a - b);
}

/** „Steuern 2026“, the year chips and the tabs. */
export function TaxInputsHead({ state }: { state: TaxYearState }) {
  const t = useT(messages);
  const years = useLoad(loadYears);
  const choices = angabenYears(Number(todayIso().slice(0, 4)), years.data ?? [], state.year);
  return (
    <>
      <div className="page-head tax-head">
        <div>
          <p className="eyebrow">{t.inputsTab}</p>
          <h1>{t.title(state.year)}</h1>
        </div>
      </div>
      <nav aria-label={t.years} className="tax-year-chips">
        {choices.map((y) => (
          <Link
            key={y}
            href={taxFormHref("angaben", { ...state, year: y })}
            aria-current={y === state.year ? "page" : undefined}
            className="tax-chip"
            scroll={false}
          >
            {y}
          </Link>
        ))}
      </nav>
      <TaxTabs current="angaben" state={state} />
    </>
  );
}

function TaxInputs({ owner, owners, state }: { owner: Owner; owners: Owner[]; state: TaxYearState }) {
  const t = useT(messages);
  const year = state.year;
  const estimate = useReturnEstimate(year);
  // The booked benefits are a proposal of Eure Angaben's points (phase 10 §7.8 (a), D293),
  // saved there with „Übernehmen“; until then the to-do return.benefit_booked carried the
  // amount in this tab's URL into the typing form.
  const loadRows = useCallback(
    () => ok(api.GET("/api/return-inputs", { params: { query: { tax_year: year, owner_id: owner.id } } })),
    [owner.id, year],
  );
  const rows = useLoad(loadRows);
  const reloadRows = rows.reload;
  const reloadEstimate = estimate.reload;
  const reloadBoth = useCallback(() => {
    reloadRows();
    reloadEstimate();
  }, [reloadRows, reloadEstimate]);
  const changed = useReturnInputsSync(reloadBoth);

  const progress = estimate.data?.questionnaire ?? null;
  const percent = progress && progress.total > 0 ? Math.round((progress.answered / progress.total) * 100) : 0;

  return (
    <div className="stack">
      <p className="muted">{t.inputsIntro}</p>
      <ReturnEstimateCard year={year} state={estimate} />
      <div className="tax-inputs-grid">
        <div className="stack">
          <section className="card stack" aria-labelledby="tax-owners-title">
            <h2 id="tax-owners-title">{t.owners}</h2>
            <ul className="tax-owner-facts">
              {owners.map((o) => (
                <li key={o.id}>
                  <div className="tax-owner-name">
                    <span className="tax-owner-avatar" aria-hidden="true">
                      {o.full_name.trim().charAt(0).toUpperCase()}
                    </span>
                    <div>
                      <p>
                        <strong>{o.full_name}</strong>
                      </p>
                      <p className="muted">{o.tax_id_ref ? t.taxIdKept : t.taxIdMissing}</p>
                    </div>
                    <span className="spacer" />
                    <Link href="/owners">{t.edit}</Link>
                  </div>
                  <dl className="facts">
                    <div>
                      <dt>{t.churchTax}</dt>
                      <dd>{o.church_tax ? t.yes : t.no}</dd>
                    </div>
                    <div>
                      <dt>{t.state}</dt>
                      <dd>{o.tax_state ? STATE_CODE[o.tax_state] : "–"}</dd>
                    </div>
                  </dl>
                </li>
              ))}
            </ul>
          </section>
          <section className="card stack" aria-labelledby="arbeitslohn">
            <h2 id="arbeitslohn">{t.payTitle}</h2>
            <p className="muted">{t.payIntro}</p>
            <IncomeSection owners={owners} fixedYear={year} />
          </section>
        </div>
        <div className="stack">
          <section className="card stack tax-questions-card" aria-labelledby="tax-questions-title">
            <h2 id="tax-questions-title">{t.questionsTitle(year)}</h2>
            <p className="muted">{t.questionsIntro}</p>
            {progress ? (
              <>
                <div className="tax-progress" role="progressbar" aria-valuemin={0} aria-valuemax={progress.total} aria-valuenow={progress.answered}>
                  <span style={{ width: `${percent}%` }} />
                </div>
                <div className="row">
                  <span>{progress.total > 0 && progress.answered >= progress.total ? t.questionsDone : t.questionsProgress(progress.answered, progress.total)}</span>
                  <span className="spacer" />
                  <Link className="button-link" href={questionnaireHref(year)}>
                    {progress.answered > 0 ? t.questionsContinue : t.questionsStart}
                  </Link>
                </div>
              </>
            ) : (
              <Link className="button-link" href={questionnaireHref(year)}>
                {t.questionsStart}
              </Link>
            )}
          </section>
          <section className="stack" aria-label={t.perOwnerTitle}>
            {owners.length > 1 ? (
              <nav aria-label={t.pickOwner} className="row tax-owner-switch">
                {owners.map((o) => (
                  <Link
                    key={o.id}
                    href={taxFormHref("angaben", { ...state, ownerId: o.id })}
                    aria-current={o.id === owner.id ? "page" : undefined}
                    scroll={false}
                  >
                    {o.full_name}
                  </Link>
                ))}
              </nav>
            ) : null}
            <p className="muted">{t.perOwnerIntro(owner.full_name)}</p>
            <Loading state={rows}>
              {(list) => <InputsPanel ownerId={owner.id} year={year} rows={list} onChanged={changed} />}
            </Loading>
          </section>
        </div>
      </div>
      <PriceRenovationCards propertyId={state.propertyId} onChanged={reloadEstimate} />
    </div>
  );
}

/** The seller's renovation inside the purchase price (phase 3 D287), per property: the one the
 * URL names, else every property. The first card carries the anchor the to-dos open. */
function PriceRenovationCards({ propertyId, onChanged }: { propertyId: string | null; onChanged: () => void }) {
  const t = useT(taxInputsText);
  const properties = useLoad(loadProperties);
  return (
    <Loading state={properties}>
      {(list: Property[]) => {
        const named = list.filter((p) => p.id === propertyId);
        const shown = named.length > 0 ? named : list;
        return (
          <>
            {shown.map((p, index) => (
              <PriceRenovationSection
                key={p.id}
                propertyId={p.id}
                title={shown.length > 1 ? t.prTitleOf(p.label) : t.prTitle}
                anchor={index === 0 ? PRICE_RENOVATION_ANCHOR : `${PRICE_RENOVATION_ANCHOR}-${p.id}`}
                onChanged={onChanged}
              />
            ))}
          </>
        );
      }}
    </Loading>
  );
}
