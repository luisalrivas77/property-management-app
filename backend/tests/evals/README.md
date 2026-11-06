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
computer and are run by hand before a prompt or model change (`hausbuch evals run`, step 5).
