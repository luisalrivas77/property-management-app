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
// - a daily bank sync the bank asked to approve (bank_approval_needed, phase 5 D120):
//   „Jetzt abrufen“ starts a manual sync and opens the account's Bankabruf card, where the
//   approval wait is shown; a failed sync or a balance drift links to that card.
// - a deadline reminder (reminder, phase 5 §6, §11) visible today: its label, the link to its
//   scope (the Grundsteuer card to the schedule row on Steuern) and "Erledigt", which posts
//   the acknowledgement. GET /inbox names the reminder's id only; the label comes from
//   GET /reminders, loaded alongside.
// - a transfer between own accounts that waits for a person (transfer_link, transfer_unmatched,
//   phase 6 D162): the counterparts to pick and „Verknüpfen“ (POST /transfers); one without a
//   counterpart links to Konten, to import the other account.
// - a property cost paid from a private account (phase 6 D161): „Übernehmen“ records the
//   proposed privately paid costs (property_cost_row, POST /transactions/{id}/private-costs);
//   „Beleg hochladen“ uploads the missing bill and saves it on the line (property_cost_bill);
//   „In <Jahr> zählen“ moves the bill's costs to the year the line paid them
//   (property_cost_year, POST /transactions/{id}/private-costs/year); an attached bill that
//   yields no costs links to its document page (property_cost_document).
//
// GET /inbox names ids only, so the lines, imports, accounts, duplicates and documents are
// loaded alongside to show what each item is about. Amounts are shown as the API returns them.
//
// "KI-Vorschläge holen" (D50) sits under the Zahlungen zuordnen heading while a line there has
// no rows: it sends those lines to the model again (POST /inbox/ai-retry) and reloads the list
// once the jobs have had time to run; each reload updates the sidebar count (the page's own
// notifyInboxChanged). It is outside the list, so the list holds only the lines.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api, ok } from "@/api/client";
import type { Account, Document, PossibleDuplicate, StatementImport, Transaction } from "@/api/schemas";
import type { components } from "@/api/types";
import { ErrorNotice } from "@/lib/ErrorNotice";
import { notifyInboxChanged } from "@/lib/inboxCount";

import { BalanceForm, DuplicateCard, OpeningFromClosing } from "./accounts";
import { documentLabel, UploadControl } from "./documents";
import { formatDate, formatDateTime, formatMoney } from "./format";
import { ALLOCATION_CATEGORY, INBOX_ITEM_KIND } from "./labels";
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
  { key: "transfers", title: "Überweisungen zwischen eigenen Konten" },
  { key: "property_costs", title: "Immobilie, privat bezahlt" },
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
  // Phase 6: a transfer between own accounts (D162) and a property cost paid privately (D161).
  transfer_link: "transfers",
  transfer_unmatched: "transfers",
  property_cost_row: "property_costs",
  property_cost_bill: "property_costs",
  property_cost_year: "property_costs",
  property_cost_document: "property_costs",
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
  transfer_link: "badge badge-info",
  transfer_unmatched: "badge badge-warn",
  property_cost_row: "badge badge-info",
  property_cost_bill: "badge badge-warn",
  property_cost_year: "badge badge-info",
  property_cost_document: "badge badge-warn",
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

/** GET /inbox plus what its items point at: every bank account's lines and imports (the rental
 * account and, since phase 6 D156, the private ones), the possible duplicates and the vault's
 * documents (one household holds few of each). */
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
      .filter((account) => account.kind === "bank_current" || account.kind === "bank_savings")
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
                        account={privateAccount(data.accounts, item.account_id)}
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
  const [heldBack, setHeldBack] = useState(0);

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
      setHeldBack(result.private_held_back);
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
          {queued === 1 ? "1 Zahlung" : `${queued} Zahlungen`} an die KI geschickt.
          {queued > 0 ? " Die Vorschläge erscheinen in ein paar Minuten." : null}
        </Notice>
      ) : null}
      {queued !== null && heldBack > 0 ? (
        <Notice>
          {heldBack === 1 ? "1 Zahlung privater Konten wurde" : `${heldBack} Zahlungen privater Konten wurden`} nicht geschickt: die KI
          für private Konten ist ausgeschaltet. Einschalten unter <Link href="/settings">Einstellungen</Link> › „Private Konten mit KI
          zuordnen“, dann hier noch einmal „KI-Vorschläge holen“.
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
/** The line's account when it is a private one (phase 6 D158), which its card names. */
function privateAccount(accounts: Map<string, Account>, accountId: string | null | undefined): Account | undefined {
  const account = accountId ? accounts.get(accountId) : undefined;
  return account?.purpose === "private" ? account : undefined;
}

function LineEntry({
  item,
  line,
  account,
  pressed,
  onSelect,
}: {
  item: InboxItem;
  line: Transaction | undefined;
  /** Set for a line of a private account (phase 6 D158): the card names the account. */
  account: Account | undefined;
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
        {account ? <span className="inbox-detail">Privatkonto · {account.label}</span> : null}
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
  if (item.kind === "bank_sync_failed" || item.kind === "bank_balance_drift" || item.kind === "bank_approval_needed") {
    const account = item.account_id ? data.accounts.get(item.account_id) : undefined;
    return <strong>Bankabruf{account ? ` · ${account.label}` : ""}</strong>;
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
  if ((item.kind === "transfer_link" || item.kind === "transfer_unmatched") && item.transfer) {
    const { leg } = item.transfer;
    return (
      <>
        <strong>
          {leg.account_label} · {signedMoney(leg.amount)}
        </strong>
        <span className="inbox-detail">
          {[formatDate(leg.booked_on), leg.counterparty_name ?? ""].filter(Boolean).join(" · ")}
        </span>
      </>
    );
  }
  if (item.kind.startsWith("property_cost_")) {
    const line = item.transaction_id ? data.transactions.get(item.transaction_id) : undefined;
    const account = line ? data.accounts.get(line.account_id) : undefined;
    return (
      <>
        <strong>{document ? documentLabel(document) : (line?.counterparty_name ?? "Privat bezahlte Rechnung")}</strong>
        {line ? (
          <span className="inbox-detail">
            {[formatDate(line.booking_date), formatMoney(line.amount.replace(/^-/, "")), account?.label ?? ""].filter(Boolean).join(" · ")}
          </span>
        ) : null}
        {(item.private_costs ?? []).map((row, index) => (
          <span key={index} className="inbox-detail">
            {privateCostText(row)}
          </span>
        ))}
      </>
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

/** A daily sync the bank asked to approve (D120): „Jetzt abrufen“ starts a manual sync
 * (POST /accounts/{id}/syncs restarts the waiting row) and opens the account's Bankabruf
 * card, which shows the five-minute wait for the approval in the S-pushTAN app. */
function BankApprovalStart({ accountId }: { accountId: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      await ok(api.POST("/api/accounts/{account_id}/syncs", { params: { path: { account_id: accountId } } }));
      notifyInboxChanged();
      router.push(`/accounts/${accountId}?tab=kontoauszuege`);
    } catch (caught) {
      setError(caught);
      setBusy(false);
    }
  }

  return (
    <>
      <button type="button" onClick={() => void start()} disabled={busy}>
        {busy ? "Startet …" : "Jetzt abrufen"}
      </button>
      <ErrorNotice error={error} />
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
      if (!imp) return null;
      return (
        <>
          <OpeningFromClosing statementImport={imp} onDone={onChanged} />
          <Link href={`/accounts/${imp.account_id}?tab=kontoauszuege`}>Kontoauszug prüfen</Link>
        </>
      );
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
    case "bank_sync_failed":
    case "bank_balance_drift":
      return item.account_id ? <Link href={`/accounts/${item.account_id}?tab=kontoauszuege`}>Zum Bankabruf</Link> : null;
    case "bank_approval_needed":
      return item.account_id ? <BankApprovalStart accountId={item.account_id} /> : null;
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
    case "transfer_link":
    case "transfer_unmatched": {
      const open = item.transfer;
      if (!open) return null;
      if (open.candidates.length > 0) return <TransferPick open={open} accounts={data.accounts} onLinked={onChanged} />;
      return (
        <>
          <Link href="/accounts">Anderes Konto importieren</Link>
          {open.leg.source === "transaction" ? <Link href={`/inbox?umsatz=${open.leg.id}`}>Buchung öffnen</Link> : null}
        </>
      );
    }
    case "property_cost_row":
      return item.transaction_id && (item.private_costs ?? []).length > 0 ? (
        <PrivateCostApply transactionId={item.transaction_id} onApplied={onChanged} />
      ) : null;
    case "property_cost_bill":
      return item.transaction_id ? <BillUpload transactionId={item.transaction_id} onAttached={onChanged} /> : null;
    case "property_cost_year":
      return item.transaction_id && item.tax_year != null ? (
        <YearPaidApply transactionId={item.transaction_id} year={item.tax_year} onApplied={onChanged} />
      ) : null;
    case "property_cost_document":
      return item.document_id ? <Link href={`/documents/${item.document_id}`}>Beleg öffnen</Link> : null;
    default:
      return null;
  }
}

// --- Phase 6: transfers between own accounts (D162) and privately paid property costs (D161)

type OpenTransfer = components["schemas"]["OpenTransfer"];
type TransferLegView = components["schemas"]["TransferLegView"];
type TransferLinkRequest = components["schemas"]["TransferLinkRequest"];
type TransferLinkKind = components["schemas"]["TransferLinkKind"];
type TaxInputCreate = components["schemas"]["TaxInputCreate"];
type PrivateCostValue = components["schemas"]["PrivateCostValue"];

/** The link's kind from its two accounts, as backend rules/matching.transfer_link_kind. */
function linkKind(accounts: Map<string, Account>, a: TransferLegView, b: TransferLegView): TransferLinkKind {
  const kinds = [accounts.get(a.account_id)?.kind, accounts.get(b.account_id)?.kind];
  if (kinds.includes("exchange")) return "exchange_eur";
  if (kinds.includes("depot")) return "depot_cash";
  return "bank";
}

/** POST /transfers' body: the leg paid out is the out leg, each by its table (D162). */
function linkBody(accounts: Map<string, Account>, leg: TransferLegView, other: TransferLegView): TransferLinkRequest {
  const [out, in_] = leg.amount.startsWith("-") ? [leg, other] : [other, leg];
  return {
    rebook: other.rebook_on_link,
    kind: linkKind(accounts, out, in_),
    out_transaction_id: out.source === "transaction" ? out.id : null,
    out_movement_id: out.source === "movement" ? out.id : null,
    in_transaction_id: in_.source === "transaction" ? in_.id : null,
    in_movement_id: in_.source === "movement" ? in_.id : null,
  };
}

function legText(leg: TransferLegView): string {
  return [leg.account_label, formatDate(leg.booked_on), signedMoney(leg.amount), leg.counterparty_name ?? ""].filter(Boolean).join(" · ");
}

/** D176 (4), review of step 2b: why a candidate booked as something else (a card bill answered
 * „Kreditkarte“ before the card account existed) is booked as a transfer when it is linked. */
function rebookText(candidate: TransferLegView, leg: TransferLegView): string {
  const debit = candidate.amount.startsWith("-");
  const what = debit ? "Die Abbuchung" : "Die Gutschrift";
  const counted = debit ? "Ausgabe" : "Einnahme";
  const booked = candidate.booked_category
    ? `ist bisher als „${ALLOCATION_CATEGORY[candidate.booked_category]}“ gebucht und zählt damit als ${counted}`
    : "ist noch nicht gebucht";
  const why =
    leg.account_kind === "credit_card" || candidate.account_kind === "credit_card"
      ? "weil die Kartenumsätze selbst schon gezählt werden"
      : "weil das Geld nur zwischen zwei eigenen Konten wechselt";
  return `${what} vom ${formatDate(candidate.booked_on)} ${booked}; sie wird als Übertrag gebucht, ${why}.`;
}

/** The counterpart's picker of a transfer card: one candidate is pre-selected, several are
 * listed for the person to choose; „Verknüpfen“ posts the link (matched_by user). A candidate
 * booked otherwise but tied to the leg by an own IBAN (rebook_on_link) is re-booked as a
 * transfer first: the button says so, with one line why. */
function TransferPick({ open, accounts, onLinked }: { open: OpenTransfer; accounts: Map<string, Account>; onLinked: () => void }) {
  const [choice, setChoice] = useState(open.candidates.length === 1 ? (open.candidates[0]?.id ?? "") : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const name = `transfer-${open.leg.id}`;
  const chosen = open.candidates.find((candidate) => candidate.id === choice);
  const rebook = chosen?.rebook_on_link === true;

  async function link() {
    const other = open.candidates.find((candidate) => candidate.id === choice);
    if (!other) return;
    setBusy(true);
    setError(null);
    try {
      await ok(api.POST("/api/transfers", { body: linkBody(accounts, open.leg, other) }));
      onLinked();
    } catch (caught) {
      setError(caught);
      setBusy(false);
    }
  }

  return (
    <fieldset className="stack">
      <legend className="visually-hidden">Gegenbuchung</legend>
      {open.candidates.map((candidate) => (
        <label key={candidate.id}>
          <input type="radio" name={name} value={candidate.id} checked={choice === candidate.id} onChange={() => setChoice(candidate.id)} />{" "}
          {legText(candidate)}
        </label>
      ))}
      {rebook && chosen ? <p className="muted">{rebookText(chosen, open.leg)}</p> : null}
      <button type="button" onClick={() => void link()} disabled={busy || choice === ""}>
        {busy ? "Verknüpft …" : rebook ? "Als Übertrag buchen und verknüpfen" : "Verknüpfen"}
      </button>
      <ErrorNotice error={error} />
    </fieldset>
  );
}

/** One proposed private_cost row as the card lists it: label and amount, before „Übernehmen“. */
function privateCostText(row: TaxInputCreate): string {
  const value = row.value as PrivateCostValue;
  const year = row.tax_year != null ? ` · Steuerjahr ${row.tax_year}` : "";
  return `${value.label}: ${formatMoney(value.amount)}${year}`;
}

/** D161 path 2: the bill's rows as privately paid costs (tax_input private_cost), recorded as
 * the card proposed them by POST /transactions/{id}/private-costs in one step; a bill whose
 * rows exist already is left as it is (no cost counted twice). */
function PrivateCostApply({ transactionId, onApplied }: { transactionId: string; onApplied: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function apply() {
    setBusy(true);
    setError(null);
    try {
      await ok(api.POST("/api/transactions/{transaction_id}/private-costs", { params: { path: { transaction_id: transactionId } } }));
      onApplied();
    } catch (caught) {
      setError(caught);
      setBusy(false);
    }
  }

  return (
    <>
      <button type="button" onClick={() => void apply()} disabled={busy}>
        {busy ? "Übernimmt …" : "Übernehmen"}
      </button>
      <ErrorNotice error={error} />
    </>
  );
}

/** D161 (1) with D143's rule: the bill's privately paid costs count in the year the private
 * line paid them; „In <Jahr> zählen“ moves them (POST /transactions/{id}/private-costs/year).
 * Until then they stay in their year. */
function YearPaidApply({ transactionId, year, onApplied }: { transactionId: string; year: number; onApplied: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function apply() {
    setBusy(true);
    setError(null);
    try {
      await ok(api.POST("/api/transactions/{transaction_id}/private-costs/year", { params: { path: { transaction_id: transactionId } } }));
      onApplied();
    } catch (caught) {
      setError(caught);
      setBusy(false);
    }
  }

  return (
    <>
      <button type="button" onClick={() => void apply()} disabled={busy}>
        {busy ? "Wird verschoben …" : `In ${year} zählen`}
      </button>
      <p className="muted">Privat bezahlte Werbungskosten zählen im Jahr der Zahlung (§ 11 Abs. 2 EStG).</p>
      <ErrorNotice error={error} />
    </>
  );
}

/** D161 path 3: the bill of a private line booked as a property cost. The upload is read like
 * any other; then the line's current rows are saved again with the bill on its property-cost
 * row(s), which brings it to path 1 or 2. */
function BillUpload({ transactionId, onAttached }: { transactionId: string; onAttached: () => void }) {
  const [error, setError] = useState<unknown>(null);

  async function attach(documentId: string) {
    setError(null);
    try {
      const path = { params: { path: { transaction_id: transactionId } } };
      const view = await ok(api.GET("/api/transactions/{transaction_id}/allocations", path));
      const allocations = view.allocations.map((row) => ({
        amount: row.amount,
        category: row.category,
        property_id: row.property_id ?? null,
        unit_id: row.unit_id ?? null,
        tenancy_id: row.tenancy_id ?? null,
        loan_id: row.loan_id ?? null,
        service_period_start: row.service_period_start ?? null,
        service_period_end: row.service_period_end ?? null,
        recurring: row.recurring,
        document_id: row.category === "hh_property_cost" && !row.document_id ? documentId : (row.document_id ?? null),
      }));
      await ok(
        api.PUT("/api/transactions/{transaction_id}/allocations", {
          ...path,
          body: {
            allocation_set: { transaction_id: transactionId, allocations, method: "user", confidence: "1" },
            learn_rule: false,
            purpose_pattern: null,
            same_amount_only: false,
          },
        }),
      );
      onAttached();
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <>
      <UploadControl label="Beleg hochladen" prefill onUploaded={(result) => void attach(result.document.id)} />
      <ErrorNotice error={error} />
    </>
  );
}
