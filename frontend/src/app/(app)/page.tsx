"use client";

// Übersicht as mockup 15-B2 (phase 10 D268), cut to what is relevant (§7.6, 22 Nov 2026): the
// tiles first (the net worth with the month's cash flow under it, a tile per kind of asset, the
// three key figures with their mark as a dot, Zu prüfen; components/overviewTiles.tsx), each a
// name, one number and at most one line, a tapped tile's summary in the detail panel with a
// button into its section; then "Was noch fehlt" (phase-3.md D136, components/overviewTodos.tsx,
// the first five and „Alle N anzeigen“) and the coming dates (phase-5.md §11, GET /reminders).
// The phase 9 block (the reserve, the limits, the key figures and the net worth over time;
// components/healthFigures.tsx) stays below them: §7.6 moves it to Planung, which does not show
// those parts yet. The property cards and the head buttons „Mieter“ and „Neue Immobilie“ left
// the page; Immobilien shows the same cards, its „Immobilie hinzufügen“ and the Mieter row. With
// no property yet the page keeps its hint, with the links to Eigentümer and „Immobilie anlegen“.
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api, ok } from "@/api/client";
import { useAuth } from "@/auth/AuthGate";
import { DetailLayout, useDetail } from "@/components/DetailPanel";
import { todayIso } from "@/components/format";
import { HealthSection } from "@/components/healthFigures";
import {
  OverviewTiles,
  TilePanel,
  isTileId,
  loadHealthOnly,
  loadInbox,
  loadPaidIn,
  loadPayback,
  loadMonthCashflow,
  loadNetWorthToday,
} from "@/components/overviewTiles";
import { OverviewTodos } from "@/components/overviewTodos";
import { NextDates } from "@/components/reminders";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT } from "@/i18n";
import { overview as catalogue } from "@/i18n/overview";

/** Once every tile's read has settled, or after this long at the latest, the page's other reads
 * start (below). */
const TILES_FIRST_MAX_MS = 20_000;

interface Gate {
  promise: Promise<void>;
  open: () => void;
}

function createGate(): Gate {
  let open = () => {};
  const promise = new Promise<void>((resolve) => {
    open = resolve;
  });
  return { promise, open };
}

/** The household's properties: the empty state and the tiles' panels read them. The to-do list
 * reads /properties at the same moment, so it is sent once (fetchShared). */
function loadProperties() {
  return ok(api.GET("/api/properties"));
}

export default function OverviewPage() {
  const { me } = useAuth();
  const t = useT(catalogue);
  const f = useFormat();
  const netWorth = useLoad(loadNetWorthToday);
  const health = useLoad(loadHealthOnly);
  const cashflow = useLoad(loadMonthCashflow);
  const inbox = useLoad(loadInbox);
  const paidIn = useLoad(loadPaidIn);
  const payback = useLoad(loadPayback);
  // Tiles first (21 Nov 2026): one load sent about 27 reads at once to a container with a quarter
  // of a CPU, so the tiles waited behind the to-do list's drafts. The to-do list, the coming
  // dates, the phase 9 block's other reads and the property list start once every tile's read
  // has settled, with or without an error, and after TILES_FIRST_MAX_MS at the latest, so none
  // waits forever. The property list and the to-do list then read /properties together, so it
  // is sent once (fetchShared).
  const [gate] = useState(createGate);
  const tilesSettled = [netWorth, health, cashflow, inbox, paidIn, payback].every((state) => !state.loading);
  useEffect(() => {
    if (tilesSettled) gate.open();
  }, [tilesSettled, gate]);
  useEffect(() => {
    const timer = window.setTimeout(gate.open, TILES_FIRST_MAX_MS);
    return () => window.clearTimeout(timer);
  }, [gate]);
  const loadReminders = useCallback(() => gate.promise.then(() => ok(api.GET("/api/reminders"))), [gate]);
  const reminders = useLoad(loadReminders);
  const loadList = useCallback(() => gate.promise.then(loadProperties), [gate]);
  const propertyList = useLoad(loadList);
  const detail = useDetail("kachel");
  const today = todayIso();
  const tiles = { netWorth, health, cashflow, inbox, paidIn, payback };
  const properties = (propertyList.data ?? []).map((property) => ({ id: property.id, label: property.label }));

  const panel = isTileId(detail.id) ? <TilePanel id={detail.id} data={tiles} detail={detail} properties={properties} /> : null;

  return (
    <DetailLayout panel={panel}>
      <section>
        <div className="page-head">
          <h1>{t.title}</h1>
          <span className="muted">
            {t.household(me.workspace.name)} · {t.asOf(f.date(today))}
          </span>
        </div>
        <OverviewTiles data={tiles} detail={detail} />
        <OverviewTodos reminders={reminders.data ?? []} onRemindersChanged={reminders.reload} after={gate.promise} />
        <Loading state={reminders}>{(list) => <NextDates reminders={list} onChanged={reminders.reload} />}</Loading>
        <HealthSection health={health} after={gate.promise} />
        {propertyList.data?.length === 0 ? (
          <div className="card stack">
            <p>{t.noProperty}</p>
            <p className="muted">{t.noPropertyHint}</p>
            <p className="row">
              <Link href="/owners">{t.owners}</Link>
              <Link href="/properties/new">{t.newProperty}</Link>
            </p>
          </div>
        ) : null}
      </section>
    </DetailLayout>
  );
}
