"use client";

// The owner share inputs of an account (phase 6 D157), shared by the Eigentümer card on the
// account page and „Konto anlegen und einlesen“ on a stored statement: one fraction per owner
// that must sum to exactly 1, checked here exactly (whole numbers, no floats) before the API
// checks it again (ownership.sum_not_one).
import type { Owner, OwnershipShare } from "@/api/schemas";

import { collector, Field, type FormState } from "./form";
import { formatFraction } from "./format";
import { toFraction } from "./input";

/** The inputs, one per owner, keyed by owner id; errors under `share-<owner id>` and `shares`.
 * `idPrefix` keeps the element ids unique when several such forms share a page. */
export function OwnerShareInputs({
  owners,
  shares,
  onChange,
  form,
  errorId,
  idPrefix = "",
}: {
  owners: Owner[];
  shares: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
  form: FormState;
  errorId: string;
  idPrefix?: string;
}) {
  const sharesErrors = form.of("shares");
  return (
    <fieldset className="stack" aria-describedby={sharesErrors.length > 0 ? errorId : undefined}>
      <legend>Anteile (als Bruch oder Prozent, z. B. 1/2 oder 50 %; leer = kein Anteil)</legend>
      <div className="grid">
        {owners.map((owner) => (
          <Field
            key={owner.id}
            id={`${idPrefix}share-${owner.id}`}
            label={owner.full_name}
            description="Der Anteil dieser Person am Konto, z. B. 1/2 bei einem gemeinsamen Konto, 1/1 bei einem eigenen. Alle Anteile zusammen müssen genau 1 (100 %) ergeben."
            errors={form.of(`share-${owner.id}`)}
          >
            <input
              type="text"
              value={shares[owner.id] ?? ""}
              onChange={(e) => onChange({ ...shares, [owner.id]: e.target.value })}
              placeholder="1/2"
              autoComplete="off"
            />
          </Field>
        ))}
      </div>
      {sharesErrors.length > 0 ? (
        <ul className="field-errors" id={errorId}>
          {sharesErrors.map((message, index) => (
            <li key={index}>{message}</li>
          ))}
        </ul>
      ) : null}
    </fieldset>
  );
}

/** The inputs to start from: the current shares, else the offer of D157 (1/1 for one owner, 1/2
 * each for two), else empty. */
export function initialShares(current: OwnershipShare[] | null | undefined, owners: Owner[]): Record<string, string> {
  if (current && current.length > 0) {
    return Object.fromEntries(
      owners.map((owner) => {
        const share = current.find((s) => s.owner_id === owner.id);
        return [owner.id, share ? formatFraction({ numerator: share.share_numerator, denominator: share.share_denominator }) : ""];
      }),
    );
  }
  const offer = owners.length === 1 ? "1/1" : owners.length === 2 ? "1/2" : "";
  return Object.fromEntries(owners.map((owner) => [owner.id, offer]));
}

/** The shares typed, parsed into `values` (messages under `share-<owner id>` and `shares`).
 * With `required`, an empty set is refused; otherwise it is allowed and returns []. */
export function collectShares(
  values: ReturnType<typeof collector>,
  owners: Owner[],
  shares: Record<string, string>,
  { reason = null, required = true }: { reason?: string | null; required?: boolean } = {},
): OwnershipShare[] {
  const body: OwnershipShare[] = [];
  for (const owner of owners) {
    const raw = shares[owner.id] ?? "";
    if (raw.trim() === "") continue;
    const share = values.take(`share-${owner.id}`, toFraction(raw));
    if (share) {
      body.push({ owner_id: owner.id, share_numerator: share.numerator, share_denominator: share.denominator, reason });
    }
  }
  if (values.ok && body.length === 0) {
    if (required) values.messages.shares = ["Mindestens einen Anteil eingeben."];
  } else if (values.ok && !sumsToOne(body)) {
    values.messages.shares = ["Die Anteile müssen zusammen genau 1 (100 %) ergeben, z. B. 1/2 und 1/2."];
  }
  return body;
}

/** Σ n/d = 1 exactly, in whole numbers (BigInt), the rule the API applies (D157). */
export function sumsToOne(shares: OwnershipShare[]): boolean {
  let numerator = 0n;
  let denominator = 1n;
  for (const share of shares) {
    const n = BigInt(share.share_numerator);
    const d = BigInt(share.share_denominator);
    numerator = numerator * d + n * denominator;
    denominator = denominator * d;
  }
  return numerator === denominator;
}
