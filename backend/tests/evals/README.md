# Recorded evals (phase 2, D29)

Synthetic German documents (a Kaufvertrag, Darlehensvertrag, Mietvertrag, notary
invoice, Grunderwerbsteuerbescheid, Wirtschaftsplan and PDF Kontoauszug, since phase 3
§9 a Zinsbescheinigung and a Hausgeldabrechnung, since phase 4b step 2 a
Lohnsteuerbescheinigung and a Gehaltsabrechnung, and since phase 4b step 6 an
Einkommensteuerbescheid) with fictional parties, generated
deterministically by `make_docs.py`. Each document's expected classification and field
values sit next to its text there.

The two pay documents print invented identifiers in their real shapes (Identifikationsnummer,
eTIN, Personalnummer, Sozialversicherungsnummer, date of birth, address, IBAN;
`make_docs.FAKE_IDENTIFIERS`). `test_pay_documents_store_no_identifier` replays them with every
value citing its whole page and checks that none of those identifiers survives into the stored
values or quotes (phase 4b D103). Phase 4b step 2 changed both prompts (the classifier offers
the two pay documents, `extract.md` gained the pay-document paragraph); they were recorded
again afterwards.

The Einkommensteuerbescheid (phase 4b D129) prints an invented Steuernummer in both forms
(with slashes and the 13-digit ELSTER form), two Identifikationsnummern, the couple's address
and an IBAN (`make_docs.BESCHEID_IDENTIFIERS`); `test_bescheid_stores_no_identifier` checks
them the same way. Its four income lines (Summe der Einkünfte, Gesamtbetrag der Einkünfte,
Einkommen, zu versteuerndes Einkommen) all differ, so only the right one passes. Step 6
changed both prompts again (the classifier offers the subtype, `extract.md` gained the
Bescheid line); all fourteen were recorded again afterwards.

The document catalogue (phase 2 D130, D131) adds seven documents read by the general reading
or as a Verwaltervertrag: `verwaltervertrag`, a plain letter (`schreiben`), and the pairs the
classifier confuses most easily: `grundbuchauszug` (it cites the Kaufvertrag),
`vorauszahlungsbescheid` (it cites the Einkommensteuerbescheid), `depotauszug` (a securities
account, not the rental account), `nachtrag_mietvertrag` (not the lease) and
`mieterselbstauskunft` (an applicant's payslip attached: a tenant document, not the owners'
payslip). The model writes the title, so a general reading checks the date and the fields its
subtype's group asks (the flat's address, never an applicant's own). The check also requires
`contains_tenant_data` for the tenancy documents and its absence for the owners' own papers.
Both prompts changed, and all twenty-one were recorded again on 10 Nov 2026.

- `test_recorded_evals.py` runs in CI on every push. It replays every recording in
  `recordings/` through the same code the `document_read` job uses (SDK message conversion,
  `parse_classification`, `rules.extraction_parse.parse_lines`, the chunk merge) and checks
  each expected field, that it carries a page and quote, and that fields the document does
  not state come back empty. Once the proposals chunk lands it also runs
  `rules.prefill.build_proposal` on the result.
- A recording whose prompt or request no longer matches the code fails as **stale**: a prompt
  or model change must be re-recorded, and its results read, before it merges.
- The harness itself is tested on **synthetic** recordings built from each document's own
  text (`recordings.synthetic_recording`, `"synthetic": true`). They are built in memory,
  never written to `recordings/`, and are not API output.

## Recording (Luis, once per prompt or model change; about $0.25–0.50 for all twenty-one)

Needs the `hausbuch-evals` key (deploy/scaleway.md step 13), from your computer:

```bash
cd backend
HAUSBUCH_ANTHROPIC_API_KEY=sk-ant-... uv run python tests/evals/record.py
uv run pytest tests/evals -v
git add tests/evals/recordings && git commit -m "Evals: record <prompt change>"
```

`record.py rechnung_notar kaufvertrag` records only those documents. Only the synthetic
documents are sent, never real ones; the key goes into the SDK client only and is not
written anywhere. A field that comes back wrong is a prompt problem to fix and record
again, not an expectation to edit.

To look at the PDFs: `uv run python tests/evals/make_docs.py /some/dir`.

Luis's real documents never enter the repository: they live in `HAUSBUCH_EVAL_DIR` on his
computer and are run by hand before a prompt or model change (`hausbuch evals run`, below).

The requests are built by `hausbuch.evals.reading`, the same code `hausbuch evals run` uses;
`tests/db/test_evals_reading_matches_job.py` checks that the `document_read` job sends exactly
those requests.

## Probing the request shapes (D50; before a deploy that changes a request or a model)

CI talks to a fake model client that accepts any request body, so a body the real API refuses
is only found in production. That happened on 8 Nov 2026: Sonnet 5.5 and Opus 5.5 answered
every categorize request with a 400 because it turned thinking off with
`{"type": "disabled"}`, and every unrecognised bank line stayed "Nicht zugeordnet". The probe
sends one request of each kind the app makes (categorize on the normal and on the hard model,
explain, classify of one synthetic PDF), built by the app's own builders from synthetic
inputs, with the `hausbuch-evals` key:

```bash
cd backend
uv run --env-file ~/.hausbuch-evals.env python tests/evals/probe_requests.py
```

`~/.hausbuch-evals.env` holds one line, `HAUSBUCH_ANTHROPIC_API_KEY=sk-ant-...`. Four small
calls, about $0.02. Per request it prints the model, the HTTP status of a refusal (with the
API's own message) or the stop reason, the output tokens and whether the app's parser accepts
the answer; it exits with 0 only when all four were answered and parsed. The key is never
printed. Run it after changing a request builder in `hausbuch/ai/requests.py`, a prompt or a
model setting, before the deploy.

## Evals (D41): how well the app reads your documents

Two commands, both run from `backend/` on your computer. Neither is part of the app or CI.

### `hausbuch evals report`: what you accepted in the app

Every time you apply a Vorschlag, the app records per field what you did with the proposed
value: kept it (accepted), changed it (edited), emptied it (cleared), or left the whole record
out (skipped). It records only that decision, never the value, so the record survives the
90-day clean-up of the Vorschläge themselves. The report counts those decisions:

```bash
cd backend
export HAUSBUCH_ADMIN_DATABASE_URL=...   # the owner URL, as for bootstrap
uv run hausbuch evals report
uv run hausbuch evals report --since 2026-11-01        # only proposals applied since then
uv run hausbuch evals report --workspace <workspace id>
```

It uses the same owner URL as `hausbuch bootstrap`, only reads, and never prints the URL. Per
document kind and field you see how often it was proposed and what you did; "accepted %" is
accepted over proposed. The last line is the number that matters: the share of amounts and
dates you accepted unchanged, against the 90 % target, plus the same share without the
records you skipped (a skipped record says nothing about how well the document was read).

### `hausbuch evals run`: your real documents against a checked answer

Put the documents you want to test (PDF, JPEG or PNG) in a folder **outside the repository**,
for example `~/hausbuch-evals/`. The command refuses a folder inside the repository, so a real
document can never be committed by accident. Next to each document sits its "golden file",
the checked answer: for `kaufvertrag.pdf` that is `kaufvertrag.expected.yaml`:

```yaml
kind: purchase_contract
fields:
  notarized_on: '2026-02-12'
  purchase_price: '325000.00'
  build_year: null          # the document does not state it
```

Each run sends every document to the model, the same way the app reads an upload (live, the
`hausbuch-evals` key, about $0.02–0.05 per document; tenant documents in the folder are sent
too), and compares the reading with the golden file:

```bash
export HAUSBUCH_EVAL_DIR=~/hausbuch-evals
export HAUSBUCH_ANTHROPIC_API_KEY=sk-ant-...   # the hausbuch-evals key
uv run hausbuch evals run
uv run hausbuch evals run kaufvertrag            # only some documents
```

Per field it prints `match`, `differs` (both have a value, not the same), `missing` (the
golden file has a value, the reading has none) or `extra` (the reading has a value the golden
file does not), then totals per document and per kind. Fields empty in both are only counted.
A wrong kind is reported for the document and its fields are not compared. By default no value
is printed, so you can paste the output into a chat; add `--show-values` to see them. The
command exits with 0 only when everything matches. Give the files neutral names (the names
are printed).

**Making a golden file the first time:**

1. Run `uv run hausbuch evals run --show-values kaufvertrag` and read the values.
2. `uv run hausbuch evals run --write-expected kaufvertrag` writes
   `kaufvertrag.expected.yaml` from this reading. An existing golden file is never overwritten
   unless you add `--force`.
3. Open the file and check every value against the document; correct any the model got wrong
   by hand. From now on that file is the answer each run is compared with.

Before changing a prompt or the model, run it once on the old version and once on the new one:
a field that turns from `match` into `differs` or `missing` blocks the change.
