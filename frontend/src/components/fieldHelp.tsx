"use client";

// The field help of Steuern › „Eure Angaben“ (phase 10 §7.8 (b) item 3, D294): every field says
// what it is and where to find it under its label, its example and source behind ⓘ in the side
// panel (computer) or the panel sliding in from the right (phone, D267), and a field the help
// marks rare goes behind „Weitere Angaben (selten nötig)“.
//
// FieldHelpProvider loads GET /api/tax/{year}/field-help once per screen and holds the panel;
// the forms below it (questionnaire.tsx's ItemForm, returnInputs.tsx's InputForm, angaben.tsx)
// read it with useFieldHelp. Without a provider (the forms elsewhere in the app) nothing changes:
// no help line, no rare section. The texts come from the server in German and English; the
// app's language picks one.
import { createContext, useCallback, useContext, type ReactNode } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { useLocale, useT } from "@/i18n";
import { angaben as messages } from "@/i18n/angaben";

import { DetailLayout, DetailPanel, useDetail, type Detail } from "./DetailPanel";
import { useLoad } from "./useLoad";

type FieldHelp = components["schemas"]["FieldHelp"];
type FieldHelpEntry = components["schemas"]["FieldHelpEntry"];

/** The panel kind: `#d-feldhilfe-<form>.<field>`. */
const PANEL_KIND = "feldhilfe";

interface HelpContext {
  help: FieldHelp | null;
  detail: Detail;
}

const Context = createContext<HelpContext | null>(null);

export interface ShownHelp {
  what: string;
  where: string;
  example: string;
  rare: boolean;
  source: string | null;
}

function shown(help: FieldHelp, entry: FieldHelpEntry, english: boolean): ShownHelp {
  const text = english ? entry.en : entry.de;
  const source = help.sources[entry.source];
  return {
    what: text.what,
    where: text.where,
    example: text.example,
    rare: entry.rare,
    source: source ? (english ? source.en : source.de) : null,
  };
}

/** The help of one field, or null (no provider, not loaded yet, or no entry). */
export function useFieldHelp(form: string, field: string): ShownHelp | null {
  const context = useContext(Context);
  const english = useLocale() === "en";
  const entry = context?.help?.forms[form]?.[field];
  return context?.help && entry ? shown(context.help, entry, english) : null;
}

/** Whether the help marks this field rare (behind „Weitere Angaben“); false without help. */
export function useRareFields(form: string): (field: string) => boolean {
  const context = useContext(Context);
  const fields = context?.help?.forms[form];
  return (field) => fields?.[field]?.rare ?? false;
}

/** The line under a field's label: what it is, where to find it, and ⓘ for the example. */
export function FieldHelpNote({ form, field, label }: { form: string; field: string; label: string }) {
  const context = useContext(Context);
  const help = useFieldHelp(form, field);
  const t = useT(messages);
  if (!context || !help) return null;
  return (
    <span className="field-help">
      <span>{help.what}</span> <span className="muted">{help.where}</span>{" "}
      <a {...context.detail.linkProps(`${form}.${field}`)} className="field-help-info" aria-label={t.helpOpen(label)}>
        ⓘ
      </a>
    </span>
  );
}

/** „Weitere Angaben (selten nötig)“: the rare fields of a form, folded. */
export function RareFields({ children }: { children: ReactNode }) {
  const t = useT(messages);
  return (
    <details className="field-rare">
      <summary>{t.moreFields}</summary>
      <div className="grid">{children}</div>
    </details>
  );
}

const loadHelp = (year: number) =>
  ok(api.GET("/api/tax/{tax_year}/field-help", { params: { path: { tax_year: year } } }));

/** The screen's field help and its panel. A failed load leaves the forms without help. */
export function FieldHelpProvider({ year, children }: { year: number; children: ReactNode }) {
  const load = useCallback(() => loadHelp(year), [year]);
  const state = useLoad(load);
  const detail = useDetail(PANEL_KIND);
  const t = useT(messages);
  const english = useLocale() === "en";
  const help = state.data ?? null;
  const open = detail.id && help ? detail.id.split(".") : null;
  const entry = open && help ? help.forms[open[0] ?? ""]?.[open[1] ?? ""] : undefined;
  const text = help && entry ? shown(help, entry, english) : null;
  return (
    <Context.Provider value={{ help, detail }}>
      <DetailLayout
        panel={
          text ? (
            <DetailPanel title={text.what} backLabel={t.title} onClose={detail.close}>
              <dl className="stack">
                <div>
                  <dt>{t.helpWhere}</dt>
                  <dd>{text.where}</dd>
                </div>
                <div>
                  <dt>{t.helpExample}</dt>
                  <dd>{text.example}</dd>
                </div>
                {text.source ? (
                  <div>
                    <dt>{t.helpSource}</dt>
                    <dd>{text.source}</dd>
                  </div>
                ) : null}
              </dl>
            </DetailPanel>
          ) : null
        }
      >
        {children}
      </DetailLayout>
    </Context.Provider>
  );
}
