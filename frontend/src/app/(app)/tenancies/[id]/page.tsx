"use client";

// Mietverhältnis (§9.1): parties and terms, the Mietstaffel (superseded rows struck through,
// "korrigieren" per row, "Mietänderung"), the actions Bearbeiten, Mieter ändern, Kaution
// erhalten, Kündigung, Auszug, and the linked documents. The milestones (Vertragsdatum, Einzug, Kündigung, Auszug,
// each Kaution receipt) change only through their corrections, offered once there is
// something to correct; the page says when a date was corrected (from History, §9.4).
// Deposits received come from GET …/deposits. A notice or move-out cannot be withdrawn, only
// corrected. A flat_rate tenancy whose Pauschale can rise or must fall shows that to-do
// (phase-4.md D155 (4)); its link ?mietaenderung=pauschale opens „Mietänderung“ pre-filled.
// Phase 10 (mockup 25): „Bearbeiten“ slides in from the right; the Mietstaffel is rows with a
// detail panel; the other actions sit in two groups, „Ändern“ and „Korrigieren“.
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback } from "react";

import { api, ok } from "@/api/client";
import type { FlatRateCheck, Property, Tenancy, Tenant, TimelineEntry, Unit } from "@/api/schemas";
import { DateCorrectionForm } from "@/components/corrections";
import { DetailLayout } from "@/components/DetailPanel";
import { LinkedDocuments } from "@/components/documents";
import { tenantName } from "@/components/entities";
import { flatRateTodos, RENT_CHANGE_PARAM, RENT_CHANGE_PAUSCHALE } from "@/components/flatRate";
import { todayIso } from "@/components/format";
import { correctedFrom, findMilestone } from "@/components/history";
import { DEPOSIT_FORM, RENT_MODEL, TENANCY_ROLE } from "@/components/labels";
import { SheetDialog } from "@/components/SheetDialog";
import { OPERATING_COSTS_TERMS } from "@/components/taxInputs";
import { TodoList } from "@/components/todos";
import { ActionDialog, Breadcrumbs, CorrectedNote, Facts, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT } from "@/i18n";
import { tenancies } from "@/i18n/tenancies";

import { DepositForm, EditTenancyForm, EndForm, NoticeForm, type NoticeValues } from "./actions";
import { PartiesForm } from "./PartiesForm";
import { useRentSchedule } from "./RentSchedule";

import "@/components/pageRows.css";

interface TenancyData {
  tenancy: Tenancy;
  unit: Unit;
  property: Property;
  tenants: Tenant[];
}

export default function TenancyPage() {
  return (
    <Suspense fallback={<p className="muted">Lädt …</p>}>
      <TenancyScreen />
    </Suspense>
  );
}

function TenancyScreen() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const search = useSearchParams();
  const prefillPauschale = search.get(RENT_CHANGE_PARAM) === RENT_CHANGE_PAUSCHALE;
  const load = useCallback(async (): Promise<TenancyData> => {
    const tenancy = await ok(api.GET("/api/tenancies/{tenancy_id}", { params: { path: { tenancy_id: id } } }));
    const unit = await ok(api.GET("/api/units/{unit_id}", { params: { path: { unit_id: tenancy.unit_id } } }));
    const [property, tenants] = await Promise.all([
      ok(api.GET("/api/properties/{property_id}", { params: { path: { property_id: unit.property_id } } })),
      ok(api.GET("/api/tenants")),
    ]);
    return { tenancy, unit, property, tenants };
  }, [id]);
  const data = useLoad(load);

  return (
    <section>
      <Loading state={data}>
        {(found) => (
          <TenancyDetail
            {...found}
            onChanged={data.reload}
            prefillPauschale={prefillPauschale}
            onPrefillDone={() => router.replace(`/tenancies/${id}`, { scroll: false })}
          />
        )}
      </Loading>
    </section>
  );
}

function TenancyDetail({
  tenancy,
  unit,
  property,
  tenants,
  onChanged,
  prefillPauschale,
  onPrefillDone,
}: TenancyData & { onChanged: () => void; prefillPauschale: boolean; onPrefillDone: () => void }) {
  const t = useT(tenancies);
  const f = useFormat();
  const loadHistory = useCallback(
    () =>
      ok(
        api.GET("/api/properties/{property_id}/history", {
          params: { path: { property_id: property.id }, query: { unit_id: unit.id } },
        }),
      ),
    [property.id, unit.id],
  );
  const loadDeposits = useCallback(
    () => ok(api.GET("/api/tenancies/{tenancy_id}/deposits", { params: { path: { tenancy_id: tenancy.id } } })),
    [tenancy.id],
  );
  const flatRate = tenancy.operating_costs_terms === "flat_rate";
  // The rise test of the property, for this tenancy's to-do only; a failure adds nothing (the
  // card on Planung shows the check itself).
  const loadFlatRate = useCallback(
    (): Promise<FlatRateCheck | null> =>
      flatRate
        ? ok(api.GET("/api/properties/{property_id}/flat-rate-check", { params: { path: { property_id: property.id } } })).catch(
            () => null,
          )
        : Promise.resolve(null),
    [flatRate, property.id],
  );
  const history = useLoad(loadHistory);
  const received = useLoad(loadDeposits);
  const flatRateCheck = useLoad(loadFlatRate);
  const todos = flatRateTodos(flatRateCheck.data ?? null, { propertyId: property.id, onlyTenancyId: tenancy.id });
  const moveInFrom = correctedFrom(findMilestone(history.data, "tenancy.started", tenancy.id), tenancy.start_date);
  const signedFrom = tenancy.signed_on ? correctedFrom(findMilestone(history.data, "tenancy.created", tenancy.id), tenancy.signed_on) : null;
  const moveOutFrom = tenancy.end_date ? correctedFrom(findMilestone(history.data, "tenancy.ended", tenancy.id), tenancy.end_date) : null;
  const notice = noticeValues(findMilestone(history.data, "tenancy.notice_given", tenancy.id), tenancy.notice_given_on ?? null);
  const deposits = received.data ?? [];
  const ended = tenancy.end_date !== null && tenancy.end_date < todayIso();
  const byId = new Map(tenants.map((tenant) => [tenant.id, tenant]));
  const path = { params: { path: { tenancy_id: tenancy.id } } };

  function changed() {
    onChanged();
    history.reload();
    received.reload();
    flatRateCheck.reload();
  }

  const schedule = useRentSchedule({
    tenancy,
    onChanged: () => {
      history.reload();
      flatRateCheck.reload();
    },
    prefillPauschale,
    onPrefillDone,
  });

  return (
    <DetailLayout panel={schedule.panel}>
      <Breadcrumbs
        items={[
          { href: `/properties/${property.id}`, label: property.label },
          { href: `/units/${unit.id}`, label: unit.label },
          { label: t.crumb },
        ]}
      />
      <div className="page-head">
        <h1>
          {t.title(unit.label)}
          {ended ? <span className="muted">{t.ended}</span> : null}
        </h1>
        <span className="spacer" />
        <SheetDialog label={t.edit} title={t.editTitle}>
          {(close) => (
            <EditTenancyForm
              tenancy={tenancy}
              onDone={() => {
                close();
                changed();
              }}
            />
          )}
        </SheetDialog>
      </div>
      {todos.length > 0 ? <TodoList titleId="tenancy-todos" entries={todos} empty="" /> : null}
      <Facts
        rows={[
          [
            t.tenants,
            <>
              {tenancy.parties.map((party, index) => (
                <span key={party.tenant_id}>
                  {index > 0 ? ", " : ""}
                  <Link href={`/tenants/${party.tenant_id}`}>{tenantName(byId.get(party.tenant_id))}</Link> ({TENANCY_ROLE[party.role]})
                </span>
              ))}
            </>,
          ],
          [
            t.signedOn,
            <>
              {f.date(tenancy.signed_on)}
              {signedFrom ? <CorrectedNote original={f.date(signedFrom)} /> : null}
            </>,
          ],
          [
            t.moveIn,
            <>
              {f.date(tenancy.start_date)}
              {moveInFrom ? <CorrectedNote original={f.date(moveInFrom)} /> : null}
            </>,
          ],
          [t.fixedUntil, tenancy.fixed_term_until ? f.date(tenancy.fixed_term_until) : t.unlimited],
          [t.rentModel, RENT_MODEL[tenancy.rent_model]],
          [t.dueDay, t.dueDayValue(tenancy.rent_due_day)],
          [t.costs, OPERATING_COSTS_TERMS[tenancy.operating_costs_terms]],
          [t.noticeOn, f.date(tenancy.notice_given_on)],
          [
            t.moveOut,
            <>
              {f.date(tenancy.end_date)}
              {moveOutFrom ? <CorrectedNote original={f.date(moveOutFrom)} /> : null}
            </>,
          ],
          [
            t.deposit,
            tenancy.deposit_form === "none" ? DEPOSIT_FORM.none : `${f.money(tenancy.deposit_amount)}, ${DEPOSIT_FORM[tenancy.deposit_form]}`,
          ],
          [t.depositAccount, [tenancy.deposit_bank, tenancy.deposit_iban_masked].filter(Boolean).join(", ") || "–"],
          [
            t.depositReceived,
            deposits.length === 0 ? (
              "–"
            ) : (
              <>
                {deposits.map((deposit, index) => (
                  <span key={deposit.event_id}>
                    {index > 0 ? "; " : ""}
                    {t.receivedOn(f.money(deposit.amount), f.date(deposit.received_on))}{" "}
                    <ActionDialog label={t.correct} title={t.depositCorrectTitle} buttonClass="link">
                      {(close) => (
                        <DepositForm
                          tenancy={tenancy}
                          receipt={deposit}
                          onDone={() => {
                            close();
                            changed();
                          }}
                        />
                      )}
                    </ActionDialog>
                  </span>
                ))}
              </>
            ),
          ],
        ]}
      />
      <h2>{t.schedule}</h2>
      {schedule.body}
      <div className="action-group section" role="group" aria-labelledby="tenancy-change">
        <span id="tenancy-change" className="action-group-label">
          {t.groupChange}
        </span>
        <div className="row">
          <ActionDialog label={t.changeParties} title={t.changeParties}>
            {(close) => (
              <PartiesForm
                tenancy={tenancy}
                tenants={tenants}
                onDone={() => {
                  close();
                  changed();
                }}
              />
            )}
          </ActionDialog>
          <ActionDialog label={t.depositReceived} title={t.depositReceived}>
            {(close) => (
              <DepositForm
                tenancy={tenancy}
                onDone={() => {
                  close();
                  changed();
                }}
              />
            )}
          </ActionDialog>
          <ActionDialog label={t.notice} title={t.noticeTitle} disabled={tenancy.notice_given_on != null || tenancy.end_date != null}>
            {(close) => (
              <NoticeForm
                tenancy={tenancy}
                onDone={() => {
                  close();
                  changed();
                }}
              />
            )}
          </ActionDialog>
          <ActionDialog label={t.moveOut} title={t.moveOutTitle} disabled={tenancy.end_date != null}>
            {(close) => (
              <EndForm
                tenancy={tenancy}
                onDone={() => {
                  close();
                  changed();
                }}
              />
            )}
          </ActionDialog>
        </div>
      </div>
      <div className="action-group" role="group" aria-labelledby="tenancy-correct">
        <span id="tenancy-correct" className="action-group-label">
          {t.groupCorrect}
        </span>
        <div className="row">
          <ActionDialog label={t.moveInCorrect} title={t.moveInCorrectTitle}>
            {(close) => (
              <DateCorrectionForm
                idPrefix="move-in"
                label={t.moveInRight}
                current={tenancy.start_date}
                onSubmit={(values) => ok(api.POST("/api/tenancies/{tenancy_id}/corrections/move-in", { ...path, body: { new_date: values.new_date } }))}
                onDone={() => {
                  close();
                  changed();
                }}
              />
            )}
          </ActionDialog>
          <ActionDialog label={tenancy.signed_on ? t.signedCorrect : t.signedAdd} title={t.signedCorrect}>
            {(close) => (
              <DateCorrectionForm
                idPrefix="signed-on"
                label={t.signedRight}
                current={tenancy.signed_on ?? ""}
                onSubmit={(values) =>
                  ok(api.POST("/api/tenancies/{tenancy_id}/corrections/signed-on", { ...path, body: { new_date: values.new_date } }))
                }
                onDone={() => {
                  close();
                  changed();
                }}
              />
            )}
          </ActionDialog>
          {notice ? (
            <ActionDialog label={t.noticeCorrect} title={t.noticeCorrect}>
              {(close) => (
                <NoticeForm
                  tenancy={tenancy}
                  current={notice}
                  onDone={() => {
                    close();
                    changed();
                  }}
                />
              )}
            </ActionDialog>
          ) : null}
          {tenancy.end_date ? (
            <ActionDialog label={t.moveOutCorrect} title={t.moveOutCorrectTitle}>
              {(close) => (
                <DateCorrectionForm
                  idPrefix="move-out"
                  label={t.moveOutRight}
                  current={tenancy.end_date ?? ""}
                  onSubmit={(values) =>
                    ok(api.POST("/api/tenancies/{tenancy_id}/corrections/move-out", { ...path, body: { new_date: values.new_date } }))
                  }
                  onDone={() => {
                    close();
                    changed();
                  }}
                />
              )}
            </ActionDialog>
          ) : null}
        </div>
      </div>
      <h2>{t.documents}</h2>
      <LinkedDocuments targetType="tenancy" targetId={tenancy.id} />
    </DetailLayout>
  );
}

/** The notice in force, from History (who and to which date are only in its event), while
 * History and the tenancy agree on the date it was given. */
function noticeValues(entry: TimelineEntry | undefined, givenOn: string | null): NoticeValues | null {
  if (!entry || !givenOn || entry.effective_date !== givenOn) return null;
  const { given_by: givenBy, ends_on: endsOn } = entry.payload;
  if ((givenBy !== "tenant" && givenBy !== "landlord") || typeof endsOn !== "string") return null;
  return { given_by: givenBy, given_on: givenOn, ends_on: endsOn };
}
