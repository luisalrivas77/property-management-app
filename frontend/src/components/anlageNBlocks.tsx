"use client";

// The Werbungskosten of the Anlage N tab block by block (phase-4b.md §5.2–5.6): one row per
// block (Arbeitsweg, Homeoffice, Arbeitszimmer, Telekommunikation) and one per work item,
// association, course and other expense, each with the amount the rules computed
// (AnlageNDraft.components) and a sentence built from the values the rule used. Every euro
// figure is the API's (principle 1): the sentences only put its numbers into words.
import type { ReactNode } from "react";

import type { TaxSourceRef } from "@/api/schemas";
import type { components } from "@/api/types";

import { formatMoney, formatPercent, formatShare } from "./format";
import { EQUIPMENT_CLASS } from "./labels";
import { SourcesButton } from "./tax";

type Components = components["schemas"]["WerbungskostenComponents"];
type Commute = components["schemas"]["CommuteComponent"];
type Route = components["schemas"]["CommuteRouteDetail"];
type SumComponent = components["schemas"]["SumComponent"];

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
        <span className="num">{formatMoney(amount)}</span>
        <SourcesButton refs={sources} title={label} />
      </div>
    </li>
  );
}

/** "100 Tage × 25 km × 0,38 €" for one part of a route; with several rate tiers (2025: 0,30 €
 * to km 20, 0,38 € from km 21) the tiers are named instead of one rate. */
function routePart(days: number, km: number, commute: Commute): string {
  const rates = commute.rates ?? [];
  const [only] = rates;
  if (rates.length === 1 && only) return `${days} Tage × ${km} km × ${formatMoney(only.rate)}`;
  const tiers = rates.map((t) => `ab km ${t.from_km} ${formatMoney(t.rate)}`).join(", ");
  return `${days} Tage × ${km} km (je km ${tiers})`;
}

function routeText(route: Route, commute: Commute): string[] {
  const parts: string[] = [];
  if (route.km_car > 0) parts.push(`Auto: ${routePart(route.days, route.km_car, commute)} = ${formatMoney(route.pauschale_car)}`);
  if (route.km_rest > 0) {
    parts.push(`Bus, Bahn, Rad, Sammelfahrt: ${routePart(route.days, route.km_rest, commute)} = ${formatMoney(route.pauschale_rest)}`);
  }
  return [`${route.workplace_label}: ${parts.join("; ")}`];
}

function CommuteRow({ commute }: { commute: Commute }) {
  const explain: string[] = (commute.routes ?? []).flatMap((route) => routeText(route, commute));
  if (commute.rest_capped) {
    explain.push(`Der Teil ohne Auto ist auf ${formatMoney(commute.cap_non_car)} im Jahr begrenzt.`);
  }
  if (commute.public_transport_used) {
    explain.push(`Die Kosten für Bus und Bahn (${formatMoney(commute.public_transport_costs)}) sind höher als die Pauschale und zählen stattdessen.`);
  }
  if (!isZero(commute.employer_offset)) {
    explain.push(`${formatMoney(commute.total)} − ${formatMoney(commute.employer_offset)} Arbeitgeberzuschuss (steuerfrei oder pauschal versteuert)`);
  }
  return (
    <Row
      label="Arbeitsweg (Entfernungspauschale)"
      amount={commute.amount}
      explain={explain}
      sources={refs((commute.routes ?? []).map((r) => r.input_id))}
    />
  );
}

function SumRows({ block, title, kind }: { block: SumComponent; title: string; kind: "dues" | "training" | "other" }) {
  return (
    <>
      {(block.items ?? []).map((item) => {
        const notes: string[] = [];
        if (kind === "dues" && item.is_union) notes.push("Gewerkschaft: zählt zusätzlich zum Arbeitnehmer-Pauschbetrag");
        if (kind === "other" && item.flat) notes.push("Kontoführung ohne Beleg: die übliche Pauschale");
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
  return (
    <ul className="tax-line-list">
      {blocks.commute ? <CommuteRow commute={blocks.commute} /> : null}
      {office ? (
        <Row
          label="Homeoffice-Tagespauschale"
          amount={office.amount}
          explain={[
            `${office.days_other_workplace + office.days_no_other_workplace} Tage × ${formatMoney(office.day_rate)} = ${formatMoney(office.full)}`,
            ...(office.capped ? [`höchstens ${formatMoney(office.cap)} im Jahr`] : []),
          ]}
          sources={refs([office.input_id])}
        />
      ) : null}
      {room ? (
        <Row
          label="Häusliches Arbeitszimmer"
          amount={room.amount}
          explain={[
            room.method === "flat"
              ? `Jahrespauschale für ${room.months} ${room.months === 1 ? "Monat" : "Monate"}: ${formatMoney(room.flat)} (anteilige Kosten wären ${formatMoney(room.actual)})`
              : `Anteilige tatsächliche Kosten für ${room.months} ${room.months === 1 ? "Monat" : "Monate"}: ${formatMoney(room.actual)} (Pauschale wäre ${formatMoney(room.flat)})`,
            room.prefer ? "Nach Ihrer Wahl." : "Der höhere Betrag zählt.",
          ]}
          sources={refs([room.input_id])}
        />
      ) : null}
      {(blocks.equipment?.items ?? []).map((item) => (
        <Row
          key={item.input_id}
          label={`Arbeitsmittel: ${item.label}`}
          amount={item.amount}
          explain={[
            `${EQUIPMENT_CLASS[item.item_class]}, gekauft ${item.bought_year}: ${formatMoney(item.gross)} × ${formatShare(item.professional_share)} beruflich = ${formatMoney(item.base)}`,
            item.method === "full"
              ? item.bought_year === year
                ? "Im Kaufjahr voll abgesetzt."
                : `Im Kaufjahr ${item.bought_year} voll abgesetzt; ${year} zählt nichts mehr.`
              : `Abgeschrieben über ${item.life_months ?? "?"} Monate, davon ${item.months_in_year} in ${year}${item.disposed ? " (nicht mehr genutzt)" : ""}.`,
          ]}
          sources={refs([item.input_id])}
        />
      ))}
      {telecom ? (
        <Row
          label="Telekommunikation"
          amount={telecom.amount}
          explain={[
            `${formatPercent(telecom.share)} von ${formatMoney(telecom.bills_year)} = ${formatMoney(telecom.share_amount)}, höchstens ${formatMoney(telecom.cap_monthly)} × ${telecom.months} Monate = ${formatMoney(telecom.cap)}`,
            ...(isZero(telecom.employer_reimbursed) ? [] : [`− ${formatMoney(telecom.employer_reimbursed)} vom Arbeitgeber erstattet`]),
          ]}
          sources={refs([telecom.input_id])}
        />
      ) : null}
      {blocks.dues ? <SumRows block={blocks.dues} title="Beiträge" kind="dues" /> : null}
      {blocks.training ? <SumRows block={blocks.training} title="Fortbildung" kind="training" /> : null}
      {blocks.other ? <SumRows block={blocks.other} title="Sonstiges" kind="other" /> : null}
    </ul>
  );
}

/** Whether the owner has any Werbungskosten block. */
export function hasBlocks(blocks: Components | undefined): blocks is Components {
  return blocks != null && Object.values(blocks).some((block) => block != null);
}
