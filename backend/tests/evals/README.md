# Recorded evals (phase 2, D29)

Seven synthetic German documents (a Kaufvertrag, Darlehensvertrag, Mietvertrag, notary
invoice, Grunderwerbsteuerbescheid, Wirtschaftsplan and PDF Kontoauszug) with fictional
parties, generated deterministically by `make_docs.py`. Each document's expected
classification and field values sit next to its text there.

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

## Recording (Luis, once per prompt or model change; about $0.10)

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

## Evals (D41): how well the app reads your documents

Two commands, both run from `backend/` on your computer. Neither is part of the app or CI.

### `hausbuch evals report`: what you accepted in the app

Every time you apply a Vorschlag, the app records per field what you did with the proposed
value: kept it (accepted), changed it (edited), emptied it (cleared), or left the whole record
out (skipped). It records only that decision, never the value, so the record survives the
90-day clean-up of the Vorschläge themselves. The report counts those decisions:

```bash
cd backend
HAUSBUCH_ADMIN_DATABASE_URL=... uv run hausbuch evals report
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
HAUSBUCH_ANTHROPIC_API_KEY=sk-ant-... uv run hausbuch evals run
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
