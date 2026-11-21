"use client";

// Hinweis-Modus (docs/phases/phase-9.md §10, §14, D251): the workspace's `advice_mode`, what the
// household plan and its KI explanation on Planung › Haushalt may name. „Eigene Nutzung“
// (`own_use`, the default) lets the explanation name amounts, asset classes and the household's
// own accounts, savings plans and held assets, never a new product, and shows every path to the
// reserve target; „Allgemein“ (`generic`) names amounts and asset classes only and shows the two
// paths „Reserve zuerst“ and „Alles weiter wie bisher“. The key figures, their marks and the
// net-worth projection are the same in both. Saved through PATCH /workspace/settings, which
// writes its own workspace.advice_mode_changed event; the choice shown is the saved one, as for
// the switches (nothing here updates optimistically).
import { useState } from "react";

import { api, ok } from "@/api/client";
import type { WorkspaceSettings } from "@/api/schemas";
import type { components } from "@/api/types";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useT } from "@/i18n";
import { settingsForms } from "@/i18n/settingsForms";
import { ErrorNotice } from "@/lib/ErrorNotice";

type AdviceMode = components["schemas"]["AdviceMode"];

const loadSettings = () => ok(api.GET("/api/workspace/settings"));

export function AdviceModeSettings() {
  const t = useT(settingsForms);
  const modes: { mode: AdviceMode; label: string; text: string }[] = [
    { mode: "own_use", label: t.ownUse, text: t.ownUseText },
    { mode: "generic", label: t.generic, text: t.genericText },
  ];
  const loaded = useLoad(loadSettings);
  const [saved, setSaved] = useState<WorkspaceSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function change(mode: AdviceMode) {
    setBusy(true);
    setError(null);
    try {
      setSaved(await ok(api.PATCH("/api/workspace/settings", { body: { advice_mode: mode } })));
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Loading state={loaded}>
      {(fetched) => {
        const current = (saved ?? fetched).advice_mode;
        const label = modes.find((m) => m.mode === current)?.label ?? current;
        return (
          <fieldset className="card stack">
            <legend>
              <strong>{t.adviceMode}</strong>
            </legend>
            <div className="stack" role="radiogroup" aria-label={t.adviceMode}>
              {modes.map(({ mode, label: name, text }) => (
                <div key={mode}>
                  <label className="inline" htmlFor={`advice-mode-${mode}`}>
                    <input
                      id={`advice-mode-${mode}`}
                      type="radio"
                      name="advice-mode"
                      checked={current === mode}
                      disabled={busy}
                      onChange={() => void change(mode)}
                    />{" "}
                    {name}
                  </label>
                  <p className="muted">{text}</p>
                </div>
              ))}
            </div>
            <p className="muted">{t.adviceSame}</p>
            <p className="muted" data-testid="advice-mode-saved">
              {t.savedAs(label)}
            </p>
            <ErrorNotice error={error} />
          </fieldset>
        );
      }}
    </Loading>
  );
}
