"use client";

// Posteingang-Adresse (docs/phases/phase-5.md §5.2, §11; D121): the token address members
// forward documents to, with a copy button and the three sentences of §5.2. GET
// /inbound-mail/address returns it only once the four HAUSBUCH_INBOX_* values are set; the
// IMAP login and the app password never reach the browser.
import { useState } from "react";

import { api, ok } from "@/api/client";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

const loadAddress = () => ok(api.GET("/api/inbound-mail/address"));

export function InboxAddress() {
  const loaded = useLoad(loadAddress);
  return (
    <Loading state={loaded}>
      {(inbox) =>
        inbox.configured && inbox.address ? (
          <ConfiguredAddress address={inbox.address} label={inbox.label ?? "Hausbuch"} />
        ) : (
          <div className="card stack">
            <p className="muted">
              Der Posteingang per Mail ist noch nicht eingerichtet. Sobald er eingerichtet ist, steht hier die Adresse, an die Sie
              Rechnungen und Kontoauszüge weiterleiten.
            </p>
          </div>
        )
      }
    </Loading>
  );
}

function ConfiguredAddress({ address, label }: { address: string; label: string }) {
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
          Adresse kopieren
        </button>
        {copied === true ? <span>Kopiert.</span> : null}
        {copied === false ? <span>Kopieren nicht möglich; bitte die Adresse markieren.</span> : null}
      </div>
      <p className="muted">
        Nur Anhänge werden übernommen; der Text der Mail wird nicht gespeichert. Die App liest nur das Label {label} und verändert
        nichts im Postfach. Mieterunterlagen besser direkt hochladen, nicht weiterleiten.
      </p>
    </div>
  );
}
