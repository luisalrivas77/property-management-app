"use client";

// Zu prüfen, the review page (formerly "Posteingang"; docs/phases/phase-2.md §8.3,
// docs/mockups/03-inbox.png): the upload for documents, the review queue grouped by what needs
// deciding, and the allocation panel beside it. The route stays /inbox. The selected bank line
// lives in the URL (?umsatz=…), so Konten › Umsätze can link to a line that is not in the
// queue (one already confirmed) and correct it in the same panel. Each fresh load of the
// queue tells the sidebar its count (lib/inboxCount.ts).
//
// Phase 10 (D267, mockup 18): a chosen line opens in the detail panel (`#d-umsatz-<id>`),
// beside the list on the computer and sliding in from the right on the phone, with its
// reasoning, question and every answer of the allocation panel. A link from another screen
// (?umsatz=…) still opens it. Every other card keeps its own buttons in the list.
import "./inbox.css";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { AllocationPanel, type TransactionAllocations } from "@/components/allocations";
import { DetailLayout, DetailPanel, useDetail } from "@/components/DetailPanel";
import { documentLabel, UploadControl, type Uploaded } from "@/components/documents";
import { api, ok } from "@/api/client";
import { InboxList, isLineItem, loadInbox, type InboxItem } from "@/components/inbox";
import { ALLOCATION_CATEGORY } from "@/components/labels";
import { Loading, Notice } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useT, type Translation } from "@/i18n";
import { common } from "@/i18n/common";
import { inbox as messages } from "@/i18n/inbox";
import { notifyInboxChanged } from "@/lib/inboxCount";

type T = Translation<(typeof messages)["de"]>;

export default function InboxPage() {
  return (
    <Suspense fallback={<Fallback />}>
      <Inbox />
    </Suspense>
  );
}

function Fallback() {
  const c = useT(common);
  return <p className="muted">{c.loading}</p>;
}

function Inbox() {
  const t = useT(messages);
  const router = useRouter();
  const search = useSearchParams();
  const detail = useDetail("umsatz");
  const linked = search.get("umsatz");
  const selected = detail.id ?? linked;
  const inbox = useLoad(loadInbox);
  const [saved, setSaved] = useState<TransactionAllocations | null>(null);
  const [uploads, setUploads] = useState<Uploaded[]>([]);

  // The sidebar's count follows what this page lists, after every load and reload.
  const items = inbox.data?.items;
  useEffect(() => {
    if (items) notifyInboxChanged(items.length);
  }, [items]);

  function select(transactionId: string) {
    setSaved(null);
    detail.open(transactionId);
  }

  /** Closes the panel, and drops a ?umsatz= another screen linked with. */
  function close() {
    detail.close();
    if (linked) router.replace("/inbox", { scroll: false });
  }

  // A learned rule re-checks the waiting lines it names in the background (rematch_waiting):
  // look at the list a few more times, so the lines it booked leave the queue without a page
  // reload. The re-check took about a second per line (14 Nov 2026: 39 lines of one payee in
  // 27 s), so the checks run for up to two minutes. Each check fetches GET /inbox only; the
  // whole page (every account's lines and imports) is reloaded only when the list changed,
  // and the checks stop at the first one that finds no change after one that did: the
  // re-check has finished. A check that finds no change before any change keeps going, since
  // the job may start late. A check is skipped while the page's own load runs (the one after
  // the save, or one a check started), so the change the save itself made is never taken for
  // the re-check's and `shown` is the list the page shows.
  const reload = inbox.reload;
  const shown = useRef("");
  const loading = useRef(true);
  useEffect(() => {
    if (items) shown.current = listKey(items);
  }, [items]);
  useEffect(() => {
    loading.current = inbox.loading;
  }, [inbox.loading]);
  useEffect(() => {
    if (!saved?.rule) return;
    let cancelled = false;
    let changed = false;
    let done = false;
    async function check() {
      if (cancelled || done || loading.current) return;
      try {
        const fresh = await ok(api.GET("/api/inbox"));
        if (cancelled || loading.current) return;
        if (listKey(fresh) !== shown.current) {
          changed = true;
          shown.current = listKey(fresh);
          reload();
        } else if (changed) {
          done = true;
        }
      } catch {
        // the next check tries again
      }
    }
    const timers = [3000, 10000, 20000, 35000, 60000, 90000, 120000].map((ms) => window.setTimeout(check, ms));
    return () => {
      cancelled = true;
      timers.forEach((timer) => window.clearTimeout(timer));
    };
  }, [saved, reload]);

  function onSaved(result: TransactionAllocations) {
    setSaved(result);
    close();
    inbox.reload();
  }

  const item = inbox.data?.items.find((entry) => isLineItem(entry) && entry.transaction_id === selected);

  const panel = selected ? (
    <DetailPanel title={t.selectedPayment} backLabel={t.title} onClose={close}>
      <AllocationPanel key={selected} transactionId={selected} reasoning={item?.reasoning} question={item?.question} onSaved={onSaved} />
    </DetailPanel>
  ) : null;

  return (
    <section>
      <p className="eyebrow">{t.title}</p>
      <div className="page-head">
        <h1>{t.heading}</h1>
        <Link className="button-link" href="/capture">
          {t.photo}
        </Link>
      </div>

      <div className="stack">
        <UploadControl
          label={t.upload}
          prefill
          onUploaded={(result) => {
            setUploads((current) => [result, ...current].slice(0, 5));
            inbox.reload();
          }}
        />
        <p className="muted">
          {t.statementsAt}
          <Link href="/accounts">{t.accounts}</Link>.
        </p>
        {uploads.map(({ document, existed, proposalId, aiUnavailable }) => (
          <Notice key={`${document.id}-${existed}-${proposalId ?? ""}`}>
            {existed ? t.existed : t.uploaded}
            <Link href={`/documents/${document.id}`}>{documentLabel(document)}</Link>
            {proposalId ? t.reading : null}
            {aiUnavailable ? t.aiOff : null}
          </Notice>
        ))}
        {saved ? (
          <Notice>
            {t.confirmed}
            {saved.rebooked > 0 ? ` ${rebookedText(t, saved)}` : null}
            {saved.rule ? t.ruleSaved : null}
          </Notice>
        ) : null}
      </div>

      <div className="inbox-page-list">
        <DetailLayout panel={panel}>
          <section className="card inbox-queue" aria-labelledby="inbox-queue-title">
            <div className="inbox-queue-head">
              <h2 id="inbox-queue-title">{t.todo}</h2>
              {inbox.data && inbox.data.items.length > 0 ? <span className="muted">{t.summary(inbox.data.items.length)}</span> : null}
            </div>
            <Loading state={inbox}>
              {(data) =>
                data.items.length === 0 ? (
                  <p className="muted inbox-empty">{t.empty}</p>
                ) : (
                  <InboxList data={data} selected={selected} onSelect={select} onChanged={inbox.reload} />
                )
              }
            </Loading>
          </section>
        </DetailLayout>
      </div>
    </section>
  );
}

/** „4 frühere Zahlungen auf Restaurant & Lieferdienst umgebucht.“ (phase 6 D160): the earlier
 * lines of the payee the answer re-booked before its rule was learned. */
/** What the list holds, item by item (kind, what it is about, a proposal's category), so a
 * rule's background check can tell whether its lines left the list. */
function listKey(items: InboxItem[]): string {
  return items
    .map((item) => [item.kind, item.transaction_id, item.proposal_id, item.document_id, item.account_id, item.category].join(":"))
    .join("|");
}

function rebookedText(t: T, saved: TransactionAllocations): string {
  const category = saved.rule?.category ?? saved.allocations[0]?.category;
  return t.rebooked(saved.rebooked, category ? ALLOCATION_CATEGORY[category] : t.sameCategory);
}
