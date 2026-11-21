"use client";

// Mitglieder (§6.7, §10.6): the household's members (GET /auth/members: enrolled or only
// invited), and inviting one (the spouse). POST /auth/invites answers with the enrollment
// link exactly once; it is shown here with a copy button and never again (GET /auth/invites
// lists pending invites without it). The link is a bearer secret until used: hand it over
// directly, not in a group chat.
import { useCallback, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Invite } from "@/api/schemas";
import { useAuth } from "@/auth/AuthGate";
import { Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT } from "@/i18n";
import { settingsForms } from "@/i18n/settingsForms";
import { ErrorNotice } from "@/lib/ErrorNotice";

const loadInvites = () => ok(api.GET("/api/auth/invites"));
const loadMembers = () => ok(api.GET("/api/auth/members"));
const KEYS = ["email", "display_name"] as const;

export function Members() {
  const t = useT(settingsForms);
  const f = useFormat();
  const { me } = useAuth();
  const members = useLoad(loadMembers);
  const invites = useLoad(loadInvites);
  const [created, setCreated] = useState<Invite | null>(null);
  const [error, setError] = useState<unknown>(null);

  const revoke = useCallback(
    async (invite: Invite) => {
      if (!window.confirm(t.revokeQuestion(invite.user.display_name))) return;
      setError(null);
      try {
        await ok(api.DELETE("/api/auth/invites/{invite_id}", { params: { path: { invite_id: invite.id } } }));
        if (created?.id === invite.id) setCreated(null);
        invites.reload();
        members.reload();
      } catch (caught) {
        setError(caught);
      }
    },
    [created, invites, members, t],
  );

  return (
    <div className="stack">
      <p className="muted">{t.membersIntro}</p>
      <Loading state={members}>
        {(list) => (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t.member}</th>
                  <th>{t.email}</th>
                  <th>{t.passkey}</th>
                </tr>
              </thead>
              <tbody>
                {list.map((member) => (
                  <tr key={member.user.id}>
                    <td>
                      {member.user.display_name}
                      {member.user.id === me.user.id ? <span className="muted">{t.you}</span> : null}
                    </td>
                    <td>{member.user.email}</td>
                    <td>{member.has_passkey ? t.passkeySet : t.passkeyNone}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Loading>
      <h3>{t.openInvites}</h3>
      {created?.enrollment_url ? <OneTimeLink invite={created} url={created.enrollment_url} onDismiss={() => setCreated(null)} /> : null}
      <Loading state={invites}>
        {(list) =>
          list.length === 0 ? (
            <p className="muted">{t.noOpenInvites}</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t.invited}</th>
                    <th>{t.email}</th>
                    <th>{t.linkValidUntil}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {list.map((invite) => (
                    <tr key={invite.id}>
                      <td>{invite.user.display_name}</td>
                      <td>{invite.user.email}</td>
                      <td>{f.dateTime(invite.expires_at)}</td>
                      <td>
                        <button type="button" className="danger" onClick={() => void revoke(invite)}>
                          {t.revoke}
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
          members.reload();
        }}
      />
    </div>
  );
}

function InviteForm({ onCreated }: { onCreated: (invite: Invite) => void }) {
  const t = useT(settingsForms);
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
      <strong>{t.inviteMember}</strong>
      <div className="grid">
        <Field id="invite-name" label={t.name} errors={form.of("display_name")}>
          <input type="text" value={displayName} onChange={(e) => setDisplayName(e.target.value)} required autoComplete="off" />
        </Field>
        <Field id="invite-email" label={t.emailLogin} errors={form.of("email")}>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="off" />
        </Field>
      </div>
      <p className="muted">{t.inviteHint}</p>
      <Submit busy={busy} busyLabel={t.inviting}>
        {t.invite}
      </Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}

function OneTimeLink({ invite, url, onDismiss }: { invite: Invite; url: string; onDismiss: () => void }) {
  const t = useT(settingsForms);
  const f = useFormat();
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
        {t.linkFor(invite.user.display_name, f.dateTime(invite.expires_at))}
        <strong>{t.onlyNow}</strong> {t.linkOnce}
      </p>
      <p className="one-time-link" data-testid="enrollment-url">
        {url}
      </p>
      <div className="row">
        <button type="button" onClick={() => void copy()}>
          {t.copyLink}
        </button>
        <button type="button" className="secondary" onClick={onDismiss}>
          {t.hide}
        </button>
        {copied === true ? <span>{t.copied}</span> : null}
        {copied === false ? <span>{t.copyLinkFailed}</span> : null}
      </div>
    </div>
  );
}
