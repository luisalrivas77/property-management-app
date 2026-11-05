"use client";

// Mitglieder (§6.7, §10.6): invite a household member (the spouse). POST /auth/invites
// answers with the enrollment link exactly once; it is shown here with a copy button and
// never again (GET /auth/invites lists pending invites without it). The link is a bearer
// secret until used: hand it over directly, not in a group chat.
import { useCallback, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Invite } from "@/api/schemas";
import { Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { formatDateTime } from "@/components/format";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { ErrorNotice } from "@/lib/ErrorNotice";

const loadInvites = () => ok(api.GET("/api/auth/invites"));
const KEYS = ["email", "display_name"] as const;

export function Members() {
  const invites = useLoad(loadInvites);
  const [created, setCreated] = useState<Invite | null>(null);
  const [error, setError] = useState<unknown>(null);

  const revoke = useCallback(
    async (invite: Invite) => {
      if (!window.confirm(`Einladung für ${invite.user.display_name} zurückziehen? Der Link funktioniert dann nicht mehr.`)) return;
      setError(null);
      try {
        await ok(api.DELETE("/api/auth/invites/{invite_id}", { params: { path: { invite_id: invite.id } } }));
        if (created?.id === invite.id) setCreated(null);
        invites.reload();
      } catch (caught) {
        setError(caught);
      }
    },
    [created, invites],
  );

  return (
    <div className="stack">
      <p className="muted">
        Beide im Haushalt sehen und bearbeiten dasselbe; das Protokoll hält fest, wer was eingetragen hat.
      </p>
      {created?.enrollment_url ? <OneTimeLink invite={created} url={created.enrollment_url} onDismiss={() => setCreated(null)} /> : null}
      <Loading state={invites}>
        {(list) =>
          list.length === 0 ? (
            <p className="muted">Keine offenen Einladungen.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Eingeladen</th>
                    <th>E-Mail</th>
                    <th>Link gültig bis</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {list.map((invite) => (
                    <tr key={invite.id}>
                      <td>{invite.user.display_name}</td>
                      <td>{invite.user.email}</td>
                      <td>{formatDateTime(invite.expires_at)}</td>
                      <td>
                        <button type="button" className="danger" onClick={() => void revoke(invite)}>
                          Zurückziehen
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Loading>
      <ErrorNotice error={error} />
      <InviteForm
        onCreated={(invite) => {
          setCreated(invite);
          invites.reload();
        }}
      />
    </div>
  );
}

function InviteForm({ onCreated }: { onCreated: (invite: Invite) => void }) {
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    form.clear();
    try {
      const invite = await ok(api.POST("/api/auth/invites", { body: { email: email.trim(), display_name: displayName.trim() } }));
      setEmail("");
      setDisplayName("");
      onCreated(invite);
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit} noValidate>
      <strong>Mitglied einladen</strong>
      <div className="grid">
        <Field id="invite-name" label="Name" errors={form.of("display_name")}>
          <input type="text" value={displayName} onChange={(e) => setDisplayName(e.target.value)} required autoComplete="off" />
        </Field>
        <Field id="invite-email" label="E-Mail (Anmeldename)" errors={form.of("email")}>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="off" />
        </Field>
      </div>
      <p className="muted">Es wird keine E-Mail verschickt: der Einladungslink erscheint hier, einmalig, zum Weitergeben.</p>
      <Submit busy={busy} busyLabel="Lädt ein …">
        Einladen
      </Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}

function OneTimeLink({ invite, url, onDismiss }: { invite: Invite; url: string; onDismiss: () => void }) {
  const [copied, setCopied] = useState<boolean | null>(null);

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      setCopied(false); // e.g. no clipboard permission: the link can still be selected
    }
  }

  return (
    <div className="notice notice-ok stack" role="status">
      <p>
        Einladungslink für {invite.user.display_name}, gültig bis {formatDateTime(invite.expires_at)}. Er wird <strong>nur jetzt</strong>{" "}
        angezeigt und funktioniert einmal.
      </p>
      <p className="one-time-link" data-testid="enrollment-url">
        {url}
      </p>
      <div className="row">
        <button type="button" onClick={() => void copy()}>
          Link kopieren
        </button>
        <button type="button" className="secondary" onClick={onDismiss}>
          Ausblenden
        </button>
        {copied === true ? <span>Kopiert.</span> : null}
        {copied === false ? <span>Kopieren nicht möglich; bitte den Link markieren.</span> : null}
      </div>
    </div>
  );
}
