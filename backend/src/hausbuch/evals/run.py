"""`hausbuch evals run`: Luis's real documents through the live model, against golden files
(D29, D41).

For each PDF, JPEG or PNG in HAUSBUCH_EVAL_DIR (or --dir), the classify and extract requests
are built exactly as the document_read job builds them (hausbuch.evals.reading), sent with
HAUSBUCH_ANTHROPIC_API_KEY (the `hausbuch-evals` key), and parsed the same way. The reading is
compared with `<name>.expected.yaml` beside the document:

    kind: purchase_contract
    fields:
      notarized_on: '2026-02-12'
      purchase_price: '325000.00'
      build_year: null            # the document does not state it

Per field: match, differs (both stated, not equal), missing (expected, not read) or extra
(read, not expected); fields empty in both are only counted. A wrong kind is reported for the
document and its fields are not compared. Values are printed only with --show-values, so the
default output can be pasted into a chat. --write-expected writes the golden file from this
reading (for Luis to check and correct by hand); an existing one only with --force.

Never inside the repository: a directory in it is refused, so a real document or golden file
can never be committed. The key goes into the SDK client only; no output or file names it.
No database, no ai_call rows: this is Luis's computer, not the app.
"""

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, TextIO

import hausbuch
from hausbuch.ai import documents
from hausbuch.ai.client import AnthropicModelClient, ModelCallError, ModelClient, ModelResponse
from hausbuch.ai.requests import parse_classification
from hausbuch.rules.extraction_parse import extraction_kind
from hausbuch.schemas.ai import ExtractedValue
from hausbuch.schemas.enums import ExtractionKind
from hausbuch.schemas.extraction import EXTRACTION_MODELS, ValueKind, value_kind
from hausbuch.settings import Settings, SettingsError

from .reading import (
    chunk_values,
    classify_request_for,
    eval_settings,
    extract_requests_for,
    merged_values,
)

MEDIA_TYPES = {
    ".pdf": documents.PDF,
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
GOLDEN_SUFFIX = ".expected.yaml"
_STOPS = frozenset({"refusal", "max_tokens"})  # the job's refusal / truncation (§5.3)
# AI settings an eval run takes from the environment, like the app (defaults otherwise).
_ENV_SETTINGS = {
    "HAUSBUCH_AI_MODEL_CLASSIFY": ("ai_model_classify", str),
    "HAUSBUCH_AI_MODEL_EXTRACT": ("ai_model_extract", str),
    "HAUSBUCH_AI_INFERENCE_GEO": ("ai_inference_geo", str),
    "HAUSBUCH_AI_MAX_PAGES": ("ai_max_pages", int),
    "HAUSBUCH_AI_TIMEOUT_S": ("ai_timeout_s", int),
}

MATCH, DIFFERS, MISSING, EXTRA = "match", "differs", "missing", "extra"
STATES = (MATCH, DIFFERS, MISSING, EXTRA)


class EvalRunError(Exception):
    """A refusal before anything is sent; printed without a traceback."""


# --- Reading one document -------------------------------------------------------------


@dataclass
class Reading:
    kind: ExtractionKind | None = None
    values: dict[str, ExtractedValue] = field(default_factory=dict)
    error: str | None = None  # an error code, never a message


def read_document(client: ModelClient, settings: Settings, data: bytes, media_type: str) -> Reading:
    """Classify, then extract the classified kind, as document_read does. No retry: a failed
    call or an unusable answer is reported with its code."""
    try:
        classify = classify_request_for(settings, data, media_type)
        response = _send(client, classify)
        if response is None:
            return Reading(error="ai.refused")
        try:
            classification = parse_classification(response.text)
        except ValueError:
            return Reading(error="ai.unparsable")
        kind = extraction_kind(classification.type, classification.subtype)
        subtype = classification.subtype
        chunks = []
        for offset, request in extract_requests_for(settings, kind, data, media_type, subtype):
            response = _send(client, request)
            if response is None:
                return Reading(kind=kind, error="ai.refused")
            values = chunk_values(kind, response, offset, subtype)
            if not values:
                return Reading(kind=kind, error="ai.unparsable")
            chunks.append(values)
        return Reading(kind=kind, values=merged_values(kind, chunks, subtype))
    except documents.DocumentInputError as exc:
        return Reading(error=exc.code)
    except ModelCallError as exc:  # status and code only; the SDK's message is never used
        status = f" (HTTP {exc.status})" if exc.status else ""
        return Reading(error=f"{exc.error_code}{status}")


def _send(client: ModelClient, request) -> ModelResponse | None:
    response = client.send(request)
    return None if response.stop_reason in _STOPS else response


# --- Golden files and comparing ---------------------------------------------------------


def golden_path(document: Path) -> Path:
    return document.with_name(document.stem + GOLDEN_SUFFIX)


def golden_of(reading: Reading) -> dict[str, Any]:
    """The golden file's content for a reading: the kind and every field (null when empty)."""
    return {
        "kind": None if reading.kind is None else reading.kind.value,
        "fields": {name: value.value for name, value in reading.values.items()},
    }


def _yaml():
    try:
        import yaml
    except ImportError:  # PyYAML is in the dev dependency group (uv sync installs it)
        raise EvalRunError(
            "PyYAML is not installed; run from backend/ after `uv sync` (dev group)"
        ) from None
    return yaml


def load_golden(path: Path) -> dict[str, Any]:
    """Raises ValueError for a file that is not a golden file (never with its content)."""
    yaml = _yaml()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError):
        raise ValueError("not YAML") from None
    if not isinstance(data, dict) or not isinstance(data.get("fields", {}), dict):
        raise ValueError("a golden file is a mapping with `kind` and `fields`")
    return {"kind": data.get("kind"), "fields": data.get("fields") or {}}


def write_golden(path: Path, reading: Reading) -> None:
    text = _yaml().safe_dump(golden_of(reading), allow_unicode=True, sort_keys=False)
    path.write_text(text, encoding="utf-8")


def canonical(value: Any, kind: ValueKind) -> Any:
    """A value in a comparable form: YAML may load a hand-written date as a date, an amount
    as a number; the reading holds the stored (JSON) form."""
    if value is None:
        return None
    if kind in ("money", "decimal", "rate", "fraction"):
        try:
            return Decimal(str(value)).normalize()
        except InvalidOperation:
            return str(value)
    if kind == "date":
        return value.isoformat() if isinstance(value, date) else str(value)
    if kind == "year":
        try:
            return int(value)
        except (TypeError, ValueError):
            return str(value)
    if kind == "text_list":
        return [str(v) for v in value] if isinstance(value, list) else [str(value)]
    return str(value)


@dataclass
class FieldResult:
    name: str
    state: str  # one of STATES
    expected: Any = None
    read: Any = None


@dataclass
class Comparison:
    kind_matches: bool
    fields: list[FieldResult] = field(default_factory=list)
    empty: int = 0  # empty in both
    unknown: list[str] = field(default_factory=list)  # golden fields the kind does not have


def compare(reading: Reading, golden: dict[str, Any]) -> Comparison:
    expected_kind = golden.get("kind")
    read_kind = None if reading.kind is None else reading.kind.value
    if expected_kind != read_kind:
        return Comparison(kind_matches=False)
    result = Comparison(kind_matches=True)
    if reading.kind is None:
        return result
    model = EXTRACTION_MODELS[reading.kind]
    expected_fields: dict[str, Any] = golden["fields"]
    result.unknown = sorted(set(expected_fields) - set(model.model_fields))
    for name in model.model_fields:
        vk = value_kind(model, name)
        expected = canonical(expected_fields.get(name), vk)
        value = reading.values.get(name)
        read = canonical(None if value is None else value.value, vk)
        if expected is None and read is None:
            result.empty += 1
            continue
        if expected is None:
            state = EXTRA
        elif read is None:
            state = MISSING
        else:
            state = MATCH if expected == read else DIFFERS
        result.fields.append(FieldResult(name, state, expected_fields.get(name), _plain(value)))
    return result


def _plain(value: ExtractedValue | None) -> Any:
    return None if value is None else value.value


# --- The run -----------------------------------------------------------------------------


@dataclass
class Totals:
    documents: int = 0
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(STATES, 0))

    def add(self, comparison: Comparison) -> None:
        self.documents += 1
        for result in comparison.fields:
            self.counts[result.state] += 1

    def line(self) -> str:
        compared = sum(self.counts.values())
        share = "" if not compared else f" ({self.counts[MATCH] * 100 / compared:.1f} % match)"
        parts = ", ".join(f"{self.counts[s]} {s}" for s in STATES)
        noun = "document" if self.documents == 1 else "documents"
        return f"{self.documents} {noun}: {parts}{share}"


@dataclass
class RunOptions:
    directory: Path
    names: list[str] = field(default_factory=list)
    show_values: bool = False
    write_expected: bool = False
    force: bool = False


def repo_roots() -> list[Path]:
    """The repository this code runs from, and the one the working directory is in."""
    roots = []
    for start in (Path(hausbuch.__file__).resolve().parent, Path.cwd().resolve()):
        for candidate in (start, *start.parents):
            if (candidate / ".git").exists():
                roots.append(candidate)
                break
    return roots


def check_directory(directory: Path) -> Path:
    resolved = directory.expanduser().resolve()
    if not resolved.is_dir():
        raise EvalRunError("the eval directory does not exist or is not a directory")
    for root in repo_roots():
        if resolved == root or root in resolved.parents:
            raise EvalRunError(
                "the eval directory is inside the repository; real documents and their "
                "golden files must live outside it (D29)"
            )
    return resolved


def documents_in(directory: Path, names: list[str]) -> list[Path]:
    found = sorted(
        p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_TYPES
    )
    if names:
        wanted = set(names)
        found = [p for p in found if p.name in wanted or p.stem in wanted]
        missing = wanted - {p.name for p in found} - {p.stem for p in found}
        if missing:
            raise EvalRunError(f"not in the eval directory: {', '.join(sorted(missing))}")
    return found


def run(
    options: RunOptions, client: ModelClient, settings: Settings, out: TextIO | None = None
) -> int:
    """Read, compare and report every document; 0 when every document has a golden file
    (or got one) and matches it in kind and every field, else 1."""
    out = out or sys.stdout
    directory = check_directory(options.directory)
    paths = documents_in(directory, options.names)
    if not paths:
        print("No PDF, JPEG or PNG documents in the eval directory.", file=out)
        return 1
    stems: dict[str, int] = {}
    for path in paths:
        stems[path.stem] = stems.get(path.stem, 0) + 1

    total, per_kind = Totals(), {}
    errors = without_golden = kind_mismatches = written = 0
    print(f"Evals: {len(paths)} document(s), live ({settings.ai_model_extract})", file=out)
    for path in paths:
        print("", file=out)
        golden = golden_path(path)
        if stems[path.stem] > 1:
            print(f"{path.name}: skipped, another document has the stem {path.stem!r}", file=out)
            errors += 1
            continue
        media_type = MEDIA_TYPES[path.suffix.lower()]
        try:
            reading = read_document(client, settings, path.read_bytes(), media_type)
        except Exception as exc:  # a bug or the SDK: the class only, never a message
            reading = Reading(error=type(exc).__name__)
        kind = "-" if reading.kind is None else reading.kind.value
        if reading.error is not None:
            print(f"{path.name}: not read ({reading.error})", file=out)
            errors += 1
            continue
        if options.write_expected:
            if golden.exists() and not options.force:
                note = f"{golden.name} exists (--force to overwrite)"
                print(f"{path.name}: kind {kind}; {note}", file=out)
                errors += 1
                continue
            write_golden(golden, reading)
            written += 1
            note = f"wrote {golden.name}, check every value in it"
            print(f"{path.name}: kind {kind}; {note}", file=out)
            _print_values(reading, options, out)
            continue
        if not golden.exists():
            without_golden += 1
            print(
                f"{path.name}: kind {kind}; no {golden.name} (check the reading, then "
                "--write-expected)",
                file=out,
            )
            _print_values(reading, options, out)
            continue
        try:
            comparison = compare(reading, load_golden(golden))
        except ValueError:  # a YAML error message quotes the file's content
            print(f"{path.name}: {golden.name} is not a valid golden file", file=out)
            errors += 1
            continue
        if not comparison.kind_matches:
            kind_mismatches += 1
            print(f"{path.name}: kind differs (read {kind}); fields not compared", file=out)
            continue
        per_kind.setdefault(kind, Totals()).add(comparison)
        total.add(comparison)
        _print_comparison(path, kind, comparison, options, out)

    print("", file=out)
    if per_kind:
        print("Per kind:", file=out)
        for kind in sorted(per_kind):
            print(f"  {kind}: {per_kind[kind].line()}", file=out)
    print(f"Total: {total.line()}", file=out)
    notes = [
        f"{n} {label}"
        for n, label in (
            (kind_mismatches, "with the wrong kind"),
            (without_golden, "without a golden file"),
            (errors, "not compared (errors)"),
            (written, "golden file(s) written"),
        )
        if n
    ]
    if notes:
        print("Also: " + "; ".join(notes), file=out)
    clean = (
        not errors
        and not kind_mismatches
        and not without_golden
        and all(total.counts[s] == 0 for s in (DIFFERS, MISSING, EXTRA))
    )
    return 0 if clean else 1


def _print_comparison(
    path: Path, kind: str, comparison: Comparison, options: RunOptions, out: TextIO
) -> None:
    print(f"{path.name}: kind {kind}", file=out)
    for result in comparison.fields:
        line = f"  {result.state:<8} {result.name}"
        if options.show_values and result.state != MATCH:
            line += f"  expected {result.expected!r}  read {result.read!r}"
        elif options.show_values:
            line += f"  {result.read!r}"
        print(line, file=out)
    for name in comparison.unknown:
        print(f"  unknown  {name} (not a field of {kind})", file=out)
    counts = {s: sum(1 for r in comparison.fields if r.state == s) for s in STATES}
    summary = ", ".join(f"{counts[s]} {s}" for s in STATES)
    print(f"  -> {summary}; {comparison.empty} empty in both", file=out)


def _print_values(reading: Reading, options: RunOptions, out: TextIO) -> None:
    if not options.show_values:
        stated = sum(1 for v in reading.values.values() if v.value is not None)
        note = "(--show-values to see them)"
        print(f"  {stated} of {len(reading.values)} fields stated {note}", file=out)
        return
    for name, value in reading.values.items():
        print(f"  {name}: {value.value!r}", file=out)


# --- CLI ---------------------------------------------------------------------------------


def settings_from(environ: Mapping[str, str]) -> Settings:
    overrides: dict[str, Any] = {}
    for variable, (name, parse) in _ENV_SETTINGS.items():
        raw = (environ.get(variable) or "").strip()
        if raw:
            try:
                overrides[name] = parse(raw)
            except ValueError:
                raise EvalRunError(f"{variable} is not valid") from None
    try:
        return eval_settings(**overrides)
    except (SettingsError, ValueError):
        raise EvalRunError("the HAUSBUCH_AI_* settings are not valid") from None


def make_client(api_key: str, settings: Settings) -> ModelClient:
    """The live client (replaced by a fake in tests)."""
    return AnthropicModelClient(api_key, timeout_s=settings.ai_timeout_s)


def main(
    args: argparse.Namespace,
    environ: Mapping[str, str] = os.environ,
    *,
    client_factory: Callable[[str, Settings], ModelClient] | None = None,
) -> int:
    from hausbuch.cli import CliError

    try:
        directory = args.dir or (environ.get("HAUSBUCH_EVAL_DIR") or "").strip()
        if not directory:
            raise EvalRunError("set HAUSBUCH_EVAL_DIR or pass --dir")
        if args.force and not args.write_expected:
            raise EvalRunError("--force only goes with --write-expected")
        options = RunOptions(
            directory=Path(directory),
            names=list(args.names),
            show_values=args.show_values,
            write_expected=args.write_expected,
            force=args.force,
        )
        check_directory(options.directory)  # before the key: refuse a repo dir first
        settings = settings_from(environ)
        key = (environ.get("HAUSBUCH_ANTHROPIC_API_KEY") or "").strip()
        if not key:
            raise EvalRunError("HAUSBUCH_ANTHROPIC_API_KEY is not set (use the hausbuch-evals key)")
        client = (client_factory or make_client)(key, settings)
        return run(options, client, settings)
    except EvalRunError as exc:
        raise CliError(str(exc)) from None
