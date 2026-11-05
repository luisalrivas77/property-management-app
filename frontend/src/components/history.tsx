"use client";

// History and audit trail (§9.4): two views of the same event log (principle 4).
//
// History: one line per TimelineEntry, in the API's order (date, then the root event's
// recording order). Label from the EventType, a summary of the payload, and for a corrected
// milestone "korrigiert, ursprünglich {original date}". Nothing is re-sorted or computed here.
//
// Audit trail: every Event in seq order, grouped by correlation_id (one request), with the
// actor and the payload collapsed.
import type { Event, JsonValue, TimelineEntry } from "@/api/schemas";
import { useAuth } from "@/auth/AuthGate";

import { formatDate, formatDateTime, formatMoney, formatPercent, shortId } from "./format";
import { ACQUISITION_COST_KIND, EVENT_TYPE, NOTICE_GIVEN_BY, RENT_CHANGE_REASON, RENT_MODEL } from "./labels";
import { CorrectedNote } from "./ui";

type Payload = { [key: string]: JsonValue };

function text(payload: Payload, key: string): string | null {
  const value = payload[key];
  return typeof value === "string" ? value : null;
}

function inMap<K extends string>(labels: Record<K, string>, value: string | null): string | null {
  return value !== null && value in labels ? labels[value as K] : null;
}

/** The payload summary of a milestone: price, rent, amount (§9.4). Values are stored data. */
export function payloadSummary(type: TimelineEntry["type"], payload: Payload): string | null {
  switch (type) {
    case "property.purchase_notarized":
      return `Kaufpreis ${formatMoney(text(payload, "purchase_price"))}`;
    case "property.possession_transferred":
      return "Beginn der AfA";
    case "property.ownership_set": {
      const shares = payload.shares;
      return Array.isArray(shares) ? `${shares.length} Eigentümer` : null;
    }
    case "property.acquisition_cost_recorded":
      return `${inMap(ACQUISITION_COST_KIND, text(payload, "kind")) ?? "Nebenkosten"}: ${formatMoney(text(payload, "amount"))}`;
    case "tenancy.created": {
      const model = inMap(RENT_MODEL, text(payload, "rent_model"));
      const term = text(payload, "fixed_term_until");
      const parts = [model, `Kaution ${formatMoney(text(payload, "deposit_amount"))}`];
      if (term) parts.push(`befristet bis ${formatDate(term)}`);
      return parts.filter(Boolean).join(", ");
    }
    case "tenancy.rent_scheduled": {
      const reason = inMap(RENT_CHANGE_REASON, text(payload, "reason"));
      const rent = `${formatMoney(text(payload, "cold_rent"))} kalt + ${formatMoney(text(payload, "operating_costs_prepayment"))} NK-Vorauszahlung`;
      return reason ? `${reason}: ${rent}` : rent;
    }
    case "tenancy.deposit_received":
      return formatMoney(text(payload, "amount"));
    case "tenancy.notice_given": {
      const by = inMap(NOTICE_GIVEN_BY, text(payload, "given_by"));
      return `${by ? `durch ${by}, ` : ""}zum ${formatDate(text(payload, "ends_on"))}`;
    }
    case "loan.created": {
      const parts = [
        text(payload, "lender"),
        formatMoney(text(payload, "principal")),
        `${formatPercent(text(payload, "nominal_rate"))} Sollzins`,
      ];
      return parts.filter(Boolean).join(", ");
    }
    case "loan.disbursed":
      return formatMoney(text(payload, "amount"));
    default:
      return null;
  }
}

/** The History timeline. `subject` names the entity of an entry (e.g. the unit of a tenancy). */
export function HistoryTimeline({
  entries,
  subject,
}: {
  entries: TimelineEntry[];
  subject?: (entry: TimelineEntry) => string | null;
}) {
  if (entries.length === 0) return <p className="muted">Noch keine Meilensteine.</p>;
  return (
    <ol className="timeline" aria-label="Verlauf">
      {entries.map((entry) => {
        const about = subject?.(entry);
        const summary = payloadSummary(entry.type, entry.payload);
        return (
          <li key={entry.root_event_id} data-type={entry.type}>
            <span className="when">{formatDate(entry.effective_date)}</span>
            <span className="what">{EVENT_TYPE[entry.type]}</span>
            {about ? <span> · {about}</span> : null}
            {entry.corrected && entry.original_effective_date ? (
              <CorrectedNote original={formatDate(entry.original_effective_date)} />
            ) : null}
            {summary ? <span className="summary">{summary}</span> : null}
          </li>
        );
      })}
    </ol>
  );
}

/** The milestone of one entity in a History (e.g. a tenancy's move-in), if any. */
export function findMilestone(entries: TimelineEntry[] | undefined, type: TimelineEntry["type"], entityId: string) {
  return entries?.find((entry) => entry.type === type && entry.entity_id === entityId);
}

/** The original date to show next to a stored milestone date, when History says it was
 * corrected. Only while History and the entity agree on the date in force: right after a
 * correction one of the two reloads first, and a note beside a stale date would be wrong. */
export function correctedFrom(entry: TimelineEntry | undefined, storedDate: string): string | null {
  if (!entry?.corrected || !entry.original_effective_date || entry.effective_date !== storedDate) return null;
  return entry.original_effective_date;
}

function groupByCorrelation(events: Event[]): Event[][] {
  const groups: Event[][] = [];
  for (const event of events) {
    const last = groups[groups.length - 1];
    if (last && last[0]?.correlation_id === event.correlation_id) last.push(event);
    else groups.push([event]);
  }
  return groups;
}

/** The audit trail (Protokoll): every event, grouped per request. */
export function AuditTrail({ events }: { events: Event[] }) {
  const { me } = useAuth();
  if (events.length === 0) return <p className="muted">Keine Einträge.</p>;
  function actor(event: Event): string {
    if (event.actor_type === "user") {
      return event.actor_id === `user:${me.user.id}` ? "du" : `Haushaltsmitglied ${shortId(event.actor_id.replace(/^user:/, ""))}`;
    }
    return event.actor_type === "ai" ? `KI (${event.actor_id})` : `System (${event.actor_id})`;
  }
  return (
    <div>
      {groupByCorrelation(events).map((group) => {
        const first = group[0];
        if (!first) return null;
        return (
          <section key={`${first.correlation_id}-${first.seq}`} className="audit-group">
            <p className="muted">
              {formatDateTime(first.recorded_at)} · {actor(first)} · Anfrage <code>{shortId(first.correlation_id)}</code>
            </p>
            <ul>
              {group.map((event) => (
                <li key={event.id}>
                  <span className="when">#{event.seq}</span> {EVENT_TYPE[event.type]}
                  {event.effective_date ? <span> · {formatDate(event.effective_date)}</span> : null}
                  {event.corrects_event_id ? <span className="corrected">Korrektur</span> : null}
                  <details>
                    <summary className="muted">
                      <code>{event.type}</code> · {event.entity_type} {shortId(event.entity_id)}
                    </summary>
                    <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                  </details>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
