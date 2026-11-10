"use client";

// The review queue of the review page, "Zu prüfen" (docs/phases/phase-2.md §8.3,
// docs/mockups/03-inbox.png), grouped by what needs deciding (SECTIONS), each group newest
// first as GET /inbox returns it. Each item shows its kind, the one-line reasoning and the
// question the API wrote, and acts through its own endpoint:
//
// - a bank line (allocation_proposed, unmatched): selecting it opens the allocation panel
//   (components/allocations.tsx);
// - a document proposal, a failed or blocked reading: a link to the document page, where the
//   Vorschlag is reviewed;
// - an import waiting for its balance: the Konten balance form; one that does not reconcile: a
//   link to the account's Kontoauszüge, which show both balances;
// - a possible duplicate: the Konten card with Zusammenführen / Beide behalten;
// - last year's open Jahresfragen (questionnaire_open, phase 4b D110): a link into the flow
//   at /tax/<year>/fragen;
// - a forwarded mail's attachment (email_received, phase 5 D121): a link to the document, or
//   to the account's Kontoauszüge when it was imported; a mail that failed three times
//   (email_failed) has no action, its question says to upload the attachments directly. A
//   poll that cannot log in, find its label or reach Gmail (email_inbox_failed) has none
//   either: its question says what to fix, and the card goes once a poll completes.
// - a deadline reminder (reminder, phase 5 §6, §11) visible today: its label, the link to its
//   scope (the Grundsteuer card to the schedule row on Steuern) and "Erledigt", which posts
//   the acknowledgement. GET /inbox names the reminder's id only; the label comes from
//   GET /reminders, loaded alongside.
//
// GET /inbox names ids only, so the lines, imports, accounts, duplicates and documents are
// loaded alongside to show what each item is about. Amounts are shown as the API returns them.
//
// "KI-Vorschläge holen" (D50) sits under the Zahlungen zuordnen heading while a line there has
// no rows: it sends those lines to the model again (POST /inbox/ai-retry) and reloads the list
// once the jobs have had time to run; each reload updates the sidebar count (the page's own
// notifyInboxChanged). It is outside the list, so the list holds only the lines.
import Link from "next/link";
import { useEffect, useState } from "react";

import { api, ok } from "@/api/client";
import type { Account, Document, PossibleDuplicate, StatementImport, Transaction } from "@/api/schemas";
import type { components } from "@/api/types";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { BalanceForm, DuplicateCard } from "./accounts";
import { documentLabel } from "./documents";
import { formatDate, formatDateTime, formatMoney } from "./format";
import { INBOX_ITEM_KIND } from "./labels";
import { ReminderDone, reminderLabel, reminderLink, type Reminder } from "./reminders";
import { reminderTodo } from "./todos";
import { ActionDialog, Notice } from "./ui";

// Step 4's schemas have no alias in schemas.ts yet.
export type InboxItem = components["schemas"]["InboxItem"];
type InboxItemKind = components["schemas"]["InboxItemKind"];

/** The kinds whose item is a bank line to allocate in the panel. */
const LINE_KINDS: readonly InboxItemKind[] = ["allocation_proposed", "unmatched"];

export function isLineItem(item: InboxItem): boolean {
  return LINE_KINDS.includes(item.kind) && !!item.transaction_id;
}

/** The groups of the queue, in this order; a group without items is not shown. */
const SECTIONS = [
  { key: "lines", title: "Zahlungen zuordnen" },
  { key: "proposals", title: "KI-Vorschläge zu Dokumenten" },
  { key: "unread", title: "Dokumente, die nicht gelesen wurden" },
  { key: "imports", title: "Kontoauszüge" },
  { key: "duplicates", title: "Mögliche Doppelbuchungen" },
  { key: "automatic", title: "Automatisch" },
  { key: "other", title: "Weitere Punkte" },
] as const;

type SectionKey = (typeof SECTIONS)[number]["key"];

/** Every kind has its group, so the compiler proves none is dropped. */
const SECTION_OF: Record<InboxItemKind, SectionKey> = {
  allocation_proposed: "lines",
  unmatched: "lines",
  proposal: "proposals",
  read_failed: "unread",
  blocked_tenant_data: "unread",
  import_pending_balance: "imports",
  import_unreconciled: "imports",
  possible_duplicate: "duplicates",
  questionnaire_open: "other",
  // Phase 5's card kinds (§11's six, and email_inbox_failed) share "Automatisch"; the API
  // returns each only once its step is built.
  bank_approval_needed: "automatic",
  bank_sync_failed: "automatic",
  bank_balance_drift: "automatic",
  email_received: "automatic",
  email_failed: "automatic",
  email_inbox_failed: "automatic",
  reminder: "automatic",
};

/** A line kind without a line id cannot open the panel: it is listed under Weitere Punkte,
 * with its kind and reasoning, rather than dropped. */
function sectionOf(item: InboxItem): SectionKey {
  const section = SECTION_OF[item.kind];
  return section === "lines" && !isLineItem(item) ? "other" : section;
}

const PILL_CLASS: Record<InboxItemKind, string> = {
  proposal: "badge badge-info",
  read_failed: "badge badge-danger",
  blocked_tenant_data: "badge badge-warn",
  import_unreconciled: "badge badge-danger",
  import_pending_balance: "badge badge-warn",
  possible_duplicate: "badge badge-warn",
  allocation_proposed: "badge badge-info",
  unmatched: "badge badge-warn",
  questionnaire_open: "badge badge-info",
  bank_approval_needed: "badge badge-warn",
  bank_sync_failed: "badge badge-danger",
  bank_balance_drift: "badge badge-warn",
  email_received: "badge badge-info",
  email_failed: "badge badge-danger",
  email_inbox_failed: "badge badge-danger",
  reminder: "badge badge-info",
};

export interface InboxData {
  items: InboxItem[];
  transactions: Map<string, Transaction>;
  imports: Map<string, StatementImport>;
  accounts: Map<string, Account>;
  duplicates: Map<string, PossibleDuplicate>;
  documents: Map<string, Document>;
  reminders: Map<string, Reminder>;
}

/** GET /inbox plus what its items point at: every statement account's lines and imports, the
 * possible duplicates and the vault's documents (one household holds few of each). */
export async function loadInbox(): Promise<InboxData> {
  const [items, accounts, duplicates, documents, reminders] = await Promise.all([
    ok(api.GET("/api/inbox")),
    ok(api.GET("/api/accounts")),
    ok(api.GET("/api/possible-duplicates")),
    ok(api.GET("/api/documents")),
    ok(api.GET("/api/reminders")),
  ]);
  const perAccount = await Promise.all(
    accounts
      .filter((account) => !account.is_owner_account)
      .map(async (account) => {
        const path = { params: { path: { account_id: account.id } } };
        const [imports, transactions] = await Promise.all([
          ok(api.GET("/api/accounts/{account_id}/imports", path)),
          ok(api.GET("/api/accounts/{account_id}/transactions", path)),
        ]);
        return { imports, transactions };
      }),
  );
  return {
    items,
    transactions: new Map(perAccount.flatMap((entry) => entry.transactions).map((line) => [line.id, line])),
    imports: new Map(perAccount.flatMap((entry) => entry.imports).map((imp) => [imp.id, imp])),
    accounts: new Map(accounts.map((account) => [account.id, account])),
    duplicates: new Map(duplicates.map((pair) => [pair.id, pair])),
    documents: new Map(documents.map((document) => [document.id, document])),
    reminders: new Map(reminders.map((reminder) => [reminder.id, reminder])),
  };
}

/** "5 offene Punkte": the total; each section heading carries its own count. */
export function inboxSummary(items: InboxItem[]): string {
  return items.length === 1 ? "1 offener Punkt" : `${items.length} offene Punkte`;
}

function isCredit(amount: string): boolean {
  return !amount.startsWith("-") && !/^0+(\.0+)?$/.test(amount);
}

function signedMoney(amount: string): string {
  return `${isCredit(amount) ? "+" : ""}${formatMoney(amount)}`;
}

export function InboxList({
  data,
  selected,
  onSelect,
  onChanged,
}: {
  data: InboxData;
  selected: string | null;
  onSelect: (transactionId: string) => void;
  onChanged: () => void;
}) {
  return (
    <div className="inbox-sections">
      {SECTIONS.map(({ key: section, title }) => {
        const items = data.items.filter((item) => sectionOf(item) === section);
        if (items.length === 0) return null;
        const headingId = `inbox-section-${section}`;
        return (
          <section key={section} className="inbox-section" aria-labelledby={headingId}>
            <h3 className="inbox-section-head">
              <span id={headingId}>{title}</span>{" "}
              <span className="inbox-section-count">{items.length}</span>
            </h3>
            {section === "lines" && items.some((item) => item.kind === "unmatched") ? <AiSuggestions onChanged={onChanged} /> : null}
            <ul className="inbox-list" aria-label={title}>
              {items.map((item) => {
                // A possible duplicate also names its later line, which may sit in two pairs: its own id first.
                // A forwarded document can arrive in two mails, and a failed mail names no id: the time tells them apart.
                const key = `${item.kind}:${item.possible_duplicate_id ?? item.proposal_id ?? item.import_id ?? item.transaction_id ?? item.document_id ?? item.reminder_id}:${item.created_at}`;
                return (
                  <li key={key}>
                    {isLineItem(item) ? (
                      <LineEntry
                        item={item}
                        line={data.transactions.get(item.transaction_id ?? "")}
                        pressed={selected === item.transaction_id}
                        onSelect={onSelect}
                      />
                    ) : (
                      <OtherEntry item={item} data={data} onChanged={onChanged} />
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

/** How long the jobs get before the list is reloaded: a categorize call takes a few seconds per
 * line, and the worker runs several at once. */
const AI_RELOAD_MS = 30_000;

/** "KI-Vorschläge holen": sends every unmatched line to the model again (D50). */
function AiSuggestions({ onChanged }: { onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [queued, setQueued] = useState<number | null>(null);

  useEffect(() => {
    if (queued === null) return;
    const timer = window.setTimeout(onChanged, AI_RELOAD_MS);
    return () => window.clearTimeout(timer);
  }, [queued, onChanged]);

  async function request() {
    setBusy(true);
    setError(null);
    setQueued(null);
    try {
      const result = await ok(api.POST("/api/inbox/ai-retry"));
      setQueued(result.queued);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="inbox-section-action stack">
      <div>
        <button type="button" className="secondary" disabled={busy} onClick={() => void request()}>
          KI-Vorschläge holen
        </button>
      </div>
      {queued !== null ? (
        <Notice>
          {queued === 1 ? "1 Zahlung" : `${queued} Zahlungen`} an die KI geschickt. Die Vorschläge erscheinen in ein paar Minuten.
        </Notice>
      ) : null}
      <ErrorNotice error={error} />
    </div>
  );
}

function Why({ item }: { item: InboxItem }) {
  return (
    <span className="inbox-why">
      <span className={PILL_CLASS[item.kind]}>{INBOX_ITEM_KIND[item.kind]}</span>
      {item.reasoning ? <span className="inbox-reasoning">{item.reasoning}</span> : null}
      {item.question ? <span className="inbox-question">{item.question}</span> : null}
    </span>
  );
}

/** A bank line: the whole row is the button that opens the panel (mockup 03). */
function LineEntry({
  item,
  line,
  pressed,
  onSelect,
}: {
  item: InboxItem;
  line: Transaction | undefined;
  pressed: boolean;
  onSelect: (transactionId: string) => void;
}) {
  const id = item.transaction_id ?? "";
  return (
    <button type="button" className="inbox-entry inbox-line" aria-pressed={pressed} onClick={() => onSelect(id)}>
      <span className="inbox-when">{line ? formatDate(line.booking_date) : formatDateTime(item.created_at)}</span>
      <span className="inbox-main">
        <strong>{line?.counterparty_name ?? "Ohne Gegenseite"}</strong>
        <span className="inbox-detail">{line ? line.purpose || "Kein Verwendungszweck" : "Buchung"}</span>
      </span>
      <span className={line && isCredit(line.amount) ? "inbox-amount amount-in" : "inbox-amount"}>
        {line ? signedMoney(line.amount) : "–"}
      </span>
      <Why item={item} />
    </button>
  );
}

/** Everything that is not a bank line: what it is about, then its own action. */
function OtherEntry({ item, data, onChanged }: { item: InboxItem; data: InboxData; onChanged: () => void }) {
  return (
    <div className="inbox-entry">
      <span className="inbox-when">{formatDateTime(item.created_at)}</span>
      <span className="inbox-main">
        <EntryTitle item={item} data={data} />
      </span>
      <span />
      <Why item={item} />
      <div className="inbox-actions">
        <EntryAction item={item} data={data} onChanged={onChanged} />
      </div>
    </div>
  );
}

function importPeriod(imp: StatementImport): string {
  if (!imp.first_booking_on) return "";
  return imp.first_booking_on === imp.last_booking_on
    ? formatDate(imp.first_booking_on)
    : `${formatDate(imp.first_booking_on)} – ${formatDate(imp.last_booking_on)}`;
}

function EntryTitle({ item, data }: { item: InboxItem; data: InboxData }) {
  const document = item.document_id ? data.documents.get(item.document_id) : undefined;
  if (item.kind === "questionnaire_open") {
    return <strong>Jahresfragen {item.tax_year ?? ""}</strong>;
  }
  if (item.kind === "email_failed") {
    return <strong>Weitergeleitete Mail</strong>;
  }
  if (item.kind === "email_inbox_failed") {
    return <strong>Posteingang per E-Mail</strong>;
  }
  if (item.kind === "reminder") {
    const reminder = item.reminder_id ? data.reminders.get(item.reminder_id) : undefined;
    return reminder ? (
      <>
        <strong>{reminderLabel(reminder)}</strong>
        <span className="inbox-detail">fällig am {formatDate(reminder.due_on)}</span>
        {reminderTodo(reminder) ? <span className="inbox-detail todo-text">Was tun: {reminderTodo(reminder)}</span> : null}
      </>
    ) : (
      <strong>Termin</strong>
    );
  }
  if (item.import_id) {
    const imp = data.imports.get(item.import_id);
    const account = imp ? data.accounts.get(imp.account_id) : undefined;
    return (
      <>
        <strong>Kontoauszug{account ? ` · ${account.label}` : ""}</strong>
        <span className="inbox-detail">
          {[imp ? importPeriod(imp) : "", document ? documentLabel(document) : ""].filter(Boolean).join(" · ")}
        </span>
      </>
    );
  }
  if (item.possible_duplicate_id) {
    const line = item.transaction_id ? data.transactions.get(item.transaction_id) : undefined;
    return (
      <>
        <strong>{line?.counterparty_name ?? "Umsatz"}</strong>
        <span className="inbox-detail">{line ? `${formatDate(line.booking_date)} · ${signedMoney(line.amount)}` : ""}</span>
      </>
    );
  }
  return (
    <>
      <strong>{document ? documentLabel(document) : "Dokument"}</strong>
    </>
  );
}

function EntryAction({ item, data, onChanged }: { item: InboxItem; data: InboxData; onChanged: () => void }) {
  switch (item.kind) {
    case "proposal":
    case "read_failed":
    case "blocked_tenant_data":
      return item.document_id ? (
        <Link href={`/documents/${item.document_id}`}>{item.kind === "proposal" ? "Vorschlag prüfen" : "Dokument öffnen"}</Link>
      ) : null;
    case "import_pending_balance": {
      const imp = item.import_id ? data.imports.get(item.import_id) : undefined;
      if (!imp) return null;
      return (
        <ActionDialog label="Endsaldo eingeben" title="Endsaldo laut Online-Banking" buttonClass="">
          {(close) => (
            <BalanceForm
              statementImport={imp}
              onDone={() => {
                close();
                onChanged();
              }}
            />
          )}
        </ActionDialog>
      );
    }
    case "import_unreconciled": {
      const imp = item.import_id ? data.imports.get(item.import_id) : undefined;
      return imp ? <Link href={`/accounts/${imp.account_id}?tab=kontoauszuege`}>Kontoauszug prüfen</Link> : null;
    }
    case "possible_duplicate": {
      const pair = item.possible_duplicate_id ? data.duplicates.get(item.possible_duplicate_id) : undefined;
      const first = pair ? data.transactions.get(pair.transaction_id) : undefined;
      const later = pair ? data.transactions.get(pair.other_transaction_id) : undefined;
      if (!pair || !first || !later) return null;
      return <DuplicateCard pair={pair} first={first} later={later} imports={data.imports} onDecided={onChanged} />;
    }
    case "questionnaire_open":
      // phase 4b D110: the one card for last year's open Jahresfragen leads into the flow.
      return item.tax_year != null ? (
        <Link href={`/tax/${item.tax_year}/fragen`}>Jahresfragen {item.tax_year} beantworten</Link>
      ) : null;
    case "email_received": {
      const imp = item.import_id ? data.imports.get(item.import_id) : undefined;
      if (imp) return <Link href={`/accounts/${imp.account_id}?tab=kontoauszuege`}>Kontoauszug ansehen</Link>;
      return item.document_id ? <Link href={`/documents/${item.document_id}`}>Dokument öffnen</Link> : null;
    }
    case "reminder": {
      const reminder = item.reminder_id ? data.reminders.get(item.reminder_id) : undefined;
      if (!reminder) return null;
      const link = reminderLink(reminder);
      return (
        <>
          {link ? <Link href={link.href}>{link.label}</Link> : null}
          <ReminderDone reminder={reminder} onDone={onChanged} />
        </>
      );
    }
    default:
      return null;
  }
}
