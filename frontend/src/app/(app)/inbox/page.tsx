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
//
// Phase 10 §7.6: the page head holds „Hochladen“ and „Foto“, each the label of a hidden file
// input. „Hochladen“ stores each file as a document that the AI reads; „Foto“ opens the phone's
// camera at once (a computer's file picker), and the photos open the page strip of
// components/capture.tsx in the detail panel. The strip lives in this page, so closing the panel
// keeps the photos; „Fotos (n)“ opens it again. /capture keeps its own page. The open panel is
// the URL hash `#d-fotos-seiten` like every other panel (D267, §7.6 (f)), so the phone's back
// button closes it; the photos live in the page only, so a reload opens it with an empty strip and
// its „Seite hinzufügen“.
import "./inbox.css";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useId, useRef, useState, type ChangeEvent } from "react";

import { AllocationPanel, type TransactionAllocations } from "@/components/allocations";
import { DetailLayout, DetailPanel, useDetail } from "@/components/DetailPanel";
import { CameraIcon, PageStripCard, PhotoInput, usePageStrip } from "@/components/capture";
import { documentLabel, storeAsDocument, UPLOAD_ACCEPT, type Uploaded } from "@/components/documents";
import { api, ok } from "@/api/client";
import { InboxList, isLineItem, loadInbox, privateAccount, type InboxItem } from "@/components/inbox";
import { ALLOCATION_CATEGORY } from "@/components/labels";
import { Loading, Notice } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { ErrorNotice } from "@/lib/ErrorNotice";
import { useT, type Translation } from "@/i18n";
import { common } from "@/i18n/common";
import { inbox as messages } from "@/i18n/inbox";
import { notifyInboxChanged, resetInbox } from "@/lib/inboxCount";

type T = Translation<(typeof messages)["de"]>;

/** What the notice after an upload names: the document, whether it was there already, and
 * whether the AI reads it. */
type UploadNote = Pick<Uploaded, "document" | "existed" | "proposalId" | "aiUnavailable">;

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

/** The photos' panel has one id: the strip of this page view (`#d-fotos-seiten`). */
const PHOTOS = "seiten";

function Inbox() {
  const t = useT(messages);
  const router = useRouter();
  const search = useSearchParams();
  const detail = useDetail("umsatz");
  const photos = useDetail("fotos");
  const linked = search.get("umsatz");
  const selected = detail.id ?? linked;
  const inbox = useLoad(loadInbox);
  const [saved, setSaved] = useState<TransactionAllocations | null>(null);
  const [uploads, setUploads] = useState<UploadNote[]>([]);
  const strip = usePageStrip();
  const photosOpen = photos.id === PHOTOS;
  const [photoError, setPhotoError] = useState<unknown>(null);

  // The sidebar's count follows what this page lists, after every load and reload.
  const items = inbox.data?.items;
  useEffect(() => {
    if (items) notifyInboxChanged(items.length);
  }, [items]);

  function select(transactionId: string) {
    setSaved(null);
    detail.open(transactionId); // replaces the photos' hash, so their panel closes
  }

  function noteUpload(note: UploadNote) {
    setUploads((current) => [note, ...current].slice(0, 5));
    inbox.reload();
  }

  function addPhotos(files: File[]) {
    if (files.length === 0) return;
    setPhotoError(null);
    photos.open(PHOTOS);
    void strip.addPages(files);
  }

  /** „Als ein Dokument hochladen“: the strip as one PDF; it stays when the upload fails. */
  async function uploadPhotos() {
    if (strip.pages.length === 0) return;
    setPhotoError(null);
    try {
      const { document, existed, proposalId } = await strip.upload();
      // Only the photos' own panel: a line opened meanwhile keeps its panel.
      if (window.location.hash === photos.href(PHOTOS)) photos.close();
      noteUpload({ document, existed, proposalId, aiUnavailable: false });
    } catch (caught) {
      setPhotoError(caught);
    }
  }

  /**
   * Closes the panel. A page another screen opened with ?umsatz= is replaced by /inbox, which
   * drops the hash too; closeDetail's history.back() would race the replace and bring the
   * linked line back.
   */
  function close() {
    if (linked) router.replace("/inbox", { scroll: false });
    else detail.close();
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
    // Forget the menu's kept list at once: the page announces its new count only when its reload
    // lands, which never happens if the person leaves first.
    resetInbox();
    inbox.reload();
  }

  const item = inbox.data?.items.find((entry) => isLineItem(entry) && entry.transaction_id === selected);

  // The line's private account (phase 6 D158) is named in its panel, no longer in the list.
  const account = selected && inbox.data
    ? privateAccount(inbox.data.accounts, item?.account_id ?? inbox.data.transactions.get(selected)?.account_id)
    : undefined;

  const panel = photosOpen ? (
    <DetailPanel key="photos" title={t.photos} backLabel={t.title} onClose={photos.close}>
      <div className="inbox-photos">
        <PhotoInput
          className={["inbox-head-button", "secondary", strip.busy || strip.full ? "busy" : ""].filter(Boolean).join(" ")}
          disabled={strip.busy || strip.full}
          onFiles={(files) => void strip.addPages(files)}
        >
          <CameraIcon />
          {strip.preparing ? t.preparing : t.addPage}
        </PhotoInput>
        {strip.notice ? (
          <div className="notice notice-warn" role="alert">
            <p>{strip.notice}</p>
          </div>
        ) : null}
        <PageStripCard strip={strip} onUpload={() => void uploadPhotos()} />
        <ErrorNotice error={photoError} />
      </div>
    </DetailPanel>
  ) : selected ? (
    <DetailPanel key="umsatz" title={t.selectedPayment} backLabel={t.title} onClose={close}>
      {account ? <p className="muted">{t.privateAccount(account.label)}</p> : null}
      <AllocationPanel key={selected} transactionId={selected} reasoning={item?.reasoning} question={item?.question} onSaved={onSaved} />
    </DetailPanel>
  ) : null;

  return (
    <section>
      <p className="eyebrow">{t.title}</p>
      <div className="page-head">
        <h1>{t.heading}</h1>
        <div className="inbox-head-actions">
          {strip.pages.length > 0 && !photosOpen ? (
            <button type="button" className="secondary" onClick={() => photos.open(PHOTOS)}>
              {t.photosWaiting(strip.pages.length)}
            </button>
          ) : null}
          <HeadUpload t={t} onUploaded={noteUpload} />
          <PhotoInput className="inbox-head-button" disabled={strip.busy || strip.full} onFiles={addPhotos}>
            <CameraIcon />
            {t.photoButton}
          </PhotoInput>
        </div>
      </div>

      <div className="stack inbox-head-notices">
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

/** „Hochladen“ in the page head (phase 10 §7.6): the label of a hidden file input. Each file is
 * stored as a document that the AI reads (storeAsDocument), as „Dokumente hochladen“ does; a
 * bank statement is imported at its account from there (phase 6 D165 (a), D176 (3)). */
function HeadUpload({ t, onUploaded }: { t: T; onUploaded: (note: UploadNote) => void }) {
  const inputId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function upload(files: File[]) {
    if (files.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      for (const file of files) onUploaded(await storeAsDocument(file));
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <input
        id={inputId}
        className="visually-hidden capture-input"
        type="file"
        accept={UPLOAD_ACCEPT}
        multiple
        disabled={busy}
        onChange={(event: ChangeEvent<HTMLInputElement>) => {
          const files = Array.from(event.target.files ?? []);
          event.target.value = "";
          void upload(files);
        }}
      />
      <label htmlFor={inputId} className={busy ? "inbox-head-button secondary busy" : "inbox-head-button secondary"}>
        {busy ? t.uploadingButton : t.uploadButton}
      </label>
      {error != null ? (
        <div className="inbox-head-error">
          <ErrorNotice error={error} />
        </div>
      ) : null}
    </>
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
