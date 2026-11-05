# Phase 1, step 3: HTTP API, passkey sign-in, first screens

Nov 5, 2026 · design by the architect agent; coder chunks are in §11.

**Status (5 Nov 2026): built.** All chunks are done and reviewed:
- 0, 1, 2, 3a and 5 merged to `main` in #6 and #7;
- 3b, 4a, 4b and the §10.8 follow-ups on the working branch for the closing PR.

The six Playwright e2e flows pass against the merged branch. Left for phase 1 step 4, the
first deploy and real data:
- the Q13/Q15/Q16 measurements in `deploy/scaleway.md`;
- the open question in §10.8 item 8.

**Done when** (phase 1): Luis enters his real flat (ETW), its tenancy, its loan and all 2026
paperwork through the browser, and History shows purchase → move-in correctly. This step
delivers the HTTP API over the existing services, passkey sign-in, a provisioning path that
needs no superuser, an S3 store behind `BlobStore`, the minimal Next.js screens, and a
deployable shape for Scaleway.

What exists and is reused unchanged: `hausbuch.schemas.*` (the contract), `hausbuch.services.*`
(plain functions over `CommandContext` / `Connection`), `hausbuch.db.session`
(`create_app_engine`, `workspace_transaction`), `hausbuch.storage` (`BlobStore`,
`LocalBlobStore`), `hausbuch.settings`, migrations `0001`–`0002`.

## 1. Principles check

| Principle | How this step keeps it |
| --- | --- |
| 1 AI explains, code calculates | No model anywhere in this step. The only computed figures (loan schedule, retention dates, rent in force) come from `hausbuch.rules.*` through the services. |
| 2 Every number is traceable | The API returns the schemas verbatim (`ScheduleEntry` carries `interest_days` / `follow_up_days`; `TimelineEntry` carries event ids); no API-side totals. |
| 3 Human-in-the-loop | Not exercised (no pipeline yet); the in-process worker slot (§3.7) is where phase 2's jobs will run. |
| 4 Append-only history | Every mutating endpoint is exactly one service command, which writes state + events in one transaction. The API never writes tables itself. |
| 5 Rules are versioned data | Untouched. |
| 6 Originals immutable | Uploads go through `upload_document`; downloads through `read_original` (SHA-256 verified). The S3 store refuses overwrites (§8). |

Security section of ARCHITECTURE.md: passkeys only (decided 5 Nov 2026), short-lived sessions,
no IBAN or tenant data in logs or error bodies (§3.6, §5), private storage served through the
authenticated API (signed URLs come with the S3 store, §8), DB on a private network (§14).

## 2. Backend layout (new modules)

```
backend/src/hausbuch/
  api/
    app.py            create_app(settings, engine, blob_store, session_resolver, worker=None)
    lifespan.py       startup/shutdown: engine disposal, optional in-process worker (§3.7)   (chunk 1)
    auth_context.py   AuthContext, SessionResolver protocol, current_auth dependency       (chunk 0)
    commands.py       Commands helper: one workspace_transaction per command/query           (chunk 0)
    errors.py         exception handlers -> ErrorResponse; 422 without input               (chunk 1)
    logging.py        request log line, request id, redaction rules                          (chunk 1)
    csrf.py           same-origin check for unsafe methods                                   (chunk 1)
    health.py         GET /healthz (container health check)                                  (chunk 1)
    routes/
      owners.py properties.py units.py tenants.py tenancies.py loans.py
      documents.py history.py                                                                (chunk 1)
      auth.py                                                                                (chunk 2)
  auth/
    webauthn.py       registration + authentication ceremonies (py_webauthn)                 (chunk 2)
    ceremony.py       signed challenge cookie (HMAC)                                          (chunk 2)
    sessions.py       create/resolve/touch/revoke sessions; the SessionResolver               (chunk 2)
    invites.py        enrollment tokens (first user, spouse, recovery)                        (chunk 2)
    lookup.py         auth_lookup_transaction(engine): pre-auth SELECT-only reads            (chunk 2)
    provisioning.py   provision_workspace(owner_engine, ...): workspace + first user         (chunk 2)
  cli.py              hausbuch bootstrap | invite | openapi                                  (chunk 2)
  storage_s3.py       S3BlobStore                                                            (chunk 5)
  schemas/auth.py     Passkey, Session, Invite, Me (contract)                                 (chunk 0)
  schemas/api.py      ErrorResponse, *Patch bodies, action bodies, DocumentDetail            (chunk 0)
backend/migrations/versions/0003_auth_passkeys.py                                            (chunk 0)
frontend/            Next.js 16 app (§9)                                                     (chunks 3a, 3b, 4)
deploy/              Dockerfiles, Scaleway notes (§14)                                        (chunk 4)
```

`hausbuch.api` imports `hausbuch.services` and `hausbuch.auth`; `hausbuch.schemas` imports
neither (it stays the pure contract).

## 3. Request lifecycle

### 3.1 Workspace and actor come from the session, never from the client

```
cookie __Host-hb_session=<token>
  -> current_auth(request) -> AuthContext(workspace_id, user_id, session_id, passkey_id)
  -> Commands(engine, auth, request_id)
  -> cmd.run(create_property, data)      # one workspace_transaction, commit on return
  -> cmd.read(get_property, id)          # one workspace_transaction, read only
```

- `AuthContext` (frozen dataclass, `hausbuch/api/auth_context.py`): `workspace_id: UUID`,
  `user_id: UUID`, `session_id: UUID`, `passkey_id: UUID | None`.
- `current_auth` reads the session cookie, calls `request.app.state.session_resolver.resolve(token)`
  (a `SessionResolver` protocol: `resolve(token: str, *, now: datetime) -> AuthContext | None`),
  and raises 401 `{"error": "unauthenticated"}` when there is no valid session. Chunk 2
  implements the resolver; chunk 1's tests pass a fake one (§10.1). No request parameter, header
  or body field ever names a workspace; the only path ids are entity ids, which RLS scopes.
- `Actor(ActorType.USER, f"user:{user_id}")`: the event log's `actor_id` identifies the person
  (Luis vs his spouse) without naming them.

### 3.2 One transaction per command, correlation ids

`Commands` (`hausbuch/api/commands.py`):

```python
class Commands:
    def __init__(self, engine: Engine, auth: AuthContext, request_id: UUID) -> None: ...
    def run(self, command, /, *args, **kwargs):
        with workspace_transaction(self.engine, self.auth.workspace_id) as conn:
            ctx = CommandContext(conn=conn, workspace_id=self.auth.workspace_id,
                                 actor=self.actor, correlation_id=self.request_id,
                                 now=datetime.now(UTC))
            return command(ctx, *args, **kwargs)
    def read(self, query, /, *args, **kwargs):
        with workspace_transaction(self.engine, self.auth.workspace_id) as conn:
            return query(conn, *args, **kwargs)
```

- Explicit `with`, not a `yield` dependency: rollback on any exception is guaranteed by
  `workspace_transaction` itself and does not depend on FastAPI's dependency-teardown order.
- **One command per mutating endpoint.** A route never calls `run` twice; if a screen needs two
  commands (create property, then set ownership) it makes two requests. The service layer's
  "nothing written on error" guarantee then holds per request.
- `correlation_id` = the request id: `X-Request-Id` from the client if it is a UUID, else a
  fresh `uuid4()`. Echoed in the response header and in every event the request appends, so
  the audit trail and the log line share one id.
- Routes are plain `def` (sync): SQLAlchemy is sync; FastAPI runs them in its threadpool.
  Engine pool: `pool_size=5, max_overflow=5` for requests, plus the worker's share (§3.7).

### 3.3 BlobStore injection

`create_app(settings, engine, blob_store, session_resolver, worker=None)` stores them on
`app.state`. Document routes take `store: BlobStore = Depends(get_blob_store)`. The ASGI entry
point (`hausbuch.api.main:app`) builds them from settings: `HAUSBUCH_BLOB_STORE=local` →
`LocalBlobStore(HAUSBUCH_BLOB_ROOT)`; `=s3` → `S3BlobStore` (§8). Tests pass a
`LocalBlobStore(tmp_path)`.

### 3.4 Money as strings, never floats

- Responses: Pydantic v2 serialises `Decimal` as a JSON string in JSON mode, and FastAPI
  serialises response models in JSON mode, so `"purchase_price": "289000.00"` comes out as a
  string with no extra code. A test asserts it (§10.1).
- Requests: today `Money` accepts a JSON number too. **Spec amendment 2** (chunk 0, see §12):
  the money/rate/share aliases in `schemas/common.py` get a `BeforeValidator` that rejects
  `float` instances ("money must be a decimal string") and a `WithJsonSchema({"type":
  "string", "pattern": ...})`, so the OpenAPI input schema is `string`, the generated TS type
  is `string`, and a client that sends `1400.0` gets a 422 instead of a silently rounded value.
  The YAML fixtures already quote every money value, so fixtures and rule tests are unaffected.
- The frontend never parses money into `number`; form inputs are text, normalised
  (`1.400,00` → `1400.00`) by one helper (§9.5).

### 3.5 Status codes and the error envelope

One response model, `ErrorResponse` (`schemas/api.py`): `error` (stable string), optional
`message`, `detail` (field errors), `codes`, `code`, `at_number`, `entity_type`, `entity_id`,
`result` (`RetentionResult`), `request_id`. The frontend switches on `error` and `codes`.

| Raised | HTTP | `error` | Body extras |
| --- | --- | --- | --- |
| `fastapi.exceptions.RequestValidationError`, `pydantic.ValidationError` (raised inside a service, e.g. `OwnerCreate.model_validate` in `update_owner`) | 422 | `validation` | `detail: [{loc, msg, type}]` only (§3.6) |
| `RuleViolationError` (`codes`), `RuleError` from `hausbuch.rules` (`code`, `at_number`: `iban.*` via `protect_iban`, `loan.*` via `build_schedule`), `InvalidEventError` with a `code` | 422 | `rule_violation` | `codes`, `code`, `at_number`, `message` |
| `ValueError` raised by a service function (e.g. "each owner may hold only one share", "a notice cannot end the tenancy before it is given", `InvalidUploadError`) | 422 | `invalid` | `message` (service messages carry ids and stored dates, never submitted values; the handler only maps `ValueError`s whose traceback originates in `hausbuch.services` or `hausbuch.auth`; any other `ValueError` is a 500) |
| `NotFoundError` and subclasses (`LinkTargetNotFoundError`), `LinkNotFoundError`, `EventNotFoundError`; unknown path id | 404 | `not_found` | `entity_type`, `entity_id` |
| `TenancyOverlapError` | 409 | `conflict` | `code: "tenancy.overlap"` |
| `ConflictError` (other), `DuplicateLinkError`, `InvalidEventError` without a code (concurrent correction) | 409 | `conflict` | `message`, `constraint` when present |
| `RetentionHoldError` | 409 | `retention_hold` | `result` |
| unauthenticated | 401 | `unauthenticated` | |
| failed same-origin check (§6.6) | 403 | `forbidden_origin` | |
| upload over `HAUSBUCH_MAX_UPLOAD_BYTES` (default 25 MiB; see Q13) | 413 | `payload_too_large` | |
| upload media type outside the allow-list | 415 | `unsupported_media_type` | |
| shutting down (§3.7) | 503 | `shutting_down` | `Retry-After` |
| `BlobConflictError`, `BlobNotFoundError` on a registered document, `OriginalIntegrityError`, anything else | 500 | `internal` | `request_id` only; details go to the log |

Successes: `POST` that creates → 201 with the created schema; actions that return the updated
entity (corrections, notice, end, erase, select split, typing) → 200; `DELETE` → 204;
`POST /documents` → 201 when `document.created_at == ctx.now` (a new row), 200 when the bytes
were already in the vault (dedup, D9). Lists → 200 with a JSON array (no pagination in v1).

### 3.6 Hard rule: no input echo in 422s, no tenant data or IBAN in logs

Phase 1 spec amendment 1 explains why `hide_input_in_errors` is not enough: it masks the error's
*text*, not the structured errors FastAPI's default 422 is built from. Concretely:

- FastAPI constructs `RequestValidationError` from `ValidationError.errors()`, whose dicts
  already contain `input`, `ctx` and `url`. The handler therefore **whitelists** `type`, `loc`,
  `msg` per error and drops everything else. For a `pydantic.ValidationError` caught directly,
  it calls `errors(include_input=False, include_url=False)` and still drops `ctx`.
- `msg` is safe: Pydantic's built-in messages name the constraint, not the value, and the
  project's own validators use fixed strings.
- The log line never contains a request body, a query string, or `str(exc)` of a
  `ValidationError` (whose repr echoes input for models without `hide_input_in_errors`). For
  other exceptions it logs `type(exc).__name__` and `str(exc)`; service messages are PII-free by
  construction (tenants.py `_validate_contact` names fields only).
- One log line per request (`hausbuch.api.logging`, JSON to stdout, which is what Scaleway's
  log collector reads): `ts, level, request_id, method, route` (the template,
  `/api/tenants/{tenant_id}`, not the raw path), `status, duration_ms, user_id, workspace_id`.
  Uvicorn's access log is off.
- Guard tests (§10.1): a tenant with a malformed IBAN and an owner with a labelled tax number
  are posted; the test asserts the submitted strings appear nowhere in the response body nor in
  the captured log records.

### 3.7 Process model: the API process also hosts the worker (Scaleway finding 2)

A Scaleway Serverless Container that never listens on port 8080 is killed as failed after
about five minutes, and Serverless Jobs are run-to-completion. So for v1 the job worker runs
**inside the API process**, started and stopped by the FastAPI lifespan, in the one container
that serves 8080, with **min-scale 1** so it is never scaled to zero. Phase 1 has no jobs yet;
this step builds the slot so phase 2 drops the Procrastinate worker in without changing the
process model.

`hausbuch/api/lifespan.py`:

```
startup:   engine ready; if settings.run_worker and app.state.worker: start worker task
                                                      (asyncio task, concurrency = settings.worker_concurrency)
shutdown:  app.state.draining = True                  (new requests -> 503 shutting_down, Retry-After: 2)
           worker.stop(): stop fetching jobs, wait for running jobs up to settings.shutdown_grace_s
           engine.dispose()
```

- `worker` is a small protocol (`start()`, `stop(timeout)`) so `create_app` is testable with a
  fake and phase 1 passes `None`. Phase 2 wires `procrastinate.App.run_worker_async(...)` and
  `app.stop()`; Procrastinate's own schema lives beside ours in the same database; its job table
  is not RLS'd, so every job payload carries `workspace_id` and the job opens its own
  `workspace_transaction` under the app role, exactly like a request.
- **Request lifecycle implications.** Requests and jobs share one engine; pool
  `pool_size = 5 (requests) + worker_concurrency (default 2)`, `max_overflow 5`. Jobs never run
  in the request thread; a request that needs work done enqueues and returns (ARCHITECTURE:
  "requests never wait on a model"). Jobs use the `system`/`ai` actor, requests the `user` actor.
  CPU-bound work in the worker competes with request handling in the same container: keep
  worker concurrency small, and move to a separate worker container once there is a second
  user or a backlog.
- **Graceful shutdown.** Scaleway sends SIGTERM on scale-down or deploy; uvicorn turns it into
  lifespan shutdown and stops accepting connections after in-flight requests finish. The
  worker gets `shutdown_grace_s` (default 20 s, must stay below the platform's kill timeout,
  Q15) to finish the job it is on; a job that overruns is retried by Procrastinate when the
  next instance starts (jobs are idempotent by design: §Ingestion "idempotent imports").
  Uvicorn is started with `--timeout-graceful-shutdown 25`. During the drain window the
  health check still returns 200 (so the platform does not kill early) while new API requests
  get 503; the one container with min-scale 1 means a deploy briefly overlaps old and new
  instances rather than leaving none.
- `GET /healthz` (`health.py`, public, no session): `SELECT 1` on the pool with a 2 s timeout
  → 200 `{"status": "ok"}`, else 503. Used as the container health check. No DB details in the
  body.
- Settings: `HAUSBUCH_RUN_WORKER` (default `true`), `HAUSBUCH_WORKER_CONCURRENCY` (2),
  `HAUSBUCH_SHUTDOWN_GRACE_S` (20).

## 4. API surface

Prefix `/api`. Every endpoint except the ones in §6.3 marked *public* and `/healthz` requires a
session. Request bodies reuse `hausbuch.schemas` `*Create` models verbatim; **writes go to flat
collections (the Create schema already names the parent), reads come from the parent**.
Response models are the existing read schemas. New API-only models live in `schemas/api.py`
and `schemas/auth.py` (chunk 0) and are listed in §4.9.

### 4.1 Owners

| Method, path | Body → response | Service |
| --- | --- | --- |
| `GET /owners` | → `list[Owner]` | `list_owners(conn)` **new** (§12) |
| `POST /owners` | `OwnerCreate` → 201 `Owner` | `create_owner` |
| `GET /owners/{owner_id}` | → `Owner` | `get_owner` |
| `PATCH /owners/{owner_id}` | `OwnerPatch` → `Owner` | `update_owner(**fields_set)` |

Patch semantics for every `*Patch`: a field absent from the JSON is unchanged; `null` clears it
where the service accepts `None`. The route passes only `body.model_fields_set` as keyword
arguments, which maps onto the services' `UNSET` convention without touching them.

### 4.2 Properties (ownership, Nebenkosten, splits, purchase-date corrections)

| Method, path | Body → response | Service |
| --- | --- | --- |
| `GET /properties` | → `list[Property]` | `list_properties(conn)` **new** |
| `POST /properties` | `PropertyCreate` → 201 `Property` | `create_property` |
| `GET /properties/{id}` | → `Property` | `get_property` |
| `PATCH /properties/{id}` | `PropertyPatch` (label, kind, address, build_year) → `Property` | `update_property` |
| `POST /properties/{id}/corrections/notarized-on` | `NotarizedOnCorrection {new_date, purchase_price?}` → `Property` | `correct_notarized_on` |
| `POST /properties/{id}/corrections/purchase-date` | `DateCorrection {new_date}` → `Property` | `correct_purchase_date` |
| `GET /properties/{id}/ownership` | → `PropertyOwnership` (404 if none set) | `get_ownership` |
| `PUT /properties/{id}/ownership` | `OwnershipBody {shares: list[OwnerShare], effective_date?}` → `PropertyOwnership` | `set_ownership` |
| `GET /properties/{id}/acquisition-costs` | → `list[AcquisitionCost]` | `list_acquisition_costs` |
| `POST /acquisition-costs` | `AcquisitionCostCreate` → 201 `AcquisitionCost` | `add_acquisition_cost` |
| `GET /properties/{id}/price-splits` | → `list[PriceSplit]` | `list_price_splits` |
| `POST /price-splits` | `PriceSplitCreate` → 201 `PriceSplit` | `record_price_split` |
| `POST /properties/{id}/price-splits/{split_id}/select` | → `Property` | `select_price_split` |
| `GET /properties/{id}/units` | → `list[Unit]` | `list_units` |
| `GET /properties/{id}/loans` | → `list[Loan]` | `list_loans` |
| `GET /properties/{id}/history?unit_id=` | → `list[TimelineEntry]` | `property_history` |
| `GET /properties/{id}/events` | → `list[Event]` | `audit_trail(property_id=)` |

Ownership is a separate `PUT` rather than part of `POST /properties` so that the property form
stays one command per request (§3.2); the UI chains the two calls.

### 4.3 Units

`POST /units` (`UnitCreate` → 201 `Unit`), `GET /units/{id}`, `PATCH /units/{id}` (`UnitPatch`:
label, floor_area_m2, rooms, mea), `GET /units/{id}/tenancies` → `list[Tenancy]`
(`list_tenancies(conn, unit_id)` **new**).

### 4.4 Tenants

| Method, path | Body → response | Service |
| --- | --- | --- |
| `GET /tenants` | → `list[Tenant]` (erased ones included, flagged by `erased_at`) | `list_tenants(conn)` **new** |
| `POST /tenants` | `TenantCreate` → 201 `Tenant` | `create_tenant(iban_key=settings.iban_hmac_key)` |
| `GET /tenants/{id}` | → `Tenant` | `get_tenant` |
| `PATCH /tenants/{id}` | `TenantPatch` (full_name, email, phone, iban write-only) → `Tenant` | `update_tenant` |
| `POST /tenants/{id}/erase` | `ErasureBody {on_request: bool = false}` → `Tenant`; 409 `retention_hold` | `erase_tenant` |
| `GET /tenants/{id}/documents` | → `list[Document]` | `list_documents(target_type=tenant)` |

`iban` appears only in request bodies; every read shape carries `iban_masked`. `TenantPatch`,
`LoanPatch` and `OwnerPatch` set `hide_input_in_errors=True` like their Create models.

### 4.5 Tenancies (rent schedule, corrections, deposit, notice, move-out)

| Method, path | Body → response | Service |
| --- | --- | --- |
| `POST /tenancies` | `TenancyCreateRequest` (= `TenancyCreate` + `initial_rent: InitialRent \| None`) → 201 `Tenancy` | `create_tenancy` |
| `GET /tenancies/{id}` | → `Tenancy` | `get_tenancy` |
| `POST /tenancies/{id}/corrections/move-in` | `DateCorrection {new_date}` → `Tenancy` | `correct_move_in` |
| `POST /tenancies/{id}/deposit-received` | `DepositReceivedBody {amount, received_on}` → 201 `Event` | `record_deposit_received` |
| `GET /tenancies/{id}/deposits` | → `list[DepositReceipt {event_id, amount, received_on, recorded_at}]` (§10.8 item 7) | `list_events` (the `tenancy.deposit_received` events) |
| `POST /tenancies/{id}/notice` | `NoticeBody {given_by, given_on, ends_on}` → `Tenancy` | `give_notice` |
| `POST /tenancies/{id}/end` | `EndTenancyBody {end_date}` → `Tenancy` | `end_tenancy` |
| `GET /tenancies/{id}/rent-schedule` | → `list[RentScheduleEntry]` (superseded rows included; the UI greys them) | `list_rent_schedule` |
| `GET /tenancies/{id}/rent?at=YYYY-MM-DD` | → `RentScheduleEntry \| null` | `rent_on` |
| `POST /rent-schedule` | `RentScheduleCreate` (reason ≠ `correction`, else 422) → 201 `RentScheduleEntry` | `schedule_rent` |
| `POST /rent-schedule/{row_id}/corrections` | `RentRowCorrection` (effective_from, cold_rent, operating_costs_prepayment, document_id; absent = keep) → 201 `RentScheduleEntry` | `correct_rent_row` |
| `GET /tenancies/{id}/documents` | → `list[Document]` | `list_documents` |

The known gaps in `phase-1.md` (no correction for `tenancy.ended`, `signed_on`, notice,
`loan.disbursed`; no party changes) stay gaps: the API exposes what the services have. Step 4
will meet them on the first typo; they need service work plus a spec note, not API design.

### 4.6 Loans (schedule)

| Method, path | Body → response | Service |
| --- | --- | --- |
| `POST /loans` | `LoanCreateRequest` (= `LoanCreate` + `signed_on: date \| None`) → 201 `Loan` | `create_loan(iban_key=...)` |
| `GET /loans/{id}` | → `Loan` | `get_loan` |
| `PATCH /loans/{id}` | `LoanPatch` (lender, contract_number, nominal_rate, monthly_payment, first_payment_on, fixed_until, follow_up_rate, account_iban) → `Loan` | `update_loan` |
| `GET /loans/{id}/schedule` | → `LoanSchedule` | `loan_schedule` |
| `GET /loans/{id}/documents` | → `list[Document]` | `list_documents` |

`POST /loans` and `PATCH /loans/{id}` call `rules.loan_schedule.build_schedule(terms)` **before**
the command, so a payment that never amortises is refused with 422
`loan.payment_does_not_cover_interest` (`at_number`) instead of being stored and failing on
`GET …/schedule`. That is an API-layer pre-check; the services are unchanged. If a stored loan
still fails (data from before the check), `GET …/schedule` answers 409 `rule_violation`.

### 4.7 Documents (upload, typing, links, original, retention)

| Method, path | Body → response | Service |
| --- | --- | --- |
| `POST /documents` | multipart: `file` (required), `source` (default `upload`) → 201/200 `Document` | `upload_document(store, data, media_type, source, original_filename)` |
| `GET /documents?target_type=&target_id=` | → `list[Document]` | `list_documents` |
| `GET /documents/{id}` | → `DocumentDetail {document, links, extraction, retention, media_type, opens_inline}` | `get_document`, `list_links`, `current_extraction`, `document_retention`, `document_media_type` (one read transaction) |
| `PUT /documents/{id}/typing` | `DocumentTypingBody {typing: DocumentTyping, fields: ManualDocumentFields \| None}` → `Document` | `type_document` |
| `GET /documents/{id}/links` | → `list[DocumentLink]` | `list_links` |
| `POST /documents/{id}/links` | `LinkBody {target_type, target_id}` → 201 `DocumentLink`; 409 duplicate; 404 target | `link_document` |
| `DELETE /documents/{id}/links/{target_type}/{target_id}` | → 204 | `unlink_document` |
| `GET /documents/{id}/original?disposition=inline\|attachment` | → bytes | `read_original` |
| `GET /documents/{id}/retention` | → `RetentionResult` | `document_retention` |

Upload: the route reads the part into memory with a hard cap (413 past
`HAUSBUCH_MAX_UPLOAD_BYTES`); media type = the part's declared content type, lower-cased, must
match the service regex **and** the allow-list `application/pdf, image/jpeg, image/png,
image/heic, image/webp, text/csv, text/plain, application/xml, text/xml, message/rfc822`
(settings-overridable) → otherwise 415. The declared type is trusted after the allow-list;
sniffing is a phase 2 job (OCR needs it anyway).

Download: `Content-Type` from `document_file.media_type`, `X-Content-Type-Options: nosniff`,
`Cache-Control: private, no-store`, `Content-Disposition` with an RFC 5987 `filename*` built
from `original_filename` (fallback `<sha256[:12]>.<ext>`). `disposition=inline` is honoured
only for `application/pdf`, `image/jpeg`, `image/png`, `image/webp`; everything else is always
`attachment` (never render HTML/SVG-like content from the API origin). No separate
"download service": `read_original` needs the `document_file` row and the store, both available.
When the S3 store lands, this endpoint may instead `302` to a 60-second presigned URL (§8).

Retention delete / `document_file.delete` is **not** exposed (no auto-delete job in phase 1,
per the spec's "Out" list). `DELETE /documents/{id}` is deliberately absent.

### 4.8 History and audit trail

`GET /properties/{id}/history?unit_id=` (milestones, corrections collapsed) and
`GET /events?property_id=&entity_type=&entity_id=` → `list[Event]` (every event, seq order).
Both are plain reads of the services; the frontend renders `TimelineEntry.payload` per
`EventType` (§9.4).

### 4.9 New API-only models (`schemas/api.py`, `schemas/auth.py`)

`ErrorResponse`, `FieldError {loc: list[str|int], msg, type}`; `OwnerPatch`, `PropertyPatch`,
`UnitPatch`, `TenantPatch`, `LoanPatch`, `RentRowCorrection`; `NotarizedOnCorrection`,
`DateCorrection`, `OwnershipBody`, `DepositReceivedBody`, `NoticeBody`, `EndTenancyBody`,
`ErasureBody`, `DocumentTypingBody`, `LinkBody`, `DocumentDetail`, `TenancyCreateRequest`,
`LoanCreateRequest`, `DepositReceipt` (§10.8); auth: `Passkey`, `Session`, `Invite`, `InviteCreate`, `Me`, `Member` (§10.8),
`EnrollmentOptionsRequest`, `EnrollmentVerifyRequest`, `LoginVerifyRequest`, `PasskeyLabel`.
`InitialRent` moves from `services/tenancies.py` to `schemas/tenancy.py` (re-exported from the
service module, so nothing else changes) because `TenancyCreateRequest` must not import
services. All are `Schema` subclasses (`extra="forbid"`, frozen).

## 5. OpenAPI and the TypeScript contract

- `hausbuch openapi` (CLI) prints `app.openapi()`; the result is committed as
  `frontend/src/api/openapi.json`, and `frontend/src/api/types.ts` is generated from it by
  `openapi-typescript`. CI fails if either file is stale (`git diff --exit-code` after
  regenerating).
- `openapi-fetch` gives a typed client keyed on paths (`client.POST("/api/properties", {body})`),
  so operation ids do not matter and nothing is hand-typed.
- WebAuthn option/credential payloads are opaque JSON on the wire (`dict[str, JsonValue]`
  in the schemas); the browser library consumes/produces them.

## 6. Passkey sign-in (WebAuthn)

### 6.1 Library

`webauthn` (py_webauthn) **3.0.1** (released 25 Sep 2026; Python ≥ 3.10; depends on
`cryptography`, `cbor2`, `pyasn1`, `pyOpenSSL`). 3.0 added ML-DSA verification and prefers
EdDSA in generated options while still accepting ES256/RS256; the 2.x function names
(`generate_registration_options`, `options_to_json`, `verify_registration_response`,
`generate_authentication_options`, `verify_authentication_response`) are unchanged per the
release notes (the coder should confirm against the installed package's `__init__`). Browser
side: `@simplewebauthn/browser` **14.0.0** (`startRegistration({ optionsJSON })`,
`startAuthentication({ optionsJSON })`, verify the exact signature against the installed
`.d.ts`; simplewebauthn.dev was not reachable from this session). Attestation: `none`; no
attestation verification, no MDS (one household).

Relying party: `HAUSBUCH_RP_ID` (e.g. `hausbuch.example`, `localhost` in dev),
`HAUSBUCH_ORIGIN` (`https://hausbuch.example`, `http://localhost:3000` in dev; WebAuthn permits
plain http only for localhost), RP name "Hausbuch". Registration options:
`resident_key=REQUIRED` (discoverable, so sign-in needs no username), `user_verification=
REQUIRED`, `user_id = app_user.id.bytes`, `user_name = email`, `user_display_name =
display_name`, `exclude_credentials` = the user's existing credential ids. Verification
requires `user_verified` on both ceremonies.

### 6.2 Tables (migration `0003_auth_passkeys`)

Three tables, all with `workspace_id` and composite FKs like every other table (D2):

```
auth_passkey
  id uuid PK, workspace_id, user_id uuid,
  credential_id bytea NOT NULL UNIQUE,            -- global: sign-in looks it up before any workspace is known
  public_key bytea NOT NULL,                       -- COSE key bytes as returned by py_webauthn
  sign_count bigint NOT NULL DEFAULT 0 CHECK (>= 0),
  aaguid uuid, transports jsonb (array of strings) NULL,
  backup_eligible boolean NOT NULL, backed_up boolean NOT NULL,
  label text NOT NULL CHECK (length BETWEEN 1 AND 100),   -- "iPhone von Luis"
  created_at, last_used_at timestamptz NULL,
  UNIQUE (workspace_id, id), FOREIGN KEY (workspace_id, user_id) REFERENCES app_user (workspace_id, id)

auth_session
  id uuid PK, workspace_id, user_id, passkey_id uuid NULL,
  token_hash bytea NOT NULL UNIQUE CHECK (octet_length = 32),   -- SHA-256 of the cookie token
  created_at, last_seen_at, expires_at (absolute), idle_expires_at, revoked_at NULL,
  composite FKs to app_user and auth_passkey

auth_invite
  id uuid PK, workspace_id, user_id,
  token_hash bytea NOT NULL UNIQUE CHECK (octet_length = 32),
  purpose text CHECK (purpose IN ('enrollment')),   -- one value today; keeps the CHECK pattern (D3)
  created_by uuid NULL (app_user id; NULL = CLI), created_at, expires_at, used_at NULL,
  composite FK to app_user
```

No raw tokens are ever stored: the cookie/URL token is 32 random bytes (base64url), the row
holds its SHA-256. Public keys and token hashes are not secrets, which is what makes the lookup
policy below acceptable.

**RLS.** All three get the standard `workspace_isolation` policy (FORCEd, `USING`/`WITH
CHECK` on `current_workspace_id()`) **plus** one extra, SELECT-only policy:

```sql
CREATE POLICY auth_lookup ON auth_passkey FOR SELECT
    USING (current_setting('app.auth_lookup', true) = 'on');
-- same on auth_session, auth_invite
```

Justification: sign-in, session resolution and invite redemption are the three moments where
the workspace is *the output* of the lookup, so it cannot be its input. Rather than exempting
the tables from RLS, the exception is explicit, read-only, and switched on only by
`hausbuch.auth.lookup.auth_lookup_transaction(engine)`: a transaction with **no** workspace
set and `SELECT set_config('app.auth_lookup', 'on', true)`. Policies are permissive (OR'd), so
with the flag on all rows are readable; with it off the table behaves like every other
(workspace unset → nothing visible, `test_session.py`). Writes never happen under the flag:
after verification the workspace is known and `sign_count` / `last_seen_at` / `used_at` are
updated inside a normal `workspace_transaction`. Schema tests (§10.2) prove that the flag does
not enable INSERT/UPDATE/DELETE, and that it is transaction-local (`is_local => true`).

Grants to `hausbuch_app`: `auth_passkey` SELECT, INSERT, UPDATE, DELETE; `auth_session`
SELECT, INSERT, UPDATE, DELETE; `auth_invite` SELECT, INSERT, UPDATE, DELETE; and **`app_user`
gains INSERT** (the invite flow creates the spouse's user row from the browser). `workspace`
stays SELECT, UPDATE only. `hausbuch/db/tables.py` mirrors the three tables; the schema tests'
table lists/counts (`MIGRATION_TABLES`, `== 18` → 21) change in the same spec PR.

Auth actions are **not** written to the `event` table in this step: `event.entity_type` is
CHECKed to the seven domain entities and the catalog is a contract. ARCHITECTURE.md says the
audit log includes admin actions; see open question Q6.

### 6.3 Ceremonies and challenge storage

Challenges are **not** stored in the database. Each ceremony's challenge travels in a signed,
short-lived cookie (`hausbuch/auth/ceremony.py`): payload `{kind: "registration" |
"authentication", challenge: b64url(32 random bytes), subject: {user_id, workspace_id} |
{invite_id} | null, exp}`, encoded base64url, signed HMAC-SHA256 with `HAUSBUCH_SESSION_KEY`
(hex, ≥ 32 bytes, like `HAUSBUCH_IBAN_HMAC_KEY`); cookie `__Host-hb_ceremony`, `HttpOnly`,
`Secure`, `SameSite=Lax`, `Path=/`, `Max-Age=300`. The verify endpoint checks signature, `exp`
and `kind`, uses the challenge as `expected_challenge`, and clears the cookie. Why: no table
without a workspace, no cleanup job, no RLS exception for throwaway rows, and nothing that
breaks when the container is replaced mid-ceremony (the key is in the environment, not in
process memory). Tradeoff: a stolen ceremony cookie plus a stolen assertion could be replayed
within 5 minutes to mint a second session; an attacker holding both already has a session, so
the marginal risk is accepted (Q4 offers the table alternative).

Endpoints (`routes/auth.py`):

| Method, path | Auth | Body → response |
| --- | --- | --- |
| `POST /auth/enroll/options` | public | `{token}` → creation options JSON; sets ceremony cookie (subject = invite) |
| `POST /auth/enroll/verify` | public | `{token, credential, label}` → 201 `Me`; marks the invite used, stores the passkey, opens a session |
| `POST /auth/login/options` | public | `{}` → request options JSON (`allowCredentials` empty = discoverable); sets ceremony cookie |
| `POST /auth/login/verify` | public | `{credential}` → `Me`; sets session cookie |
| `POST /auth/logout` | session | → 204; revokes the session, clears the cookie |
| `POST /auth/logout-all` | session | → 204; revokes every session of the user |
| `GET /auth/me` | session | → `Me {user: AppUser, workspace: Workspace, session: Session}` |
| `GET /auth/passkeys` | session | → `list[Passkey]` (id, label, created_at, last_used_at, backed_up, aaguid) |
| `POST /auth/passkeys/options` | session | → creation options (adding a second passkey; `exclude_credentials` set) |
| `POST /auth/passkeys/verify` | session | `{credential, label}` → 201 `Passkey` |
| `PATCH /auth/passkeys/{id}` | session | `{label}` → `Passkey` |
| `DELETE /auth/passkeys/{id}` | session | → 204; 409 `conflict` when it is the user's last passkey |
| `POST /auth/invites` | session | `InviteCreate {email, display_name}` → 201 `Invite {id, user, enrollment_url, expires_at}` (the URL is shown once) |
| `GET /auth/invites` | session | → pending invites (no token) |
| `DELETE /auth/invites/{id}` | session | → 204 |
| `GET /auth/members` | session | → `list[Member {user: AppUser, has_passkey}]`, oldest first (§10.8 item 7) |

Sign-in verification (`auth/webauthn.py`): parse `rawId` → `auth_lookup_transaction` reads
the `auth_passkey` row (404-as-401 if unknown) → `verify_authentication_response(credential,
expected_challenge, expected_rp_id, expected_origin, credential_public_key,
credential_current_sign_count, require_user_verification=True)` → `userHandle` must equal
`user_id.bytes` → `workspace_transaction(workspace_id)`: update `sign_count`/`last_used_at`,
delete that user's expired sessions, insert the session row → set the cookie. A failed
verification is a 401 with no detail and a warning log line (no credential id in the log).

### 6.4 Session design

- Token: 32 random bytes (`secrets.token_bytes`), base64url in the cookie; only the SHA-256 is
  stored. Lookup by hash under `auth_lookup`. Sessions live in Postgres, so they survive
  container replacement and scale-out (no in-memory state in the API process).
- Cookie `__Host-hb_session`: `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`, no `Domain`,
  `Max-Age` = absolute lifetime. In dev (`HAUSBUCH_COOKIE_SECURE=false`) the name drops the
  `__Host-` prefix because the prefix requires `Secure` (Chromium accepts Secure cookies on
  `http://localhost`, Safari does not).
- Lifetimes (settings, defaults): absolute **12 h** (`expires_at`), idle **2 h**
  (`idle_expires_at`, slid forward on use at most once every 5 minutes to avoid a write per
  request). "Short-lived sessions" with enough room for a data-entry afternoon.
- Resolution (`sessions.py`, the `SessionResolver`): hash the token, read the row under
  `auth_lookup`, check `revoked_at IS NULL`, `now < expires_at`, `now < idle_expires_at`;
  then, if `last_seen_at` is older than 5 min, slide `idle_expires_at` in a
  `workspace_transaction`. Returns `AuthContext`.
- Rotation: a fresh token (new row) on every sign-in; logout revokes (`revoked_at`) rather than
  deletes so the row documents who was signed in; expired rows are deleted on the next sign-in
  of that user (no sweeper job). No mid-session rotation in v1: the session is bound to a
  passkey sign-in and never upgrades privilege.
- Signed-out state on the frontend: any 401 → `/login`.

### 6.5 First user and workspace: bootstrap token from the CLI (no sign-up)

```
$ HAUSBUCH_ADMIN_DATABASE_URL=postgresql+psycopg://hausbuch_owner:…@host/hausbuch \
  uv run hausbuch bootstrap --workspace "Haushalt Rivas" --email luis@example.org --name "Luis"
Workspace 8f1c… created. Enrol the first passkey within 24 h:
https://hausbuch.example/enroll#token=…   (shown once; not stored)
```

`provision_workspace(owner_engine, name, email, display_name, *, now)` (§7) creates the
workspace, the `app_user` and an `auth_invite` in one transaction and returns the raw token
once. The URL carries the token in the **fragment**, so it never reaches server logs or the
Next.js server; the enroll page reads `location.hash` and posts it. `bootstrap` refuses to
run if the database already has a workspace unless `--another-workspace` is passed (SaaS-later
path, not used in v1). On Scaleway the CLI runs as a Serverless **Job** from the same image
(§14): run-to-completion is exactly what a one-off command needs.

### 6.6 CSRF and same-origin

Cookies are `SameSite=Lax`, which already blocks cross-site `POST`s with cookies in modern
browsers; as defence in depth `hausbuch/api/csrf.py` rejects every unsafe method
(`POST/PUT/PATCH/DELETE`) whose `Origin` header is absent or differs from `HAUSBUCH_ORIGIN`
with 403 `forbidden_origin`. Browsers always send `Origin` on cross-origin and same-origin
unsafe requests, including multipart form posts; the WebAuthn verify endpoints (public, POST)
are covered too. No CORS middleware: frontend and API are one origin to the browser (§9.3).
`Strict` was rejected: a link from an email to the app would open logged-out. Chunk 1 must
verify that the `Origin` header survives the Next.js rewrite/proxy in front of the API (§9.3);
if it does not, the proxy adds a fixed header the API accepts instead (Q14).

### 6.7 Second passkey, second household member, recovery

- **Second passkey** (e.g. Luis's laptop after enrolling on the phone): Einstellungen →
  "Passkey hinzufügen" → `POST /auth/passkeys/options` + `verify` (§6.3). Synced passkeys
  (iCloud/Google) report `backed_up`; shown as a hint, not enforced.
- **Spouse**: Einstellungen → "Mitglied einladen" (`POST /auth/invites`) → the response's
  `enrollment_url` is shown once with a copy button; the spouse opens it and enrolls exactly like
  the first user. The user row exists from the invite on (harmless while un-enrolled). Both
  users share the workspace; `actor_id` keeps them apart in the audit trail. There are no roles
  in v1: both are full members (Q5).
- **All passkeys lost**: `uv run hausbuch invite --email luis@example.org [--revoke-passkeys]
  [--revoke-sessions]` (run from Luis's computer, §10.8 item 1; first planned as a
  Serverless Job) prints a fresh enrollment URL (same table, same flow). Root of trust = the
  owner DB credentials plus an allow-listed IP (first: the ability to run a Job);
  there is no email or SMS recovery in v1 (no outbound mail infrastructure, and a recovery
  mail would be the weakest link of a passkey-only design). The UI refuses to delete the last
  passkey (409) so the CLI path is the only way to reach "no passkeys".

## 7. Workspace provisioning without a superuser (confirmed on Scaleway)

Facts (from `0001` and the 5 Nov Scaleway test): `workspace` has FORCE RLS with policy
`id = current_workspace_id()`; `hausbuch_app` has only SELECT, UPDATE on `workspace` and gets
"permission denied" on INSERT. `hausbuch_owner` **can** insert, but only if it sets
`set_config('app.workspace_id', <new id>, true)` in the same transaction first; without that
RLS rejects even the owner. So provisioning runs as the **schema owner** with the new id set:

```python
def provision_workspace(owner_engine, *, name, email, display_name, now) -> Provisioned:
    workspace_id = uuid4()
    with workspace_transaction(owner_engine, workspace_id) as conn:   # SET LOCAL app.workspace_id = new id
        conn.execute(insert(workspace).values(id=workspace_id, name=name, created_at=now))
        user_id = conn.execute(insert(app_user).values(workspace_id=workspace_id, email=email.lower(),
                                                       display_name=display_name, created_at=now)
                               .returning(app_user.c.id)).scalar_one()
        token = create_invite(conn, workspace_id, user_id, created_by=None, now=now)   # same code the API uses
    return Provisioned(workspace_id, user_id, token)
```

`workspace_transaction` already does the `set_config` before yielding, so this is the existing
helper on an owner-role engine. `create_app_engine` accepts the owner URL because
`hausbuch_owner` is `rolsuper=false, rolbypassrls=false` (confirmed), and its guard still
refuses a real superuser, so a misconfigured `HAUSBUCH_ADMIN_DATABASE_URL` fails loudly.

Confirmed environment facts the design relies on (no longer assumptions):

1. Both roles are non-superuser, non-BYPASSRLS LOGIN roles; `btree_gist` is allowed; migration
   `0001` ran as `hausbuch_owner`. (The grants block in `0001` runs only if `hausbuch_app`
   exists at migration time; the deploy notes in §14 order it that way.)
2. Setting custom GUCs (`app.workspace_id`, `app.auth_lookup`) needs no privilege.
3. The CLI never runs inside the API container with the owner URL: `HAUSBUCH_ADMIN_DATABASE_URL`
   is injected into Jobs only (§14).
4. No step, at any point, needs a superuser. The test suite's superuser creates throwaway
   databases and roles only; the new tests exercise provisioning through an owner-role engine
   (`make_engine(dbname, OWNER)`), so the production path is what gets tested.

## 8. S3 store behind `BlobStore`

`hausbuch/storage_s3.py`: `S3BlobStore(bucket, client)` implementing the protocol with
`boto3`, EU region, private bucket, SSE enabled bucket-side. `put` (as built in chunk 5):
1. It **HEADs first**. If the key exists, a different size is an immediate conflict;
   otherwise it fetches and compares the bytes. Equal bytes upload nothing.
2. Otherwise it PUTs with `IfNoneMatch="*"` and, on a 412/409, goes back to step 1 and
   compares again.

That reproduces `LocalBlobStore`'s contract: same bytes = no-op, different bytes =
`BlobConflictError`.
- **If the endpoint ignores the precondition,** the same sequence degrades to head-then-put,
  with a race window lasting the whole upload. That's harmless for content-addressed keys,
  and `read_original` checks the SHA-256.
- **Header support:** Scaleway documents `If-None-Match` support on PutObject. The one-time
  check against a real bucket (Q16) still has to be recorded in `deploy/scaleway.md`.
- **Why HEAD first:** an earlier draft of this section said PUT first; the HEAD-first order is
  at least as safe and avoids re-uploading on every dedup (chunk 5 review). `get` verifies nothing
itself (`read_original` checks the SHA-256). `delete` is retention-only and unused in phase 1.
`presigned_get_url(key, ttl=60)` is an extra method for the download endpoint's future `302`.
Settings: `HAUSBUCH_BLOB_STORE=s3`, `HAUSBUCH_S3_BUCKET`, `HAUSBUCH_S3_ENDPOINT_URL`,
`HAUSBUCH_S3_REGION`; credentials via the SDK's standard chain. Tests: the contract tests from
`tests/test_storage.py` are parametrised to run against S3 only when `HAUSBUCH_TEST_S3_BUCKET`
is set (skipped in CI). Containers have no persistent disk, so **`local` is dev/test only**;
production always uses `s3`.

## 9. Frontend (Next.js 16, App Router, TypeScript, PWA manifest)

Stack: `next` 16.4 (Turbopack default; `proxy.ts` replaces `middleware.ts` in 16), React 19,
TypeScript strict, `@simplewebauthn/browser` 14, `openapi-fetch` + generated `types.ts`,
plain CSS in the mockup style (decided 5 Nov 2026; this replaced the original top bar,
teal accent and system font: `docs/mockups/README.md`) (one `globals.css`, a handful of utility classes; no component library, no
Tailwind). German labels throughout. The PWA manifest (`app/manifest.ts`, icons, `display:
standalone`) ships now so the app can be installed on the phone; no service worker and no
offline mode until phase 2's capture flow needs them.

### 9.1 Screens (minimal set for the done criterion)

| Route | Screen | Calls |
| --- | --- | --- |
| `/login` | "Mit Passkey anmelden" button; error line | `login/options`, `login/verify` |
| `/enroll` | reads `#token=`, "Passkey einrichten" (label input, default = browser/OS name) | `enroll/options`, `enroll/verify` |
| `/` | Übersicht: property cards (label, address, units, open tenancy, loans); "Immobilie anlegen" | `GET /properties`, per property `/units`, `/loans` |
| `/owners` | Eigentümer list + inline create/edit (name, Steuer-ID-Referenz, Grenzsteuersatz) | owners endpoints |
| `/properties/new`, `/properties/[id]/edit` | Immobilie form: Bezeichnung, Art, Adresse, Baujahr, Notartermin, Übergang, Kaufpreis | `POST/PATCH /properties` |
| `/properties/[id]` | Tabs: **Stammdaten** (plus the two date corrections as "Datum korrigieren" dialogs), **Eigentümer** (shares as fractions, `PUT ownership`), **Nebenkosten** (list + add, with document picker), **Kaufpreisaufteilung** (candidates, add, "als AfA-Basis wählen"), **Einheiten**, **Darlehen**, **Dokumente**, **Verlauf** (History), **Protokoll** (audit) | §4.2, §4.8 |
| `/properties/[id]/units/new`, `/units/[id]` | Einheit form; unit page lists tenancies, "Mietverhältnis anlegen", History scoped to the unit | §4.3 |
| `/tenants/new`, `/tenants/[id]` | Mieter form (Name, E-Mail, Telefon, IBAN write-only, shows mask after save); erase with the `retention_hold` explanation | §4.4 |
| `/tenancies/new?unit=…`, `/tenancies/[id]` | Mietverhältnis form (Parteien with "Mieter anlegen" inline, Vertragsdatum, Einzug, Befristung, Mietmodell, Kaution incl. Kautionskonto-IBAN, Anfangsmiete); detail with **Mietstaffel** table (superseded rows struck through, "korrigieren" per row, "Mietänderung"), actions: Kaution erhalten, Kündigung, Auszug, Einzug korrigieren; linked documents | §4.5 |
| `/loans/new?property=…`, `/loans/[id]` | Darlehen form (Bank, Vertragsnummer, Darlehenssumme, Nominalzins, Rate, Auszahlung, erste Rate, Zinsbindung bis, Anschlusszins, Konto-IBAN, Vertragsdatum); detail with the **Tilgungsplan** (entries table, year totals; a 422 `loan.payment_does_not_cover_interest` is shown on the form) | §4.6 |
| `/documents`, `/documents/[id]` | Dokumente list with upload (drag/drop or file picker; shows "bereits vorhanden" on 200); detail: typing form (Typ, Untertyp, Titel, Datum, Steuerjahr, manuelle Felder), Verknüpfungen (add: Zieltyp + picker, remove), "Original öffnen/herunterladen", Aufbewahrung (class, bis / Grund) | §4.7 |
| `/settings` | Passkeys (list, add, rename, delete), Mitglieder (invite → one-time link), Abmelden / überall abmelden | §6.3 |

The document picker (used by Nebenkosten, Kaufpreisaufteilung, Mietstaffel) is one component
over `GET /documents`, with an inline upload.

### 9.2 Layout and navigation

`app/(app)/layout.tsx`: left sidebar (Hausbuch · Übersicht · Dokumente · Eigentümer ·
Einstellungen · user display name · Abmelden; it stacks above the content on a phone),
content column max ~1160 px, breadcrumb inside
a property (Immobilie › Einheit › Mietverhältnis). `app/(public)/layout.tsx` for `/login` and
`/enroll`. The `(app)` layout wraps children in an `AuthGate` client component that calls
`GET /auth/me` once, stores `Me` in a context, and redirects to `/login` on 401. No `proxy.ts`
cookie check: the API is the only authority on sessions, and a presence check on an opaque
cookie buys nothing.

### 9.3 Talking to the API: same origin, cookies

- The browser sees **one origin**, the web app's. Dev: `next.config.ts` `rewrites()` maps
  `/api/:path*` to `${HAUSBUCH_API_URL ?? "http://127.0.0.1:8000"}/api/:path*`. Production on
  Scaleway (§14): the same rewrite, pointing at the API container's URL, so the web container
  proxies `/api/*` server-side; cookies set by the API (`Set-Cookie` passes through the
  rewrite) land on the web origin, `Origin` matches `HAUSBUCH_ORIGIN`, and the RP id is the
  web host. The coder verifies that request headers (`Origin`, `Cookie`) and `Set-Cookie`
  survive the rewrite in both `next dev` and the standalone server; the alternative is a
  `proxy.ts` that rewrites with explicit header forwarding.
- One client module (`src/api/client.ts`): `openapi-fetch` with `baseUrl: ""`, a middleware
  that adds `X-Request-Id` (uuid) and turns non-2xx into a typed `ApiError` (parsed
  `ErrorResponse`), a 401 hook that routes to `/login`, and a retry-once on 503
  `shutting_down` (honouring `Retry-After`), which is what a deploy looks like from the browser.
- Data fetching is client-side (`useEffect`/SWR-style hook per page); pages are client
  components. Server components would need to forward the cookie to FastAPI and would duplicate
  the auth path; not worth it for one household.

### 9.4 Rendering History

`TimelineEntry` → one line per milestone: date, label from `EventType` (German map:
`property.purchase_notarized` → "Kaufvertrag beurkundet", `property.possession_transferred` →
"Übergang Besitz, Nutzen und Lasten", `tenancy.started` → "Einzug", `loan.disbursed` →
"Darlehen ausgezahlt", …), a payload summary (price, rent, amount) and, when `corrected`,
"korrigiert, ursprünglich {original_effective_date}". The audit tab lists `Event` rows with
`seq`, type, actor, `correlation_id` (grouped), and a collapsed payload.

### 9.5 Form validation using the API's errors

- Forms submit the schema shapes directly; no client-side duplication of the rules beyond
  `required` and basic date/number formats. Money inputs are text fields normalised by
  `toMoneyString()` (`"1.400,00"` / `"1400"` → `"1400.00"`), rates by `toRate()`
  (`"3,6 %"` → `"0.036000"`), shares as `n/d` fractions.
- 422 `validation`: each `detail[].loc` (`["body", "address", "postal_code"]`) is mapped to the
  field path; the message appears under that input; unmapped errors go to the form banner.
- 422 `rule_violation` / `invalid`: `codes` are translated by a dictionary keyed on the stable
  codes from `phase-1.md` (`ownership.shares_must_sum_to_one` → "Die Anteile müssen zusammen
  genau 1 ergeben", `tenancy.exactly_one_primary`, `rent_schedule.outside_tenancy`, `iban.*`,
  `loan.payment_does_not_cover_interest` with `at_number`); unknown codes show the server
  message.
- 409 `conflict` / `retention_hold` and 404: banner with the message; `retention_hold` shows
  `result.retain_until` or the hold reason.

## 10. Testing

### 10.1 API tests (`backend/tests/api/`, FastAPI `TestClient` + the real test database)

Reuse `tests/db/conftest.py` (`dbname`, `make_engine`, `fresh_workspaces`, `blob_store`).
New fixtures in `tests/api/conftest.py`:

- `api_app(app_engine, fresh_workspaces, tmp_path)`: `create_app(settings=test_settings,
  engine=app_engine, blob_store=LocalBlobStore(...), session_resolver=FakeResolver({token_a:
  AuthContext(ws_a, user_a, …), token_b: …}), worker=None)`. The fake resolver is the §3.1
  protocol, so domain tests need no WebAuthn. Both workspaces are **provisioned through
  `provision_workspace`** once chunk 2 lands; until then the `fresh_workspaces` superuser insert
  is fine because `app_user` rows are not needed by a fake resolver.
- `client_a`, `client_b`: `TestClient(api_app)` with the respective cookie and the
  `Origin: http://testserver` header set by default.

Cases (one file per router): create/read/patch round-trips for every resource; status codes
and `error` values for each row of §3.5 (not found, overlap 409, retention hold 409, duplicate
link 409, rule violation codes); **isolation** (`client_b` gets 404 for `client_a`'s ids on
every entity type); **money is a string** (`type(json["purchase_price"]) is str`, and a float
body → 422 after amendment 2); **no echo** (§3.6: bad IBAN and labelled tax number never
appear in body or `caplog`); **transaction boundary** (a `POST /tenancies` with an invalid
party set leaves no tenancy row and no event); **correlation** (events of one request share
`correlation_id == X-Request-Id`); upload dedup (second upload of same bytes → 200, same id),
413 and 415; original download headers; **lifespan** (a fake worker is started on startup and
`stop()`ed within the grace period on shutdown; requests during drain get 503; `/healthz` 200
with the DB and 503 without); **done criterion through the API**: the `timeline.yaml`
"done-criterion" scenario replayed over HTTP and compared to the fixture's expected entries
(the same comparison `tests/db/test_history.py` makes through the services).

### 10.2 Auth tests (`backend/tests/auth/`)

- `tests/auth/authenticator.py`: a **software authenticator** built from `cryptography` and
  `cbor2` (both already dependencies of py_webauthn): generates an ES256 key, and produces
  `attestationObject` (`fmt: "none"`, authenticator data with UP|UV|AT|BE|BS flags, AAGUID,
  credential id, COSE key) and `clientDataJSON` for registration, and `authenticatorData` +
  DER signature over `authData || sha256(clientDataJSON)` for authentication, with a
  controllable sign counter and origin. This exercises the real `verify_*` code paths and lets
  tests assert rejections: wrong origin, wrong RP id, replayed challenge, counter going
  backwards, `userHandle` mismatch, missing UV flag, expired ceremony cookie, tampered cookie
  signature.
- Flows: bootstrap → enroll → `me`; login → `me` → logout → 401; idle and absolute expiry (freeze
  `now`); invite spouse → enroll → both see the same property; last-passkey delete refused;
  invite reuse refused; `logout-all`.
- Schema/RLS tests (`tests/db/test_auth_tables.py`): `auth_lookup` on → SELECT sees rows of
  every workspace, UPDATE/INSERT/DELETE affect none; flag off → nothing visible; flag is gone in
  the next transaction on the same connection; `hausbuch_app` cannot INSERT into `workspace`
  (the Scaleway finding, now a regression test); `provision_workspace` succeeds as
  `hausbuch_owner`, fails without the `set_config` (a direct INSERT as owner in a transaction
  with no workspace set is rejected), and fails with `UnsafeDatabaseRoleError` when handed a
  superuser engine.

### 10.3 Playwright (`frontend/e2e/`, Chromium)

A CDP session enables `WebAuthn` and adds a virtual authenticator (`protocol: "ctap2",
transport: "internal", hasResidentKey: true, hasUserVerification: true, isUserVerified:
true`), which is the standard way to drive passkeys headlessly (Chromium only). Global setup
creates a throwaway database and roles, migrates as owner, runs `hausbuch bootstrap` and
writes the enrollment URL to a file; `webServer` starts uvicorn and `next dev`.

Flows:
1. **Enrol and sign in**: open the enrollment URL → passkey created → `/` → sign out → sign in
   with the passkey → `/`.
2. **Done criterion through the UI**: owners (Luis, spouse 1/2 each) → property (ETW, both
   dates, price) → ownership → Nebenkosten with an uploaded notary invoice → split from the
   contract, selected → unit → tenant → tenancy with initial rent and deposit → loan →
   `/documents` upload of the lease, typed and linked to the tenancy → the property's Verlauf
   shows, in order: Kaufvertrag beurkundet, Darlehen …, Übergang, Einzug, with the dates
   entered; then correct the move-in date and see "korrigiert, ursprünglich …".
3. **Validation surfaces**: ownership shares 1/2 + 1/3 → the German message under the shares
   field; a loan with a payment below the first interest → the schedule error on the form.

### 10.4 CI

`backend.yml` unchanged in spirit (adds `tests/api`, `tests/auth` automatically);
`frontend.yml`: `npm ci`, `tsc --noEmit`, `next lint`, `next build`, the OpenAPI/types staleness
check (§5), then the Playwright job (Postgres service, `uv run`, `npx playwright install
chromium`). E2E runs on PRs touching `frontend/` or `backend/src/hausbuch/api/`. Image builds
(§14) run on `main` only.

## 10.5 Rules from the chunk 0 review (orchestrator, 5 Nov 2026)

Binding for the chunks that follow:

- **Deleting a passkey ends the sessions it opened** (chunk 2: `DELETE /auth/passkeys/{id}`
  and `hausbuch invite --revoke-passkeys`). In one `workspace_transaction`, revoke its
  un-revoked sessions and set their `passkey_id` to NULL, then delete the passkey.
  - The `auth_session → auth_passkey` FK is deliberately `NO ACTION`, so code that forgets
    this fails loudly. A lost device's passkey must never leave its sessions alive.
  - `test_a_passkey_with_sessions_cannot_be_deleted_bare` pins the contract.
- **Atomic sign counter** (chunk 2). The lookup transaction can't lock rows; `FOR UPDATE`
  under the flag returns nothing, by design. So the WebAuthn sign-count check must be atomic
  inside the workspace transaction: `UPDATE auth_passkey SET sign_count = :new WHERE id = :id
  AND sign_count < :new`, then treat 0 rows as a cloned authenticator. Otherwise two
  concurrent assertions could both pass.
- **The API refuses the owner role** (chunk 2, `main.py`). The API must not start as the
  schema owner: it would gain INSERT on `workspace` and DELETE on `event`. Refuse at startup
  when the connected role owns the schema's tables, or has INSERT on `workspace`, in the
  same spirit as the existing superuser/BYPASSRLS refusal.
- **No raw DB errors in logs or responses** (chunk 1). `IntegrityError` text contains the
  offending values: for example, a duplicate `app_user.email` names the email. Never log or
  return `str(exc)` for DBAPI errors; log the constraint name only (§3.6).

## 10.6 Handover notes from chunk 3a (orchestrator, 5 Nov 2026)

- **Chunk 3b** adds the two `/settings` pieces 3a left out: renaming a passkey, and
  **inviting a household member**. The spouse's invite screen is needed before real use.
  3b also replaces the `(app)/page.tsx` placeholder and `src/api/interim-auth-types.ts` (with
  its generator script) with types generated from the real `openapi.json`.
- **Chunk 4:**
  - Next's rewrite fixes the API address **at build time**, so the web image needs
    `HAUSBUCH_API_URL` as a build argument, or a `proxy.ts` that routes at runtime.
  - The rewrite adds `x-forwarded-host` but not `x-forwarded-for`, so the API sees the web
    container's IP. That matters for per-IP rate limiting (Q9).
  - Next's proxy times out after 30 s by default. Measure it with a 25 MiB upload together
    with the Scaleway body limit (Q13).
- The frontend CI's `openapi.json` staleness check becomes meaningful once `hausbuch openapi`
  (chunk 2) and the real routers (chunk 1) land.

## 10.7 Rules from the chunk 2 review (orchestrator, 5 Nov 2026)

- **Re-inviting a member who never enrolled.** Today a deleted or expired invite makes the
  email unusable: `app_user.email` is globally unique, and the app role can't delete users.
  Decision: `POST /auth/invites` for an email whose `app_user` is **in this workspace and has
  no passkey** reuses that row. It deletes the user's pending invites and issues a fresh
  link. An email belonging to a user with a passkey, or to another workspace, stays a 409.
  This refines §6.3's "creates their app_user row".
- **Enrollment links in Job logs** (§6.5, chunk 4's `deploy/scaleway.md`). The CLI prints the
  link to stdout, which on Scaleway lands in the Job's logs. That is acceptable for v1
  because the link is single-use and expires after 24 h. The deploy notes must say:
  - enroll immediately after the Job runs;
  - a refused link means "assume it was used": run `hausbuch invite --email … --revoke-passkeys
    --revoke-sessions`, which evicts whoever used it.
  - A shorter `--ttl` is optional hardening for later.
- **Test helpers.** The three `tests/{api,auth,db}/conftest.py` are all importable as
  `conftest`, so `pytest tests/auth tests/db/...` in one run picks the wrong one. Fix
  repo-wide by moving shared helpers into uniquely named support modules, as `api_support.py`
  already does. No behaviour change.

## 10.8 Findings from chunks 4a and 3b (orchestrator, 5 Nov 2026)

Chunk 4a found these by running the images and the setup for real:

1. **One-off commands can't run as Serverless Jobs against a private-only database.** Per
   Scaleway's docs, Jobs can't join a private network and have no fixed IP. That breaks §14,
   and the Provisioning row in `docs/ARCHITECTURE.md`. **Decided (Luis, 5 Nov 2026): option (A)**, run from his computer with `uv`, no Docker; recorded in ARCHITECTURE's Decisions table. The options were
   in `deploy/scaleway.md`:
   - (A) run the same commands from Luis's computer, with the database's public endpoint
     allowed only from his IP;
   - (B) Jobs over a public endpoint open to Scaleway's IP ranges;
   - (C) a short-lived private Serverless Container per command.

   **Runbook updated:** `deploy/scaleway.md` now makes (A) the only path. It covers
   installing uv, `uv sync` in `backend/`, the admin env file with
   `HAUSBUCH_ADMIN_DATABASE_URL` (`sslmode=require`) on Luis's computer only, the
   public-endpoint allow-list of his current IP and how to replace it, migrations as
   `uv run --env-file … alembic upgrade head` from the deployed commit, and the §10.7 link
   rule. The Job path is a short "not used" note. §6.7's and §14's mentions of Jobs below
   are superseded by it.
2. **§14's one-time SQL as first written didn't lock the database.** A non-superuser admin's
   `REVOKE CONNECT … FROM PUBLIC` silently does nothing once its membership in the owner role
   is gone. The revoke must run as the owner (`SET ROLE hausbuch_owner`) **before** the
   membership is dropped. `deploy/scaleway.md` and `scripts/dev-db.sh` use that order and end
   with a check query. (§14 as first written was wrong; the test found it.)
3. **Uploads over 10 MB fail through the web container.** Next's proxy cuts bodies at 10 MB
   and times out after 30 s. The fix, tested in a copy:
   `experimental: { proxyClientMaxBodySize: "26mb", proxyTimeout: 120_000 }` in
   `frontend/next.config.ts`.
4. **The API exits when the DB is unreachable at startup.** Scaleway can start a container
   before its private network is ready. `main.py` should retry the startup role check with a
   bounded backoff. The runbook also uses Scaleway's two-private-networks workaround.
   **Implemented:** `check_database_role_with_retry` retries on `OperationalError` /
   `InterfaceError` (SQLAlchemy's and psycopg's) only: 6 attempts, waits 1–16 s, no new
   attempt once the next wait would pass 60 s. Each attempt is logged by error class, never
   the URL or the driver message. Role refusals raise at once. A connect that hangs is bounded
   by the OS or a `connect_timeout` in the URL, not by this loop.
5. **`pg_dump` as the owner gives an empty backup** under forced RLS: `--enable-row-security`
   "succeeds" with zero rows. The runbook says how to back up correctly.

Chunk 3b found these API gaps; none of them blocked a screen:

6. **Correcting the move-in doesn't move the initial rent row.** **Decided (Luis, 5 Nov 2026):**
   `correct_move_in` also corrects the rent row that started exactly on the old move-in date (a
   `correction` row plus a correcting `tenancy.rent_scheduled` event, so History shows it).
   Rows with other dates are untouched.
   **Implemented:** `correct_move_in` calls `correct_rent_row` for the row in force on the old
   date, after updating the tenancy, in the same transaction. Two refusals guard against a
   silent change of which row applies: two rows in force on the old date (they would tie with
   the same `recorded_at`), and a moved row landing on another row's date (it would win the
   tie). The done-criterion replays are unchanged: they correct the move-in before the first
   rent row exists.
7. **No list of household members**, no read endpoint for deposits received (the tenancy page
   reads them from the event log), and document responses lack the media type.
   **Implemented:** `GET /auth/members` (`Member {user, has_passkey}`),
   `GET /tenancies/{id}/deposits` (`DepositReceipt`, from the `tenancy.deposit_received`
   events; a corrected one would show its latest event), and `DocumentDetail.media_type` plus
   `opens_inline` (the §4.7 inline allow-list). `/settings` lists the members, the tenancy
   page reads the new deposits endpoint, and the document page offers "Original öffnen" only
   when it opens inline. The original download now reads its media type from
   `document_file` (new `document_media_type`), the same source as the detail. The tenancy
   page's rent schedule reloads after a move-in correction, since that moves the first row.

8. **Open question (not a defect; for Luis, later):** two rent rows in force on the **same**
   date. `rent_at.yaml`'s tie-break case treats that state as legitimate. A move-in correction
   there is refused, which is the correct guard, but no command can retire one of the two rows,
   so the state is a dead end. A supported way out, e.g. a deterministic order when moving
   both, would touch the `rent_at` tie-break contract, so it belongs in a spec PR. It is rare:
   it needs an initial rent and a rent agreement entered for the same day.

## 11. Delivery plan: chunks, ownership, order

Every chunk commits pathspec-limited to its owned paths; nobody edits another chunk's files.
Interfaces fixed by chunk 0 before anything else starts: `AuthContext` + `SessionResolver`,
`Commands`, the worker protocol and `create_app` signature, `ErrorResponse` and the `error`
strings, the `schemas/api.py` and `schemas/auth.py` models, the endpoint table above (paths
and bodies), settings names, the `0003` tables and grants, and `pyproject.toml` dependencies
(all added at once to avoid `uv.lock` conflicts).

| # | Chunk | Owns | Depends on | Parallel with | Done when | Risk |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | **Spec PR: contracts and migration** — `schemas/api.py`, `schemas/auth.py`, `InitialRent` move, spec amendment 2 (money strings), `0003_auth_passkeys` + `tables.py` mirror + schema-test list updates, `settings.py` additions, the four `list_*` service queries, `api/auth_context.py`, `api/commands.py`, `api/app.py` stub (factory signature, router includes, worker protocol), `pyproject.toml` deps, this doc's status flips | listed files | — | — | Schema tests green incl. 21 tables; `tests/db/test_auth_tables.py` RLS cases pass; `uv run pytest` 497 + new; `hausbuch.schemas` imports nothing from services | **High** (migration, RLS policy, grants, schema contract) → reviewer, FOCUS: `auth_lookup` policy cannot enable writes; composite FKs; `app_user` INSERT grant; amendment 2 does not change any fixture outcome |
| 1 | **API core + domain routers** — `api/app.py` body, `lifespan.py`, `health.py`, `errors.py`, `logging.py`, `csrf.py`, `routes/*` except `auth.py`, `api/main.py`, `tests/api/*` | `backend/src/hausbuch/api/` (minus `routes/auth.py`), `tests/api/` | 0 | 2, 3a, 5 | §10.1 cases pass with the fake resolver and a fake worker, including the done-criterion replay and the no-echo guards; `hausbuch openapi` output committed | **Medium** → reviewer, FOCUS: 422 handler whitelist and logging redaction; one transaction per request; no path that writes outside a service; drain/shutdown ordering |
| 2 | **Auth** — `hausbuch/auth/*`, `routes/auth.py`, `cli.py` (`bootstrap`, `invite`, `openapi`), `tests/auth/*` | `backend/src/hausbuch/auth/`, `api/routes/auth.py`, `cli.py`, `tests/auth/` | 0 (`create_app` and the router include come from the stub) | 1, 3a, 5 | §10.2 passes with the software authenticator; `hausbuch bootstrap` works against a dev DB as `hausbuch_owner`; `provision_workspace` refuses a superuser engine | **High** (sessions, pre-auth lookup, provisioning) → reviewer, FOCUS: verification parameters (origin, RP id, UV, counter), token hashing, cookie flags, lookup flag never on during writes |
| 3a | **Frontend scaffold + auth screens** — `frontend/` package, layout, `AuthGate`, API client, `/login`, `/enroll`, `/settings`, PWA manifest, `frontend.yml` lint/build | `frontend/` (minus `src/app/(app)/{properties,units,tenants,tenancies,loans,documents,owners}`), `.github/workflows/frontend.yml` | 0 (endpoint contract in this doc); runs against chunk 2 for manual checks | 1, 2, 5 | `next build` passes; sign-in/enrol work against a local backend by hand; `Origin`/`Set-Cookie` verified through the rewrite | Low |
| 3b | **Domain screens** — overview, property tabs, forms, documents, History | `frontend/src/app/(app)/{properties,units,tenants,tenancies,loans,documents,owners}/`, `frontend/src/components/` | 1 (generated types), 3a | 2, 5 | Every §9.1 screen reachable; done-criterion flow works by hand against the API; types generated, no `any` | Low (no persisted logic); the orchestrator checks the History rendering against `timeline.yaml` wording |
| 4 | **E2E, CI wiring, deploy artefacts** — `frontend/e2e/*`, `playwright.config.ts`, e2e job, `scripts/e2e-db.sh`, `deploy/` (two Dockerfiles, `deploy/scaleway.md` with §14's one-time steps) | `frontend/e2e/`, `scripts/`, `deploy/`, e2e + image jobs in the workflows | 1, 2, 3b | 5 | The three §10.3 flows green in CI on Chromium; both images build; `deploy/scaleway.md` reproduces §14 | Low |
| 5 | **S3BlobStore** — `storage_s3.py`, settings wiring, contract tests | `backend/src/hausbuch/storage_s3.py`, `tests/test_storage_s3.py` | 0 (settings names) | 1, 2, 3a, 3b | Contract tests pass against the Scaleway bucket once by hand (record whether `If-None-Match` is honoured, Q16); CI skips without credentials | **Medium** (principle 6) → short review, FOCUS: no overwrite path, conflict detection |

Order: 0 → {1, 2, 3a, 5} → 3b → 4. Chunk 0 is small enough for one coder (or the orchestrator)
and must land first; four coders can then run at once. Step 4 of phase 1 (real data) starts
after 4, on the deployed instance.

**Ownership of `api/main.py` (orchestrator, 5 Nov 2026):** `main.py` is the composition root that builds the settings, engine, BlobStore and chunk 2's `SessionResolverImpl` and calls `create_app`. It is owned by **chunk 2**, not chunk 1 (the table above lists it under chunk 1, which contradicted the rule below; found while implementing chunk 0). Chunk 1's tests build the app with `create_app` and a fake resolver and never need `main.py`.

Interfaces the parallel chunks must agree on, restated: chunk 1 never imports `hausbuch.auth`
(it talks to `SessionResolver` only); chunk 2 never edits `api/app.py` (it exposes
`auth_router` and `SessionResolverImpl`; chunk 0's `app.py` stub already includes
`hausbuch.api.routes.auth.router`); chunk 3a's client is typed against a hand-checked
`openapi.json` exported from chunk 0's stub app (empty routers) and regenerated by chunk 3b
once chunk 1 lands.

## 12. Changes to existing contracts and to ARCHITECTURE.md

Spec-level (chunk 0, the spec PR):

1. **Spec amendment 2, money as strings** (`schemas/common.py`): `Money`, `NonNegativeMoney`,
   `PositiveMoney`, `Rate`, `TaxRate`, `BuildingShare`, `Area`, `Rooms` reject `float` input and
   publish `type: string` in JSON schema. Fixtures are unaffected (all quoted). Record in
   `phase-1.md` under "Spec amendment 2".
2. **`InitialRent`** moves to `schemas/tenancy.py` (re-exported by `services/tenancies.py`).
3. **Grants**: `app_user` gains INSERT for `hausbuch_app` (invites). `phase-1.md`'s data model
   table says "Read-only to the app"; update it to "Insert by invite; never updated or deleted
   by the app".
4. **New tables** `auth_passkey`, `auth_session`, `auth_invite` with the `auth_lookup`
   SELECT policy: document in `phase-1.md`'s data model (21 tables) and invariants ("Unset
   workspace means no rows visible, except SELECT on the three auth tables while
   `app.auth_lookup = 'on'`, set only by `hausbuch.auth.lookup`").
5. **Service additions** (`list_owners`, `list_properties`, `list_tenants`, `list_tenancies`):
   pure queries, same style as `list_units`.
6. **Schema tests** (`test_migration.MIGRATION_TABLES`, `test_tables_match_migration` count):
   updated in the spec PR; these are schema tests, not rule fixtures.

`docs/ARCHITECTURE.md` (same PR as chunk 0):

- Decisions table, new row: **Provisioning (5 Nov 2026)** — "Workspaces and first users are
  created by `hausbuch bootstrap`, a CLI running as the schema-owner role (a Serverless Job on
  Scaleway); members join by one-time enrollment link; recovery is a new link from the CLI" —
  what it changes: "no sign-up endpoint; the owner DB URL is an operator secret the API never
  holds".
- Decisions table, new row: **Sessions (5 Nov 2026)** — "Opaque server-side sessions in
  Postgres, 12 h absolute / 2 h idle, `SameSite=Lax` + Origin check" — what it changes:
  "nothing else; recorded so 'short-lived' has a number".
- Decisions table, new row: **Worker placement (5 Nov 2026)** — "The job worker runs inside
  the API process (FastAPI lifespan) in the one container that serves port 8080, min-scale 1;
  a separate worker container only when load justifies it" — what it changes: "the System
  architecture diagram's 'Background workers' box is a lifespan task in the API container for
  v1, not a second deployable; Scaleway Serverless Containers kill a process that does not
  listen on 8080".
- System architecture section: one sentence under the diagram stating the above, and
  "Hosting" row of the stack table: "Scaleway Serverless Containers (API+worker, web) +
  managed Postgres on a private network + Object Storage, all in an EU region; one-off
  commands as Serverless Jobs".
- Security bullet "Audit log = the Event table; admin actions and AI decisions included":
  add "(auth actions: server log in phase 1; see phase 1 step 3 Q6)" until Q6 is decided.
  Security bullets gain "database reachable only on the private network; CONNECT revoked from
  PUBLIC" (§14).
- Stack table row "API": add "py_webauthn for passkeys".

## 13. Open questions and tradeoffs (with recommendations)

**Decided by Luis on 5 Nov 2026:** every recommendation below is adopted. Q6 is (a) now and (b) in the phase 2 spec PR. Hosting is Scaleway. `docs/ARCHITECTURE.md` records these in its Decisions table.

| # | Question | Options | Recommendation |
| --- | --- | --- | --- |
| Q1 | **Reject JSON numbers for money** (amendment 2) or only coerce? | (a) reject floats, publish `string` schema; (b) accept both, rely on the frontend | **(a)**: it is the only way the generated TS types enforce strings; the cost is one `BeforeValidator` and a schema annotation. Needs the full suite run in the spec PR. |
| Q2 | **Session lifetimes** 12 h / 2 h idle? | shorter (4 h / 30 min) is safer; longer is friendlier | Keep 12 h / 2 h as defaults in settings; Luis can tighten them per environment. |
| Q3 | **`SameSite=Lax` + Origin check** vs `Strict` | Strict blocks nothing extra given the Origin check and breaks email links | Lax. |
| Q4 | **Challenge in a signed cookie** vs an `auth_challenge` table | Table: single-use enforced server-side, needs the `auth_lookup` policy and a cleanup; cookie: stateless, 5-minute replay window | Cookie for v1; revisit if the app ever becomes multi-tenant SaaS. |
| Q5 | **Roles for household members** (spouse read-only?) | none vs owner/member | None in v1: both file jointly and both enter paperwork; `actor_id` already attributes every event. |
| Q6 | **Auth actions in the audit trail** | (a) server log only; (b) extend the event catalog with a `user` entity (`user.passkey_added`, `user.signed_in`, …), a spec change to `event.entity_type` CHECK and `EVENT_CATALOG`; (c) a separate `auth_log` table | (a) now, (b) in the phase 2 spec PR, which already touches the catalog for AI provenance. Flagged because ARCHITECTURE.md promises admin actions in the audit log. |
| Q7 | **Serving originals**: through the API or presigned S3 URLs? | API streams bytes (simple, SHA-verified every time); presigned URL (no API bandwidth, no verification on read) | API in phase 1; add the `302` when uploads grow (phase 2 statements) or if Q13 forces it. |
| Q8 | **Upload media allow-list** | strict (pdf/images) vs broad | The §4.7 list; `message/rfc822` kept for forwarded mails Luis already has. |
| Q9 | **Rate limiting the public auth endpoints** | none; per-IP limit at the edge | Scaleway Edge Services / a small in-API token bucket on `/api/auth/*` only if abuse appears; passkeys are phishing-resistant and unguessable, so this is about noise, not brute force. |
| Q10 | **Pagination** on lists | none vs cursor | None in v1 (one flat, tens of documents); add when phase 2 imports statements. |
| Q11 | **Dev database setup** | manual psql vs `hausbuch dev-init` creating roles | A documented `scripts/dev-db.sh` (chunk 4 owns `scripts/`) that mirrors §14's one-time steps on a local Postgres, rather than a CLI that needs a superuser. |
| Q12 | **Should `POST /properties` accept ownership inline?** | one request (two commands in one transaction would need a new service) vs two requests | Two requests (§4.2); if the half-done state bothers Luis in step 4, add a `create_property_with_ownership` service in a later spec PR. |
| Q13 | **Request body limit on Scaleway Serverless Containers vs 25 MiB uploads** | lower the cap; or upload straight to Object Storage with a presigned PUT and register the key afterwards | Measure the platform limit during chunk 4 (deploy). If it is below 25 MiB, lower `HAUSBUCH_MAX_UPLOAD_BYTES` to fit for phase 1 (scanned PDFs are a few MB) and schedule the presigned-PUT path for phase 2 (the service's "blob first, rows second" order already allows registering a key that exists). |
| Q14 | **API container public or private?** | public (every endpoint authenticated; Origin check); private (Scaleway container token added by the web proxy via `proxy.ts`) | Public for v1, since the API already authenticates everything and the web container must reach it anyway; make it private once `proxy.ts` header forwarding is verified (chunk 3a's check). Either way `/healthz` stays reachable by the platform. |
| Q15 | **Shutdown grace** | the worker's grace must be shorter than the platform's SIGTERM→SIGKILL window | Confirm Scaleway's termination timeout during chunk 4; default 20 s + uvicorn 25 s assumes ≥ 30 s. |
| Q16 | **Conditional PUT on Scaleway Object Storage** | honoured → strong no-overwrite; ignored → head-then-put (small race) | Test once by hand in chunk 5 and record the result in `deploy/scaleway.md`; principle 6 is still protected by content addressing and the read-side SHA-256 check either way. |

Needs a spec change to existing contracts: Q1 (amendment 2), §12 items 2–6. Changes
`docs/ARCHITECTURE.md`: §12's three Decisions rows, the hosting row and the audit-log footnote.

## 14. Deployment on Scaleway (from the 5 Nov compatibility test)

Two Serverless Containers from two images, both listening on **8080**:

| Container | Image | Runs | Scale | Env / secrets |
| --- | --- | --- | --- | --- |
| `hausbuch-api` | `backend/Dockerfile` (uv, Python 3.12) | `uvicorn hausbuch.api.main:app --host 0.0.0.0 --port 8080 --timeout-graceful-shutdown 25`, worker inside (§3.7) | **min 1, max 1** in v1 (one worker; sessions and jobs live in Postgres, so max > 1 is safe later) | `HAUSBUCH_DATABASE_URL` (app role), `HAUSBUCH_IBAN_HMAC_KEY`, `HAUSBUCH_SESSION_KEY`, `HAUSBUCH_RP_ID`, `HAUSBUCH_ORIGIN`, `HAUSBUCH_BLOB_STORE=s3`, `HAUSBUCH_S3_*`, `HAUSBUCH_RUN_WORKER=true`; health check `GET /healthz` |
| `hausbuch-web` | `frontend/Dockerfile` (Next standalone output) | `node server.js` on 8080, `/api/*` rewritten to the API container URL | min 0 is acceptable (stateless; cold start only costs a page load) | `HAUSBUCH_API_URL` |
| ~~Jobs (same `backend` image)~~ superseded by §10.8 item 1: run from Luis's computer with `uv` | — | `alembic upgrade head`, `hausbuch bootstrap`, `hausbuch invite` | run-to-completion | `HAUSBUCH_ADMIN_DATABASE_URL` (owner role) **only there**, never in the API container |

Why min-scale 1 on the API: a container whose process does not listen on 8080 is killed after
about five minutes, so a separate worker container is not an option in v1, and the in-process
worker must not be scaled to zero. A Serverless Job is run-to-completion and fits the CLI, not
a long-running worker.

One-time database setup (deployment, not code; the admin user is not a superuser):

```sql
-- as the managed instance's admin user
CREATE ROLE hausbuch_owner LOGIN PASSWORD '…';
CREATE ROLE hausbuch_app   LOGIN PASSWORD '…';           -- before the first migration, so 0001's grants run
GRANT hausbuch_owner TO <admin> WITH SET TRUE;
CREATE DATABASE hausbuch OWNER hausbuch_owner;
REVOKE hausbuch_owner FROM <admin>;
REVOKE CONNECT ON DATABASE hausbuch FROM PUBLIC;
GRANT CONNECT ON DATABASE hausbuch TO hausbuch_owner, hausbuch_app;
-- then, as a Job with HAUSBUCH_DATABASE_URL = the owner URL: alembic upgrade head
```

Confirmed: both roles `rolsuper=false, rolbypassrls=false`; `btree_gist` allowed; migration
`0001` ran as the owner; the app role cannot insert into `workspace`; the owner can, with the
`set_config` in the same transaction (§7).

Hardening (to do with the first deploy, recorded in `deploy/scaleway.md`):

- Put the Postgres instance on a **private network**; the default allows any IP. Containers
  attach to the same private network.
- `REVOKE CONNECT … FROM PUBLIC` and grant CONNECT to the two Hausbuch roles only (above).
- Secrets from Scaleway Secret Manager into container/job environments; the owner URL goes to
  Jobs only. Rotate `HAUSBUCH_SESSION_KEY` by redeploying (invalidates in-flight ceremonies,
  not sessions).
- Object Storage bucket private, EU region (same as the database), SSE on; the API's S3
  credentials scoped to that bucket.
- Custom domain on `hausbuch-web` with TLS; `HAUSBUCH_ORIGIN` and `HAUSBUCH_RP_ID` set to it.
  The API container's own URL is never used by a browser.
- Logs: JSON on stdout only (§3.6); no request bodies, no IBANs, no tenant names.

Open items for the deploy step: Q13 (request size limit), Q14 (private API container), Q15
(termination timeout), Q16 (conditional PUT), and the container memory/CPU sizing (start at
the smallest tier; the schedule and History are cheap).

## Running it (target state after chunks 0–4)

```bash
cd backend
uv sync --python 3.12
export HAUSBUCH_DATABASE_URL=postgresql+psycopg://hausbuch_app:…@localhost/hausbuch
export HAUSBUCH_ADMIN_DATABASE_URL=postgresql+psycopg://hausbuch_owner:…@localhost/hausbuch
export HAUSBUCH_IBAN_HMAC_KEY=<64 hex> HAUSBUCH_SESSION_KEY=<64 hex>
export HAUSBUCH_RP_ID=localhost HAUSBUCH_ORIGIN=http://localhost:3000 HAUSBUCH_COOKIE_SECURE=false
export HAUSBUCH_BLOB_STORE=local HAUSBUCH_BLOB_ROOT=./var/blobs HAUSBUCH_RUN_WORKER=false
HAUSBUCH_DATABASE_URL=$HAUSBUCH_ADMIN_DATABASE_URL uv run alembic upgrade head   # as the owner
uv run hausbuch bootstrap --workspace "Haushalt" --email luis@example.org --name Luis
uv run uvicorn hausbuch.api.main:app --port 8000
cd ../frontend && npm ci && npm run dev        # http://localhost:3000, /api proxied to :8000
```
