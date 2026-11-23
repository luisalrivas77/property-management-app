"use client";

/** A depot's parts as the net worth counts them (D292): its funds, its cash balance and the
 * money on its way to it, shown under the depot's total on the ETF and Übersicht tabs. */

import type { components } from "@/api/types";
import { useFormat, useT } from "@/i18n";
import { assets } from "@/i18n/assets";

type NetWorthLine = components["schemas"]["NetWorthLine"];

export function PortfolioParts({ label, funds, cash }: { label: string; funds: string; cash: NetWorthLine[] }) {
  const t = useT(assets);
  const f = useFormat();
  const lines = cash.filter((line) => line.value_eur != null);
  return (
    <dl className="portfolio-parts" aria-label={t.partsOf(label)}>
      <div>
        <dt>{t.partFunds}</dt>
        <dd className="num">{f.money(funds)}</dd>
      </div>
      {lines.map((line, index) => (
        <div key={`${line.in_transit ? "transit" : "cash"}-${index}`}>
          <dt>
            {line.in_transit ? t.partTransit : line.as_of ? t.partCash(f.date(line.as_of)) : t.kindCash}
            {line.in_transit && line.note ? <span className="muted"> · {line.note}</span> : null}
          </dt>
          <dd className="num">{f.money(line.value_eur)}</dd>
        </div>
      ))}
    </dl>
  );
}
