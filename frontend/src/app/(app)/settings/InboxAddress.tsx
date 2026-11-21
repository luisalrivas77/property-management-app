"use client";

// Posteingang-Adresse (docs/phases/phase-5.md §5.2, §11; D121): the token address members
// forward documents to, with a copy button and the three sentences of §5.2. GET
// /inbound-mail/address returns it only once the four inbox values are set (typed under
// Zugangsschlüssel, phase 6 D260, or the HAUSBUCH_INBOX_* variables); the
// IMAP login and the app password never reach the browser.
import { useState } from "react";

import { api, ok } from "@/api/client";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useT } from "@/i18n";
import { settingsForms } from "@/i18n/settingsForms";

const loadAddress = () => ok(api.GET("/api/inbound-mail/address"));

export function InboxAddress() {
  const t = useT(settingsForms);
  const loaded = useLoad(loadAddress);
  return (
    <Loading state={loaded}>
      {(inbox) =>
        inbox.configured && inbox.address ? (
          <ConfiguredAddress address={inbox.address} label={inbox.label ?? "Hausbuch"} />
        ) : (
          <div className="card stack">
            <p className="muted">{t.inboxNotSet}</p>
          </div>
        )
      }
    </Loading>
  );
}

function ConfiguredAddress({ address, label }: { address: string; label: string }) {
  const t = useT(settingsForms);
  const [copied, setCopied] = useState<boolean | null>(null);

  async function copy() {
    try {
      await navigator.clipboard.writeText(address);
      setCopied(true);
    } catch {
      setCopied(false); // e.g. no clipboard permission: the address can still be selected
    }
  }

  return (
    <div className="card stack">
      <p className="one-time-link" data-testid="inbox-address">
        {address}
      </p>
      <div className="row">
        <button type="button" onClick={() => void copy()}>
          {t.copyAddress}
        </button>
        {copied === true ? <span>{t.copied}</span> : null}
        {copied === false ? <span>{t.copyAddressFailed}</span> : null}
      </div>
      <p className="muted">{t.inboxNote(label)}</p>
    </div>
  );
}
