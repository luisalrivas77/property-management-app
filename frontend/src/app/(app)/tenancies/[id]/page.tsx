"use client";

// Mietverhältnis (§9.1): parties and terms, the Mietstaffel (superseded rows struck through,
// "korrigieren" per row, "Mietänderung"), the actions Bearbeiten, Mieter ändern, Kaution
// erhalten, Kündigung, Auszug, and the linked documents. The milestones (Vertragsdatum, Einzug, Kündigung, Auszug,
// each Kaution receipt) change only through their corrections, offered once there is
// something to correct; the page says when a date was corrected (from History, §9.4).
// Deposits received come from GET …/deposits. A notice or move-out cannot be withdrawn, only
// corrected.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import type { Property, Tenancy, Tenant, TimelineEntry, Unit } from "@/api/schemas";
import { DateCorrectionForm } from "@/components/corrections";
import { LinkedDocuments } from "@/components/documents";
import { tenantName } from "@/components/entities";
import { formatDate, formatMoney, todayIso } from "@/components/format";
import { correctedFrom, findMilestone } from "@/components/history";
import { DEPOSIT_FORM, RENT_MODEL, TENANCY_ROLE } from "@/components/labels";
import { ActionDialog, Breadcrumbs, CorrectedNote, Facts, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

import { DepositForm, EditTenancyForm, EndForm, NoticeForm, type NoticeValues } from "./actions";
import { PartiesForm } from "./PartiesForm";
import { RentSchedule } from "./RentSchedule";

interface TenancyData {
  tenancy: Tenancy;
  unit: Unit;
  property: Property;
  tenants: Tenant[];
}

export default function TenancyPage() {
  const { id } = useParams<{ id: string }>();
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
        {(found) => <TenancyDetail {...found} onChanged={data.reload} />}
      </Loading>
    </section>
  );
}

function TenancyDetail({ tenancy, unit, property, tenants, onChanged }: TenancyData & { onChanged: () => void }) {
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
  const history = useLoad(loadHistory);
  const received = useLoad(loadDeposits);
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
  }

  return (
    <>
      <Breadcrumbs
        items={[
          { href: `/properties/${property.id}`, label: property.label },
          { href: `/units/${unit.id}`, label: unit.label },
          { label: "Mietverhältnis" },
        ]}
      />
      <h1>
        Mietverhältnis {unit.label}
        {ended ? <span className="muted"> (beendet)</span> : null}
      </h1>
      <Facts
        rows={[
          [
            "Mieter",
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
            "Vertragsdatum",
            <>
              {formatDate(tenancy.signed_on)}
              {signedFrom ? <CorrectedNote original={formatDate(signedFrom)} /> : null}
            </>,
          ],
          [
            "Einzug",
            <>
              {formatDate(tenancy.start_date)}
              {moveInFrom ? <CorrectedNote original={formatDate(moveInFrom)} /> : null}
            </>,
          ],
          ["Befristet bis", tenancy.fixed_term_until ? formatDate(tenancy.fixed_term_until) : "unbefristet"],
          ["Mietmodell", RENT_MODEL[tenancy.rent_model]],
          ["Kündigung erhalten/ausgesprochen am", formatDate(tenancy.notice_given_on)],
          [
            "Auszug",
            <>
              {formatDate(tenancy.end_date)}
              {moveOutFrom ? <CorrectedNote original={formatDate(moveOutFrom)} /> : null}
            </>,
          ],
          [
            "Kaution",
            tenancy.deposit_form === "none"
              ? DEPOSIT_FORM.none
              : `${formatMoney(tenancy.deposit_amount)}, ${DEPOSIT_FORM[tenancy.deposit_form]}`,
          ],
          ["Kautionskonto", [tenancy.deposit_bank, tenancy.deposit_iban_masked].filter(Boolean).join(", ") || "–"],
          [
            "Kaution erhalten",
            deposits.length === 0 ? (
              "–"
            ) : (
              <>
                {deposits.map((deposit, index) => (
                  <span key={deposit.event_id}>
                    {index > 0 ? "; " : ""}
                    {formatMoney(deposit.amount)} am {formatDate(deposit.received_on)}{" "}
                    <ActionDialog label="korrigieren" title="Kaution erhalten korrigieren" buttonClass="link">
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
      <div className="row section">
        <ActionDialog label="Bearbeiten" title="Mietverhältnis bearbeiten">
          {(close) => (
            <EditTenancyForm
              tenancy={tenancy}
              onDone={() => {
                close();
                changed();
              }}
            />
          )}
        </ActionDialog>
        <ActionDialog label="Mieter ändern" title="Mieter ändern">
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
        <ActionDialog label="Einzug korrigieren" title="Einzugsdatum korrigieren">
          {(close) => (
            <DateCorrectionForm
              idPrefix="move-in"
              label="Richtiges Einzugsdatum"
              current={tenancy.start_date}
              onSubmit={(values) => ok(api.POST("/api/tenancies/{tenancy_id}/corrections/move-in", { ...path, body: { new_date: values.new_date } }))}
              onDone={() => {
                close();
                changed();
              }}
            />
          )}
        </ActionDialog>
        <ActionDialog label={tenancy.signed_on ? "Vertragsdatum korrigieren" : "Vertragsdatum nachtragen"} title="Vertragsdatum korrigieren">
          {(close) => (
            <DateCorrectionForm
              idPrefix="signed-on"
              label="Richtiges Vertragsdatum"
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
        <ActionDialog label="Kaution erhalten" title="Kaution erhalten">
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
        <ActionDialog label="Kündigung" title="Kündigung erfassen" disabled={tenancy.notice_given_on != null || tenancy.end_date != null}>
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
        {notice ? (
          <ActionDialog label="Kündigung korrigieren" title="Kündigung korrigieren">
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
        <ActionDialog label="Auszug" title="Auszug erfassen" disabled={tenancy.end_date != null}>
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
        {tenancy.end_date ? (
          <ActionDialog label="Auszug korrigieren" title="Auszugsdatum korrigieren">
            {(close) => (
              <DateCorrectionForm
                idPrefix="move-out"
                label="Richtiges Auszugsdatum"
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
      <h2>Mietstaffel</h2>
      {/* Keyed on the move-in: correcting it also moves the first rent row (§10.8 item 6), so
          the schedule reloads. */}
      <RentSchedule key={tenancy.start_date} tenancy={tenancy} onChanged={() => history.reload()} />
      <h2>Dokumente</h2>
      <LinkedDocuments targetType="tenancy" targetId={tenancy.id} />
    </>
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
