"use client";

// Verlauf (History, optionally for one unit) and Protokoll (audit trail) of a property.
import { useCallback, useState } from "react";

import { api, ok } from "@/api/client";
import { AuditTrail, HistoryTimeline } from "@/components/history";
import { historySubject, loadPropertyContext } from "@/components/propertyContext";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useT } from "@/i18n";
import { propertyPage } from "@/i18n/propertyPage";

export function HistoryTab({ propertyId }: { propertyId: string }) {
  const t = useT(propertyPage);
  const [unitId, setUnitId] = useState("");
  const loadHistory = useCallback(
    () =>
      ok(
        api.GET("/api/properties/{property_id}/history", {
          params: { path: { property_id: propertyId }, query: unitId ? { unit_id: unitId } : {} },
        }),
      ),
    [propertyId, unitId],
  );
  const loadContext = useCallback(() => loadPropertyContext(propertyId), [propertyId]);
  const history = useLoad(loadHistory);
  const context = useLoad(loadContext);

  return (
    <div className="stack">
      <p className="muted">{t.historyIntro}</p>
      {context.data && context.data.units.length > 1 ? (
        <div>
          <label htmlFor="history-unit">{t.historyUnit}</label>
          <select id="history-unit" value={unitId} onChange={(e) => setUnitId(e.target.value)}>
            <option value="">{t.allUnits}</option>
            {context.data.units.map((unit) => (
              <option key={unit.id} value={unit.id}>
                {unit.label}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      <Loading state={history}>{(entries) => <HistoryTimeline entries={entries} subject={historySubject(context.data)} />}</Loading>
    </div>
  );
}

export function AuditTab({ propertyId }: { propertyId: string }) {
  const t = useT(propertyPage);
  const load = useCallback(
    () => ok(api.GET("/api/properties/{property_id}/events", { params: { path: { property_id: propertyId } } })),
    [propertyId],
  );
  const events = useLoad(load);
  return (
    <div className="stack">
      <p className="muted">{t.auditIntro}</p>
      <Loading state={events}>{(list) => <AuditTrail events={list} />}</Loading>
    </div>
  );
}
