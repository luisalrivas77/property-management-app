"use client";

// The Werbungskosten of the Anlage N tab block by block (phase-4b.md §5.2–5.6): one row per
// block (Arbeitsweg, Homeoffice, Arbeitszimmer, Telekommunikation) and one per work item,
// association, course and other expense, each with the amount the rules computed
// (AnlageNDraft.components) and a sentence built from the values the rule used. Every euro
// figure is the API's (principle 1): the sentences only put its numbers into words.
import type { ReactNode } from "react";

import type { TaxSourceRef } from "@/api/schemas";
import type { components } from "@/api/types";
import { useFormat, useT, type Formatter, type Translation } from "@/i18n";
import { blocks as messages } from "@/i18n/returnInputs";

import { formatShare } from "./format";
import { EQUIPMENT_CLASS } from "./labels";
import { SourcesButton } from "./tax";

type Components = components["schemas"]["WerbungskostenComponents"];
type Commute = components["schemas"]["CommuteComponent"];
type Route = components["schemas"]["CommuteRouteDetail"];
type SumComponent = components["schemas"]["SumComponent"];
type T = Translation<(typeof messages)["de"]>;

const isZero = (value: string | null | undefined) => value == null || /^-?0*(\.0*)?$/.test(value);

function refs(ids: string[]): TaxSourceRef[] {
  return ids.map((id) => ({ kind: "return_input", id }));
}

function Row({
  label,
  amount,
  explain,
  sources,
}: {
  label: string;
  amount: string;
  explain: ReactNode[];
  sources: TaxSourceRef[];
}) {
  const f = useFormat();
  return (
    <li className="tax-line">
      <div className="tax-line-main">
        <p className="tax-line-label">{label}</p>
        {explain.map((text, index) => (
          <p key={index} className="tax-line-sub">
            {text}
          </p>
        ))}
      </div>
      <div className="tax-line-amount">
        <span className="num">{f.money(amount)}</span>
        <SourcesButton refs={sources} title={label} />
      </div>
    </li>
  );
}

/** "100 Tage × 25 km × 0,38 €" for one part of a route; with several rate tiers (2025: 0,30 €
 * to km 20, 0,38 € from km 21) the tiers are named instead of one rate. */
function routePart(days: number, km: number, commute: Commute, t: T, f: Formatter): string {
  const rates = commute.rates ?? [];
  const [only] = rates;
  if (rates.length === 1 && only) return t.routeOne(days, km, f.money(only.rate));
  const tiers = rates.map((tier) => t.tier(tier.from_km, f.money(tier.rate))).join(", ");
  return t.routeTiers(days, km, tiers);
}

function routeText(route: Route, commute: Commute, t: T, f: Formatter): string[] {
  const parts: string[] = [];
  if (route.km_car > 0) parts.push(t.car(routePart(route.days, route.km_car, commute, t, f), f.money(route.pauschale_car)));
  if (route.km_rest > 0) {
    parts.push(t.rest(routePart(route.days, route.km_rest, commute, t, f), f.money(route.pauschale_rest)));
  }
  return [`${route.workplace_label}: ${parts.join("; ")}`];
}

function CommuteRow({ commute }: { commute: Commute }) {
  const t = useT(messages);
  const f = useFormat();
  const explain: string[] = (commute.routes ?? []).flatMap((route) => routeText(route, commute, t, f));
  if (commute.rest_capped) {
    explain.push(t.restCapped(f.money(commute.cap_non_car)));
  }
  if (commute.public_transport_used) {
    explain.push(t.publicTransport(f.money(commute.public_transport_costs)));
  }
  if (!isZero(commute.employer_offset)) {
    explain.push(t.employerOffset(f.money(commute.total), f.money(commute.employer_offset)));
  }
  return (
    <Row
      label={t.commute}
      amount={commute.amount}
      explain={explain}
      sources={refs((commute.routes ?? []).map((r) => r.input_id))}
    />
  );
}

function SumRows({ block, title, kind }: { block: SumComponent; title: string; kind: "dues" | "training" | "other" }) {
  const t = useT(messages);
  return (
    <>
      {(block.items ?? []).map((item) => {
        const notes: string[] = [];
        if (kind === "dues" && item.is_union) notes.push(t.union);
        if (kind === "other" && item.flat) notes.push(t.accountFlat);
        return (
          <Row key={item.input_id} label={`${title}: ${item.label}`} amount={item.amount} explain={notes} sources={refs([item.input_id])} />
        );
      })}
    </>
  );
}

/** One row per block and per item; nothing when the owner typed no Werbungskosten. */
export function BlockRows({ blocks, year }: { blocks: Components; year: number }) {
  const office = blocks.home_office;
  const room = blocks.work_room;
  const telecom = blocks.telecom;
  const t = useT(messages);
  const f = useFormat();
  return (
    <ul className="tax-line-list">
      {blocks.commute ? <CommuteRow commute={blocks.commute} /> : null}
      {office ? (
        <Row
          label={t.homeOffice}
          amount={office.amount}
          explain={[
            t.homeOfficeDays(office.days_other_workplace + office.days_no_other_workplace, f.money(office.day_rate), f.money(office.full)),
            ...(office.capped ? [t.homeOfficeCap(f.money(office.cap))] : []),
          ]}
          sources={refs([office.input_id])}
        />
      ) : null}
      {room ? (
        <Row
          label={t.workRoom}
          amount={room.amount}
          explain={[
            room.method === "flat"
              ? t.workRoomFlat(room.months, f.money(room.flat), f.money(room.actual))
              : t.workRoomActual(room.months, f.money(room.actual), f.money(room.flat)),
            room.prefer ? t.workRoomPrefer : t.workRoomHigher,
          ]}
          sources={refs([room.input_id])}
        />
      ) : null}
      {(blocks.equipment?.items ?? []).map((item) => (
        <Row
          key={item.input_id}
          label={t.equipment(item.label)}
          amount={item.amount}
          explain={[
            t.equipmentBase(
              EQUIPMENT_CLASS[item.item_class],
              item.bought_year,
              f.money(item.gross),
              formatShare(item.professional_share),
              f.money(item.base),
            ),
            item.method === "full"
              ? item.bought_year === year
                ? t.equipmentFullThisYear
                : t.equipmentFullEarlier(item.bought_year, year)
              : t.equipmentSpread(item.life_months ?? "?", item.months_in_year, year, Boolean(item.disposed)),
          ]}
          sources={refs([item.input_id])}
        />
      ))}
      {telecom ? (
        <Row
          label={t.telecom}
          amount={telecom.amount}
          explain={[
            t.telecomLine(
              f.percent(telecom.share),
              f.money(telecom.bills_year),
              f.money(telecom.share_amount),
              f.money(telecom.cap_monthly),
              telecom.months,
              f.money(telecom.cap),
            ),
            ...(isZero(telecom.employer_reimbursed) ? [] : [t.telecomReimbursed(f.money(telecom.employer_reimbursed))]),
          ]}
          sources={refs([telecom.input_id])}
        />
      ) : null}
      {blocks.dues ? <SumRows block={blocks.dues} title={t.dues} kind="dues" /> : null}
      {blocks.training ? <SumRows block={blocks.training} title={t.training} kind="training" /> : null}
      {blocks.other ? <SumRows block={blocks.other} title={t.other} kind="other" /> : null}
    </ul>
  );
}

/** Whether the owner has any Werbungskosten block. */
export function hasBlocks(blocks: Components | undefined): blocks is Components {
  return blocks != null && Object.values(blocks).some((block) => block != null);
}
