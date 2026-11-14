"use client";

// Zu prüfen, the review page (formerly "Posteingang"; docs/phases/phase-2.md §8.3,
// docs/mockups/03-inbox.png): the upload for documents, the review queue grouped by what needs
// deciding, and the allocation panel beside it. The route stays /inbox. The selected bank line
// lives in the URL (?umsatz=…), so Konten › Umsätze can link to a line that is not in the
// queue (one already confirmed) and correct it in the same panel. Each fresh load of the
// queue tells the sidebar its count (lib/inboxCount.ts).
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { AllocationPanel, type TransactionAllocations } from "@/components/allocations";
import { documentLabel, UploadControl, type Uploaded } from "@/components/documents";
import { inboxSummary, InboxList, isLineItem, loadInbox } from "@/components/inbox";
import { ALLOCATION_CATEGORY } from "@/components/labels";
import { Loading, Notice } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { notifyInboxChanged } from "@/lib/inboxCount";

export default function InboxPage() {
  return (
    <Suspense fallback={<p className="muted">Lädt …</p>}>
      <Inbox />
    </Suspense>
  );
}

function Inbox() {
  const router = useRouter();
  const search = useSearchParams();
  const selected = search.get("umsatz");
  const inbox = useLoad(loadInbox);
  const [saved, setSaved] = useState<TransactionAllocations | null>(null);
  const [uploads, setUploads] = useState<Uploaded[]>([]);
  const panel = useRef<HTMLElement>(null);

  // The sidebar's count follows what this page lists, after every load and reload.
  const items = inbox.data?.items;
  useEffect(() => {
    if (items) notifyInboxChanged(items.length);
  }, [items]);

  // Stacked layout (a phone): bring the panel into view when a line is chosen.
  useEffect(() => {
    if (!selected || !panel.current) return;
    if (window.matchMedia("(max-width: 959px)").matches) panel.current.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [selected]);

  function select(transactionId: string) {
    setSaved(null);
    router.replace(`/inbox?umsatz=${transactionId}`, { scroll: false });
  }

  // A learned rule re-checks the waiting lines it names in the background (rematch_waiting):
  // reload the list a few more times, so the lines it booked leave the queue without a page
  // reload. The re-check takes about a second per line (14 Nov 2026: 39 lines of one payee in
  // 27 s, after the two reloads at 3 and 10 s had passed), so the reloads run for two minutes.
  const reload = inbox.reload;
  useEffect(() => {
    if (!saved?.rule) return;
    const timers = [3000, 10000, 20000, 35000, 60000, 90000, 120000].map((ms) => window.setTimeout(reload, ms));
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [saved, reload]);

  function onSaved(result: TransactionAllocations) {
    setSaved(result);
    router.replace("/inbox", { scroll: false });
    inbox.reload();
  }

  const item = inbox.data?.items.find((entry) => isLineItem(entry) && entry.transaction_id === selected);

  return (
    <section>
      <p className="eyebrow">Zu prüfen</p>
      <div className="page-head">
        <h1>Hochladen, bestätigen, fertig</h1>
        <Link className="button-link" href="/capture">
          Beleg fotografieren
        </Link>
      </div>

      <div className="stack">
        <UploadControl
          label="Rechnungen, Bescheide und Briefe hochladen"
          prefill
          onUploaded={(result) => {
            setUploads((current) => [result, ...current].slice(0, 5));
            inbox.reload();
          }}
        />
        <p className="muted">
          Kontoauszüge werden beim jeweiligen Konto importiert: <Link href="/accounts">Konten</Link>.
        </p>
        {uploads.map(({ document, existed, proposalId, aiUnavailable }) => (
          <Notice key={`${document.id}-${existed}-${proposalId ?? ""}`}>
            {existed ? "Bereits vorhanden: " : "Hochgeladen: "}
            <Link href={`/documents/${document.id}`}>{documentLabel(document)}</Link>
            {proposalId ? " – wird automatisch ausgelesen; der Vorschlag erscheint hier, sobald er fertig ist." : null}
            {aiUnavailable ? " Automatisches Auslesen ist auf dem Server nicht eingeschaltet." : null}
          </Notice>
        ))}
        {saved ? (
          <Notice>
            Zuordnung bestätigt.
            {saved.rebooked > 0 ? ` ${rebookedText(saved)}` : null}
            {saved.rule
              ? " Regel gespeichert: künftige Buchungen dieser Art werden automatisch so zugeordnet. Wartende Zahlungen, auf die sie passt, werden jetzt im Hintergrund neu geprüft; die Liste aktualisiert sich in den nächsten zwei Minuten von selbst."
              : null}
          </Notice>
        ) : null}
      </div>

      <div className="inbox-layout">
        <section className="card inbox-queue" aria-labelledby="inbox-queue-title">
          <div className="inbox-queue-head">
            <h2 id="inbox-queue-title">Zu erledigen</h2>
            {inbox.data && inbox.data.items.length > 0 ? <span className="muted">{inboxSummary(inbox.data.items)}</span> : null}
          </div>
          <Loading state={inbox}>
            {(data) =>
              data.items.length === 0 ? (
                <p className="muted inbox-empty">
                  Nichts zu prüfen. Neue Buchungen und gelesene Dokumente erscheinen hier, sobald etwas zu entscheiden ist.
                </p>
              ) : (
                <InboxList data={data} selected={selected} onSelect={select} onChanged={inbox.reload} />
              )
            }
          </Loading>
        </section>
        <aside ref={panel} className="card inbox-panel" aria-label="Zuordnung">
          {selected ? (
            <AllocationPanel
              key={selected}
              transactionId={selected}
              reasoning={item?.reasoning}
              question={item?.question}
              onSaved={onSaved}
            />
          ) : (
            <p className="muted">Eine Buchung aus der Liste wählen, um sie zuzuordnen.</p>
          )}
        </aside>
      </div>
    </section>
  );
}

/** „4 frühere Zahlungen auf Restaurant & Lieferdienst umgebucht.“ (phase 6 D160): the earlier
 * lines of the payee the answer re-booked before its rule was learned. */
function rebookedText(saved: TransactionAllocations): string {
  const category = saved.rule?.category ?? saved.allocations[0]?.category;
  const label = category ? ALLOCATION_CATEGORY[category] : "dieselbe Kategorie";
  const count = saved.rebooked === 1 ? "1 frühere Zahlung" : `${saved.rebooked} frühere Zahlungen`;
  return `${count} auf ${label} umgebucht.`;
}
