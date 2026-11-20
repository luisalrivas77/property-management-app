"use client";

// Dokumente (§9.1, phase 10 D282, mockup 23): the vault by section with a search. Upload by file
// picker or drag and drop; the same bytes a second time are not stored twice (200: "bereits
// vorhanden", D9). The search runs in the server (`GET /documents?q=`, every word, case- and
// umlaut-insensitive over title, kind, file name, issuer, date and amount); the chips count
// that one answer by each document's section, so the counts follow the search. Section and
// search live in the URL (`/documents?bereich=…&q=…`). A tapped document opens in the detail
// panel (D267) with its original, its explanation and its suggestion to „Übernehmen“; typing,
// links, retention and deleting stay on its own page (/documents/<id>).
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { api, ok } from "@/api/client";
import type { DocumentDetail } from "@/api/schemas";
import type { components } from "@/api/types";
import { DetailLayout, DetailPanel, useDetail } from "@/components/DetailPanel";
import { DocumentExplanationBox, documentLabel, StatementImportsNotice, UploadControl, type Uploaded } from "@/components/documents";
import { loadDirectory } from "@/components/entities";
import { DOCUMENT_SUBTYPE, DOCUMENT_TYPE } from "@/components/labels";
import { ProposalSection } from "@/components/proposals";
import { Loading, Notice } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useLocale, useT, type Formatter, type Locale, type Translation } from "@/i18n";
import { DOCUMENT_SUBTYPE_EN, DOCUMENT_TYPE_EN, documents as messages } from "@/i18n/documents";
import { Icon } from "@/lib/icons";

import "./documents.css";

type ListItem = components["schemas"]["DocumentListItem"];
type Section = components["schemas"]["DocumentSection"];
type T = Translation<(typeof messages)["de"]>;

/** The sections in D282's order, each with its German URL word. */
const SECTIONS: { section: Section; slug: string; label: (t: T) => string }[] = [
  { section: "property", slug: "immobilie", label: (t) => t.property },
  { section: "tax", slug: "steuern", label: (t) => t.tax },
  { section: "accounts", slug: "konten", label: (t) => t.accounts },
  { section: "investments", slug: "anlagen", label: (t) => t.investments },
  { section: "other", slug: "sonstiges", label: (t) => t.other },
];

function sectionLabel(section: Section, t: T): string {
  return SECTIONS.find((s) => s.section === section)?.label(t) ?? t.other;
}

/** „Vertrag · Mietvertrag · 12.10.2026“, the kind in the screen's language. */
function kindText(document: ListItem, locale: Locale, f: Formatter, t: T): string {
  const typing = document.typing;
  if (!typing) return t.untyped;
  const parts = [locale === "en" ? DOCUMENT_TYPE_EN[typing.type] : DOCUMENT_TYPE[typing.type]];
  if (typing.subtype) parts.push(locale === "en" ? DOCUMENT_SUBTYPE_EN[typing.subtype] : DOCUMENT_SUBTYPE[typing.subtype]);
  if (typing.document_date) parts.push(f.date(typing.document_date));
  return parts.join(" · ");
}

export default function DocumentsPage() {
  return (
    <Suspense fallback={<p className="muted">…</p>}>
      <Documents />
    </Suspense>
  );
}

function Documents() {
  const t = useT(messages);
  const search = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const urlQuery = search.get("q") ?? "";
  const slug = search.get("bereich");
  const current = SECTIONS.find((s) => s.slug === slug)?.section ?? null;
  const [typed, setTyped] = useState(urlQuery);

  // The URL keeps the open document's hash (the detail panel's state).
  const navigate = useCallback(
    (q: string, section: Section | null) => {
      const params = new URLSearchParams();
      const slugOf = SECTIONS.find((s) => s.section === section)?.slug;
      if (slugOf) params.set("bereich", slugOf);
      if (q.trim()) params.set("q", q.trim());
      const query = params.toString();
      router.replace(`${pathname}${query ? `?${query}` : ""}${window.location.hash}`, { scroll: false });
    },
    [pathname, router],
  );

  // Typing waits a moment before it searches, so each key does not start a request.
  useEffect(() => {
    if (typed.trim() === urlQuery.trim()) return;
    const timer = window.setTimeout(() => navigate(typed, current), 250);
    return () => window.clearTimeout(timer);
  }, [typed, urlQuery, current, navigate]);

  const load = useCallback(() => ok(api.GET("/api/documents", { params: { query: urlQuery ? { q: urlQuery } : {} } })), [urlQuery]);
  const list = useLoad(load);
  const directory = useLoad(loadDirectory);
  const [uploads, setUploads] = useState<Uploaded[]>([]);

  return (
    <section>
      <div className="page-head">
        <h1>{t.title}</h1>
        <span className="spacer" />
        <Link className="button-link secondary" href="/capture">
          <Icon name="camera" size={18} /> {t.photograph}
        </Link>
      </div>
      <UploadControl
        label={t.upload}
        prefill
        onUploaded={(result) => {
          setUploads((previous) => [result, ...previous].slice(0, 5));
          list.reload();
        }}
      />
      {uploads.map(({ document, existed, proposalId, aiUnavailable }) => (
        <Notice key={`${document.id}-${existed}-${proposalId ?? ""}`}>
          {existed ? t.existed : t.uploaded}
          <Link href={`/documents/${document.id}`}>{documentLabel(document)}</Link>
          {proposalId ? t.reading : document.typing ? null : t.typeIt}
          {aiUnavailable ? t.aiOff : null}
        </Notice>
      ))}
      <form
        role="search"
        className="doc-search"
        onSubmit={(event) => {
          event.preventDefault();
          navigate(typed, current);
        }}
      >
        <input
          type="search"
          aria-label={t.search}
          placeholder={t.searchPlaceholder}
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
        />
        {typed ? (
          <button
            type="button"
            className="secondary"
            aria-label={t.clearSearch}
            onClick={() => {
              setTyped("");
              navigate("", current);
            }}
          >
            <Icon name="close" size={18} />
          </button>
        ) : null}
      </form>
      <Loading state={list}>
        {(items) => (
          <DocumentList
            items={items}
            current={current}
            query={urlQuery}
            onSection={(section) => navigate(typed, section)}
            directory={directory.data ?? null}
            onChanged={list.reload}
          />
        )}
      </Loading>
    </section>
  );
}

function DocumentList({
  items,
  current,
  query,
  onSection,
  directory,
  onChanged,
}: {
  items: ListItem[];
  current: Section | null;
  query: string;
  onSection: (section: Section | null) => void;
  directory: Parameters<typeof ProposalSection>[0]["directory"];
  onChanged: () => void;
}) {
  const t = useT(messages);
  const f = useFormat();
  const locale = useLocale();
  const detail = useDetail("document");
  // Newest first (mockup 23); the server lists oldest first.
  const newest = useMemo(() => [...items].reverse(), [items]);
  const counts = useMemo(() => {
    const result = new Map<Section, number>();
    for (const item of items) result.set(item.section, (result.get(item.section) ?? 0) + 1);
    return result;
  }, [items]);
  const shown = current ? newest.filter((item) => item.section === current) : newest;
  const selected = items.find((item) => item.id === detail.id);

  const chips: { section: Section | null; label: string; n: number }[] = [
    { section: null, label: t.all, n: items.length },
    ...SECTIONS.map((s) => ({ section: s.section, label: s.label(t), n: counts.get(s.section) ?? 0 })),
  ];

  const heading = query ? t.hits(shown.length, query) : current ? t.sectionCount(sectionLabel(current, t), shown.length) : t.newestFirst;

  return (
    <DetailLayout
      panel={
        selected ? (
          <DetailPanel title={documentLabel(selected)} backLabel={t.title} onClose={detail.close}>
            <DocumentPanel id={selected.id} directory={directory} onChanged={onChanged} />
          </DetailPanel>
        ) : null
      }
    >
      <ul className="doc-chips" aria-label={t.sections}>
        {chips.map((chip) => (
          <li key={chip.section ?? "all"}>
            <button type="button" className="doc-chip" aria-pressed={chip.section === current} onClick={() => onSection(chip.section)}>
              {chip.label} <span className="doc-chip-n">{f.count(chip.n)}</span>
            </button>
          </li>
        ))}
      </ul>
      <h2>{heading}</h2>
      {shown.length === 0 ? (
        <p className="muted">{items.length === 0 && !query ? t.none : t.noHits}</p>
      ) : (
        <div className="table-wrap">
          <table className="doc-table">
            <thead>
              <tr>
                <th>{t.colDocument}</th>
                <th>{t.colSection}</th>
                <th>{t.colKind}</th>
                <th>{t.colTaxYear}</th>
                <th>{t.colReceived}</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((document) => (
                <tr key={document.id}>
                  <td>
                    <a {...detail.linkProps(document.id)}>{documentLabel(document)}</a>
                    <div className="doc-row-sub">
                      <span className="doc-section-tag">{sectionLabel(document.section, t)}</span>
                      <span>{kindText(document, locale, f, t)}</span>
                    </div>
                  </td>
                  <td className="doc-col-wide">
                    <span className="doc-section-tag">{sectionLabel(document.section, t)}</span>
                  </td>
                  <td className="doc-col-wide">
                    <span className="tag">{kindText(document, locale, f, t)}</span>
                  </td>
                  <td className="doc-col-wide">{document.typing?.tax_year ?? "–"}</td>
                  <td className="doc-col-wide">{f.dateTime(document.received_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {query ? <p className="muted">{t.searchedWhere}</p> : null}
    </DetailLayout>
  );
}

/** The panel: the original, what the document is, its suggestion with „Übernehmen“, and the way
 * to its own page for typing, links, retention and deleting. */
function DocumentPanel({
  id,
  directory,
  onChanged,
}: {
  id: string;
  directory: Parameters<typeof ProposalSection>[0]["directory"];
  onChanged: () => void;
}) {
  const t = useT(messages);
  const f = useFormat();
  const load = useCallback(() => ok(api.GET("/api/documents/{document_id}", { params: { path: { document_id: id } } })), [id]);
  const detail = useLoad(load);
  const [pending, setPending] = useState<boolean | undefined>(undefined);

  return (
    <Loading state={detail}>
      {(data: DocumentDetail) => {
        const base = `/api/documents/${data.document.id}/original`;
        return (
          <div className="stack">
            <p className="doc-panel-meta">
              {data.media_type} · {t.received(f.dateTime(data.document.received_at))}
            </p>
            <div className="row">
              {data.opens_inline ? (
                <a href={`${base}?disposition=inline`} target="_blank" rel="noopener" className="button-link secondary">
                  {t.open}
                </a>
              ) : null}
              <a href={`${base}?disposition=attachment`} className="button-link secondary">
                {t.download}
              </a>
            </div>
            {data.opens_inline ? null : <p className="muted">{t.downloadOnly}</p>}
            <StatementImportsNotice imports={data.statement_imports} />
            {pending === false ? <DocumentExplanationBox explanation={data.explanation} /> : null}
            <ProposalSection
              detail={data}
              directory={directory}
              onApplied={() => {
                detail.reload();
                onChanged();
              }}
              onPendingChange={setPending}
            />
            <dl className="facts">
              <div>
                <dt>{t.file}</dt>
                <dd>{data.document.original_filename ?? "–"}</dd>
              </div>
              <div>
                <dt>{t.taxYear}</dt>
                <dd>{data.document.typing?.tax_year ?? "–"}</dd>
              </div>
            </dl>
            <p className="muted">{t.fullPageHint}</p>
            <Link href={`/documents/${data.document.id}`} className="button-link">
              {t.fullPage} ›
            </Link>
          </div>
        );
      }}
    </Loading>
  );
}
