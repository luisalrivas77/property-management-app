# Deploying Hausbuch on Scaleway

A step-by-step runbook for the first deploy and for later updates. Each step says what it
does in plain words first; the exact commands follow. The design behind it is
`docs/phases/phase-1-step-3.md` §3.7 (process model), §6.5–6.7 (bootstrap and recovery),
§10.6–10.7 and §14 (deployment). Scaleway facts below were checked against Scaleway's
documentation sources on 5 Nov 2026. Console labels and CLI flags change, so where a click path or
flag differs, follow the console and `scw <command> --help`.

## What you end up with

- **`hausbuch-web`**: a Serverless Container running the Next.js server. Browsers only ever
  talk to it, on your own domain. It forwards every `/api/*` request to the API container.
- **`hausbuch-api`**: a Serverless Container running the FastAPI app, with the job worker in
  the same process (phase 2 fills it). It always runs one instance (min-scale 1, max-scale 1).
- **One-off commands** from the same API image: database migrations, `hausbuch bootstrap`
  (first workspace and user) and `hausbuch invite` (a fresh enrollment link, e.g. after a lost
  phone). Only these get the database **owner** password; the API never does.
- **Managed PostgreSQL 16** (DB-DEV-S) on a private network, with daily backups.
- **An Object Storage bucket** (private, EU) for the original documents.
- **A Container Registry namespace** for the two images.
- **Secret Manager** as the one place where every secret value is kept.

Everything lives in one Scaleway project in region **fr-par** (Paris). The expected cost is
**about €20/month excluding VAT** at this load (`docs/ARCHITECTURE.md`, stack table). That
is an estimate from November 2026. Re-check it on Scaleway's live price pages before you
create anything; the API container's min-scale 1 means it is billed around the clock.

## Read this first: five things the spec did not know

The first deploy will run into these. Two need a decision from you before you start
(marked **Decision**). The rest are already handled in the steps below.

1. **Decision: Serverless Jobs cannot reach a private network.** Scaleway's Jobs FAQ says
   so: "Serverless Jobs does not currently support Scaleway VPC or Private Networks, though
   this feature is under development". Jobs also have no fixed IP address. The spec puts
   the database on a private network *and* runs migrations, bootstrap and invite as Jobs.
   Both cannot be true today. Pick one:

   - **A (recommended): run the one-off commands from your own computer.** The database
     keeps a public endpoint, but its allow-list holds only your home IP address, so the
     internet cannot reach it. You run the same image with `docker run`, and the owner URL
     stays in your password manager; it is never stored at Scaleway. The cost: you must be
     at an allowed IP to migrate or recover a passkey, and you update the allow-list when
     your home IP changes. This also changes a recorded decision (`docs/ARCHITECTURE.md`,
     Decisions, "Provisioning": "a Serverless Job on Scaleway"). The root of trust becomes
     "the owner password plus an allowed IP" instead of "the ability to run a Job".
   - **B (the spec as written): Serverless Jobs over the public endpoint.** Because Jobs
     have no fixed IP, the allow-list must stay open to all of Scaleway's IP ranges (or to
     everyone). Every Scaleway-hosted machine can then reach the database port. What still
     protects it: a long random password, TLS, and CONNECT revoked from everyone else.

   Switch to Jobs on the private network (the spec's intent) once Scaleway ships VPC support
   for Jobs. Every one-off command below is written for both options.

2. **The spec's one-time SQL leaves the database open to every role** (§14). The spec runs
   `REVOKE CONNECT … FROM PUBLIC` after the admin's temporary membership in `hausbuch_owner`
   has been revoked. Scaleway's admin is not a superuser, so at that point it holds no
   privilege on the database. Postgres then only prints `WARNING: no privileges could be
   revoked` and changes nothing. I reproduced this on PostgreSQL 16 with a non-superuser
   admin: PUBLIC could still connect. Step 4 below uses the corrected order (the revoke
   happens while the admin may still act as the owner) and ends with a check query.

3. **Uploads over 10 MB fail through the web container** until the frontend config
   changes. Next.js's proxy cuts request bodies at 10 MB. The API then waits for the rest,
   and after Next's 30-second proxy timeout the browser gets a 500. Measured locally with a
   25 MiB PDF: 500 after 30.05 s, and the log says `Request body exceeded 10MB`. The fix is
   two settings in `frontend/next.config.ts`, verified locally (the same upload then reached
   the API at once):

   ```ts
   experimental: {
     proxyClientMaxBodySize: "26mb", // 25 MiB file + multipart overhead
     proxyTimeout: 120_000,          // ms; Next's default is 30 s
   },
   ```

   That file belongs to the frontend chunk, so this runbook does not change it. Until it
   lands, documents above 10 MB cannot be uploaded.

4. **In fr-par, a container can start before its private network is ready.** Scaleway
   documents this as a known issue limited to fr-par. The API checks its database role at
   startup. While the database cannot be reached it now retries, up to 6 attempts with
   waits of 1, 2, 4, 8 and 16 s (about a minute at most), logging each attempt as
   `database unreachable at startup` without the URL; then it exits. A role refusal
   (superuser, BYPASSRLS, schema owner) is never retried and exits at once. Scaleway's
   mitigation is also in place: the containers and the database sit on **two private
   networks in the same VPC**, which step 2 does.

5. **The optional nightly `pg_dump` cannot run as a Hausbuch role.** Every table has forced
   row-level security, so `pg_dump` as `hausbuch_owner` stops with `query would be affected
   by row-level security policy`. The tempting `--enable-row-security` flag makes it
   "succeed" with **zero rows** (checked: 0 rows dumped while the database held one
   workspace). That is a backup that looks fine and holds nothing. See "Backups" for what
   works.

Two smaller differences from the spec:

- **Serverless Containers cannot read Secret Manager yet** ("Secrets on Serverless
  Containers are not linked with Secret Manager product yet"). Jobs can. So Secret Manager
  is where each value lives, and for the containers you paste the value into the
  container's *secret environment variables*.
- **Scaleway ignores Docker's `HEALTHCHECK`.** The health check is configured on the
  container itself (step 10).

## Before you start

On your computer:

- Docker (with BuildKit, the default since Docker 23).
- `psql` 16 or later.
- The Scaleway CLI `scw`, signed in with `scw init` (project: the Hausbuch project, region
  `fr-par`).
- `openssl`, for generating keys and passwords.

Choose now, because passkeys are bound to it:

- **The web domain**, e.g. `hausbuch.example.org`. Every passkey is tied to this host name
  (the WebAuthn "RP ID"). Changing it later makes every enrolled passkey useless, and
  everyone has to re-enroll through `hausbuch invite`.

Generate the secrets once and store each in your password manager as you go:

```bash
openssl rand -hex 32   # HAUSBUCH_IBAN_HMAC_KEY
openssl rand -hex 32   # HAUSBUCH_SESSION_KEY (must differ from the IBAN key)
openssl rand -hex 24   # hausbuch_owner database password (hex: URL-safe, no encoding needed)
openssl rand -hex 24   # hausbuch_app database password
```

**Never change `HAUSBUCH_IBAN_HMAC_KEY` once data exists.** Stored tenant IBANs are kept as
keyed hashes, so a new key silently stops every IBAN match. `HAUSBUCH_SESSION_KEY` can be
rotated by redeploying the API: that cancels passkey ceremonies in progress, not sessions.

## Step 1: Project and region

Create one project for everything, so permissions and billing stay together.

Console: Organization → Projects → **Create project**, name `hausbuch`. Use region
**fr-par** for every resource below. The database, bucket and containers must all be in
the same EU region.

## Step 2: VPC and two private networks

The private network lets the API reach the database without it being on the internet. Two
networks in one VPC, because of the fr-par startup issue (point 4 above).

Console: Network → VPC → **Create VPC** `hausbuch-vpc` in fr-par, and make sure **routing**
is enabled on it (containers support cross-network routing). In it, create two private
networks:

- `hausbuch-apps`, for the two containers;
- `hausbuch-db`, for the database.

## Step 3: The PostgreSQL instance

Console: Databases → PostgreSQL and MySQL → **Create Database Instance**:

- engine **PostgreSQL 16**, node type **DB-DEV-S**, region fr-par;
- the initial user is the instance's **admin** (call it `scwadmin`; a long random
  password). It is not a superuser. You use it only for step 4;
- backups: leave **Automatic** (autobackup) on. The default is daily, kept 7 days;
- network: attach the private network **`hausbuch-db`**.

Then, on the instance's page:

- **Network**: note the **private endpoint** (an IP in `hausbuch-db`, port 5432). The API
  uses it.
- **Public endpoint**: Scaleway creates one by default, open to `0.0.0.0/0`.
  - Option A: in **Allowed IPs**, add your home IP as `x.x.x.x/32` and delete
    `0.0.0.0/0`.
  - Option B: replace `0.0.0.0/0` with Scaleway's IP prefixes
    (https://www.scaleway.com/en/peering/), or leave it open if you accept that.
  - (Removing the public endpoint makes the instance re-initialise. Re-adding one gets a new IP
    and port, so it is not something to toggle per migration.)
- Download the instance's **TLS certificate** (for `sslmode=verify-full` from your
  computer).

Do **not** create users or databases in the console's Users tab. Step 4 creates them in SQL,
because the console cannot express the grants Hausbuch needs.

## Step 4: One-time database setup (corrected order)

What this does, in plain words:

1. creates the two Hausbuch roles:
   - `hausbuch_owner` owns the tables; migrations and the CLI use it;
   - `hausbuch_app` is the API's role, with only the privileges the app needs.

   `hausbuch_app` must exist before the first migration, because migration 0001 grants it
   its privileges.
2. lets the admin act as `hausbuch_owner` for a moment, so it can create a database owned by
   that role;
3. while the admin still can, takes the right to connect away from every other role
   (`PUBLIC`) and gives it to the two Hausbuch roles only;
4. withdraws that temporary right from the admin.

Run it once, from your computer, through the public endpoint, as the admin:

```bash
psql "host=<public-ip> port=<public-port> dbname=rdb user=scwadmin sslmode=verify-full sslrootcert=./<instance-cert>.pem"
```

(`rdb` is the default database of a Scaleway instance. Any database the admin may connect to
works.)

```sql
CREATE ROLE hausbuch_owner LOGIN PASSWORD '<owner password>';
CREATE ROLE hausbuch_app   LOGIN PASSWORD '<app password>';
GRANT hausbuch_owner TO scwadmin WITH SET TRUE;
CREATE DATABASE hausbuch OWNER hausbuch_owner;
-- Still able to act as the owner: only the owner can change the database's privileges.
SET ROLE hausbuch_owner;
REVOKE CONNECT ON DATABASE hausbuch FROM PUBLIC;
GRANT CONNECT ON DATABASE hausbuch TO hausbuch_owner, hausbuch_app;
RESET ROLE;
REVOKE hausbuch_owner FROM scwadmin;

-- The check. Expected: f | t | t | f | f | f
SELECT has_database_privilege('public', 'hausbuch', 'CONNECT')       AS public_can_connect,
       has_database_privilege('hausbuch_app', 'hausbuch', 'CONNECT') AS app_can_connect,
       has_database_privilege('hausbuch_owner', 'hausbuch', 'CONNECT') AS owner_can_connect,
       pg_has_role('scwadmin', 'hausbuch_owner', 'SET')               AS admin_can_act_as_owner,
       (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = 'hausbuch_owner') AS owner_unsafe,
       (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = 'hausbuch_app')   AS app_unsafe;
```

If `public_can_connect` is `t`, the REVOKE did not take effect. Do not continue until it is
`f`.

The admin keeps an "admin option" on the two roles, which Postgres 16 grants to whoever
creates a role. It lets the admin reset their passwords, but not act as them or read their
data (`admin_can_act_as_owner` is `f`).

The same steps, on your own machine, are what `scripts/dev-db.sh` does for a local dev
database.

## Step 5: The bucket

A private bucket for document originals; the app never overwrites an object (principle 6).

Console: Storage → Object Storage → **Create bucket** `hausbuch-originals-<random>` (bucket
names are global) in **fr-par**, visibility **private**. The full bucket settings are listed
in the docstring of `backend/src/hausbuch/storage_s3.py`:

- no public ACL or policy;
- encryption at rest on;
- **no lifecycle rules**: retention is the app's decision, never a timer's;
- Object Lock off;
- versioning optional.

```bash
scw object bucket create name=hausbuch-originals-<random> region=fr-par
```

## Step 6: An API key that can only reach the bucket

The API's S3 credentials must not be able to touch anything else.

Console: IAM → Applications → **Create application** `hausbuch-api-s3`. Give it a policy
that grants Object Storage access in the Hausbuch project only. The app needs read, write
and delete on objects, and **list on the bucket**: without list, S3 answers a missing key
with 403 instead of 404. Then create an **API key** for the application, with the Hausbuch
project as its default Object Storage project. Keep the access key and the secret key.

## Step 7: Registry and images

Each image gets an immutable tag (the git commit), so a rollback is "deploy the previous
tag".

Console: Containers → Container Registry → **Create namespace** `hausbuch` in fr-par,
**private**.

```bash
scw registry namespace create name=hausbuch region=fr-par is-public=false
# Log Docker in with any API key of yours (user "nologin", the secret key as password):
echo "$SCW_SECRET_KEY" | docker login rg.fr-par.scw.cloud/hausbuch -u nologin --password-stdin
```

Build and push the **API image** from the repo root:

```bash
TAG=$(git rev-parse --short HEAD)
docker build --platform linux/amd64 -f deploy/api.Dockerfile -t rg.fr-par.scw.cloud/hausbuch/api:$TAG .
docker push rg.fr-par.scw.cloud/hausbuch/api:$TAG
```

The **web image** needs the API container's URL at **build time** (§10.6). Next.js writes
the `/api/*` forwarding rule into the build, and setting `HAUSBUCH_API_URL` on the running
container changes nothing (checked: a container started with a different value still
forwarded to the build-time one). So build it after step 10, once the API URL exists:

```bash
docker build --platform linux/amd64 -f deploy/web.Dockerfile \
  --build-arg HAUSBUCH_API_URL=https://<api-container-endpoint> \
  -t rg.fr-par.scw.cloud/hausbuch/web:$TAG .
docker push rg.fr-par.scw.cloud/hausbuch/web:$TAG
```

A new API URL means a new web image.

## Step 8: Secrets

Secret Manager is the one place every secret value is kept, versioned.

Console: Security & Identity → Secret Manager, in fr-par. Create one secret per value:

| Secret | Value | Used by |
| --- | --- | --- |
| `hausbuch-app-database-url` | `postgresql+psycopg://hausbuch_app:<app password>@<private-ip>:5432/hausbuch?sslmode=require` | API container |
| `hausbuch-owner-database-url` | `postgresql+psycopg://hausbuch_owner:<owner password>@<public-ip>:<public-port>/hausbuch?sslmode=require` | one-off commands **only** |
| `hausbuch-iban-hmac-key` | 64 hex characters | API container |
| `hausbuch-session-key` | 64 hex characters (a different value) | API container |
| `hausbuch-s3-access-key` | the step 6 access key | API container |
| `hausbuch-s3-secret-key` | the step 6 secret key | API container |

`sslmode=require` encrypts the connection but does not check the server's certificate: the
container image does not carry the instance's CA. From your computer, use
`sslmode=verify-full&sslrootcert=<file>` with the certificate from step 3.

With option A the owner URL does not need to be in Secret Manager at all; your password
manager is enough.

## Step 9: Migrations (one-off, as the owner)

Migrations create and change the tables. They run as `hausbuch_owner` and must run before a
new API image that needs them. The image's `hausbuch-migrate` command runs
`alembic upgrade head`. It reads the owner URL from `HAUSBUCH_ADMIN_DATABASE_URL`, the same
variable `bootstrap` and `invite` read. Without it, the command refuses.

**Option A, from your computer** (owner URL in a file only you can read, never in shell
history):

```bash
umask 077
printf 'HAUSBUCH_ADMIN_DATABASE_URL=%s\n' '<owner URL>' > ~/.hausbuch-owner.env
docker run --rm --env-file ~/.hausbuch-owner.env rg.fr-par.scw.cloud/hausbuch/api:$TAG hausbuch-migrate
```

**Option B, as a Serverless Job.** Console: Serverless → Jobs → **Create job definition**
`hausbuch-migrate`:

- image `rg.fr-par.scw.cloud/hausbuch/api:<tag>`;
- the smallest resources;
- **secret reference**: `HAUSBUCH_ADMIN_DATABASE_URL` from `hausbuch-owner-database-url`;
- **startup command** `["hausbuch-migrate"]`;
- no retries.

Run it, and read its logs for `Running upgrade … -> …`.

After the first run, check that the tables are owned by the owner:

```sql
SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname = 'public';   -- hausbuch_owner
```

## Step 10: The API container

Console: Serverless → Containers → **Create namespace** `hausbuch` (fr-par), then **Deploy
container** `hausbuch-api`:

- image `rg.fr-par.scw.cloud/hausbuch/api:<tag>`; port **8080**;
- **min-scale 1, max-scale 1** (§3.7: the worker runs in this process and must never be
  scaled to zero);
- resources: start at the smallest tier that runs it;
- private network **`hausbuch-apps`**; public endpoint **on** (Q14: public for v1);
- **health check**: an **HTTP** liveness probe on path **`/healthz`**, timeout **3 s**.
  The app's own database probe allows itself 2 s, so the 1 s default timeout would mark a
  healthy container as failing;
- environment variables and secrets as in the table below. Set them on the container, not
  on the namespace: namespace-level variables reach every container in the namespace.

The container starts uvicorn on port 8080 with `--timeout-graceful-shutdown 25`. It refuses
to start if its database role is a superuser, BYPASSRLS, or the schema owner. That is how
you notice the owner URL pasted into the wrong place: the container exits with
`SchemaOwnerRoleError`.

Note the container's endpoint (`https://…functions.fnc.fr-par.scw.cloud`). Then check it:

```bash
curl -i https://<api-container-endpoint>/healthz      # 200 {"status":"ok"}
```

### Environment of each container and command

| Variable | `hausbuch-api` | `hausbuch-web` | migrate | bootstrap / invite |
| --- | --- | --- | --- | --- |
| `HAUSBUCH_DATABASE_URL` | **secret**: app role, private IP | — | — | — |
| `HAUSBUCH_ADMIN_DATABASE_URL` | **never** | — | **secret**: owner | **secret**: owner |
| `HAUSBUCH_IBAN_HMAC_KEY` | **secret** | — | — | — |
| `HAUSBUCH_SESSION_KEY` | **secret** | — | — | — |
| `AWS_ACCESS_KEY_ID` | **secret** (step 6) | — | — | — |
| `AWS_SECRET_ACCESS_KEY` | **secret** (step 6) | — | — | — |
| `HAUSBUCH_RP_ID` | `hausbuch.example.org` | — | — | — |
| `HAUSBUCH_ORIGIN` | `https://hausbuch.example.org` | — | — | same value (the enrollment link's host) |
| `HAUSBUCH_BLOB_STORE` | `s3` | — | — | — |
| `HAUSBUCH_S3_BUCKET` | the step 5 bucket | — | — | — |
| `HAUSBUCH_S3_ENDPOINT_URL` | `https://s3.fr-par.scw.cloud` | — | — | — |
| `HAUSBUCH_S3_REGION` | `fr-par` | — | — | — |
| `HAUSBUCH_RUN_WORKER` | `true` | — | — | — |
| `HAUSBUCH_API_URL` | — | **build argument**, not a variable (step 7) | — | — |

The API's optional settings keep their defaults unless you have a reason to change them:

- `HAUSBUCH_COOKIE_SECURE` (`true`);
- `HAUSBUCH_MAX_UPLOAD_BYTES` (25 MiB);
- `HAUSBUCH_SESSION_MAX_AGE_S` (12 h) and `HAUSBUCH_SESSION_IDLE_S` (2 h);
- `HAUSBUCH_WORKER_CONCURRENCY` (2);
- `HAUSBUCH_SHUTDOWN_GRACE_S` (20, which must stay below the kill timeout, Q15).

`PORT` is set by Scaleway; `SCW_*` names are reserved.

## Step 11: The web container and the domain

Build and push the web image now (step 7, second half), with the API endpoint from step 10.

Console: in the same namespace, **Deploy container** `hausbuch-web`:

- image `rg.fr-par.scw.cloud/hausbuch/web:<tag>`, port **8080**;
- min-scale 0 is fine: it is stateless, and a cold start costs one slow page load;
- private network `hausbuch-apps`;
- no secrets; it needs none.

**Custom domain**:

1. at your DNS provider, add a CNAME from `hausbuch.example.org` to the web container's
   default endpoint, and wait until it resolves;
2. in the container's **Network & Domains** tab, add the domain. Scaleway issues the TLS
   certificate.

The API container's own URL is never used by a browser.

Check: `https://hausbuch.example.org/login` loads, and
`curl -i https://hausbuch.example.org/api/auth/me` returns `401 {"error":"unauthenticated",…}`
from the API through the web container.

## Step 12: Bootstrap the workspace and enroll at once

`hausbuch bootstrap` creates the workspace, your user and a one-time enrollment link, valid
24 hours. It refuses to run a second time.

**Option A, from your computer:**

```bash
docker run --rm --env-file ~/.hausbuch-owner.env -e HAUSBUCH_ORIGIN=https://hausbuch.example.org \
  rg.fr-par.scw.cloud/hausbuch/api:$TAG \
  hausbuch bootstrap --workspace "Haushalt Rivas" --email luis@example.org --name "Luis"
```

**Option B, as a Job:**

- a job definition `hausbuch-cli`: same image, the owner-URL secret reference, and
  `HAUSBUCH_ORIGIN` as a plain variable;
- run it with the contextual startup command `["hausbuch"]` and the arguments
  `["bootstrap", "--workspace", "Haushalt Rivas", "--email", "luis@example.org", "--name", "Luis"]`.

It prints:

```
Workspace 8f1c… created. Enrol the first passkey within 24 h:
https://hausbuch.example.org/enroll#token=…   (shown once; not stored)
```

**The rule for enrollment links (§10.7):** the link is printed to standard output. With
option B that means it is in the Job's logs, where anyone with log access can read it. That
is acceptable because the link works once and expires after 24 hours. So:

- **open the link and enroll your passkey immediately** after the command finishes;
- **if the link is refused** ("already used" or "invalid"), assume someone else used it.
  Run `hausbuch invite` at once with both revoke flags, which deletes every passkey and
  session of that user (the intruder's included), then enroll with the new link:

  ```bash
  docker run --rm --env-file ~/.hausbuch-owner.env -e HAUSBUCH_ORIGIN=https://hausbuch.example.org \
    rg.fr-par.scw.cloud/hausbuch/api:$TAG \
    hausbuch invite --email luis@example.org --revoke-passkeys --revoke-sessions
  ```

  (As a Job: the `hausbuch-cli` definition with the arguments
  `["invite", "--email", "luis@example.org", "--revoke-passkeys", "--revoke-sessions"]`.)

The same `invite` command is the recovery path when all passkeys are lost (§6.7). Your
spouse joins from the app: Einstellungen → "Mitglied einladen".

## Checks to run at the first deploy, and record here

These are open questions from the spec (§13) that only the real platform can answer. Fill
in the results below and commit them.

### Q16: does the bucket honour "If-None-Match" (no-overwrite uploads)?

Run the store's tests against the real bucket from your computer. They write under a fresh
prefix and delete it afterwards:

```bash
cd backend
HAUSBUCH_TEST_S3_BUCKET=<bucket> \
HAUSBUCH_TEST_S3_ENDPOINT_URL=https://s3.fr-par.scw.cloud \
HAUSBUCH_TEST_S3_REGION=fr-par \
AWS_ACCESS_KEY_ID=<step 6 access key> AWS_SECRET_ACCESS_KEY=<step 6 secret key> \
uv run pytest tests/test_storage_s3.py -v -rsx
```

`test_real_bucket_honours_if_none_match` **passes** if the conditional PUT is honoured and
**xfails** (with the reason) if it is not. Either way the originals stay safe: content
addressing plus the SHA-256 check on every read cover it (§8).

### Q13: how large a request body does the platform accept?

The app accepts uploads up to 25 MiB. This checks whether Scaleway lets that through.

1. **The platform, straight at the API container** (no session needed: a body the platform
   refuses comes back as 413 from Scaleway; a body that reaches the app gets the app's 401):

   ```bash
   { printf '%%PDF-1.7\n'; head -c 26214391 /dev/urandom; } > big25.pdf   # 25 MiB
   curl -sS -o /dev/null -w '%{http_code} in %{time_total}s\n' -X POST \
     -H 'Origin: https://hausbuch.example.org' \
     -F 'file=@big25.pdf;type=application/pdf' \
     https://<api-container-endpoint>/api/documents
   ```

   `401` means the body got through; `413` (or a reset) means the platform limit is lower.
2. **End to end, signed in:** once the `next.config.ts` fix (point 3 at the top) has shipped,
   upload `big25.pdf` on `/documents`. It must succeed within the 120 s proxy timeout.

If the platform limit is below 25 MiB, lower `HAUSBUCH_MAX_UPLOAD_BYTES` to fit, and leave
presigned uploads to phase 2 (Q13's recommendation).

### Q15: how long does Scaleway wait between SIGTERM and killing a container?

The API needs at least 30 s: the worker's 20 s grace, inside uvicorn's 25 s graceful
shutdown. Measure it with the throwaway probe in `deploy/sigterm-probe.Dockerfile`. It
ignores SIGTERM and logs once a second until it is killed:

```bash
docker build --platform linux/amd64 -f deploy/sigterm-probe.Dockerfile -t rg.fr-par.scw.cloud/hausbuch/sigterm-probe deploy
docker push rg.fr-par.scw.cloud/hausbuch/sigterm-probe
```

1. deploy it as a temporary container (port 8080, min-scale 1);
2. once it is ready, trigger a stop: redeploy it, or set min-scale 0 and wait for
   scale-down;
3. in its logs (Cockpit), the last `alive Ns after SIGTERM` line is the kill timeout;
4. delete the probe container and its image.

If the result is below 30 s, lower `HAUSBUCH_SHUTDOWN_GRACE_S`. Uvicorn's
`--timeout-graceful-shutdown 25` lives in `deploy/api.Dockerfile`, so lowering it means a new
image. Keep the order: grace < uvicorn's timeout < the platform's kill timeout.

### Also note

- **`X-Forwarded-For`**: Scaleway sets `X-Forwarded-For` and `X-Real-IP` on every request
  to a container. For the API, `X-Real-IP` is the web container's address, not the
  browser's. Nothing uses client IPs yet; it matters only if per-IP rate limiting (Q9) is
  ever added. Scaleway itself warns that `X-Forwarded-For` can be forged by clients.
- **Q14, a private API container**: Scaleway containers can now have a *private endpoint*
  (`http://<container-id>.<private-network-id>.internal`) reachable from the same VPC. That
  may let the web container reach the API privately and the API's public endpoint be
  switched off, without the token-forwarding `proxy.ts` the spec assumed. That would need a
  web image built with the private URL; untested.

### Results (fill in at the first deploy)

| Check | Date | Result |
| --- | --- | --- |
| Q16 If-None-Match honoured on the bucket | | |
| Q13 platform body limit (25 MiB straight to the API) | | |
| Q13 end-to-end 25 MiB upload through the web container | | |
| Q15 SIGTERM → kill timeout | | |
| Option A or B chosen for one-off commands | | |

## Deploying an update

1. Build and push both images with the new commit's tag (step 7). The web image only needs
   a rebuild when `frontend/` changed or the API URL changed.
2. If the commit adds migrations, run `hausbuch-migrate` with the **new** image first (step
   9). Migrations are append-only and additive, so the old API keeps working against the
   new schema while the new one rolls out.
3. Point `hausbuch-api` at the new tag. Scaleway starts the new instance and switches
   traffic once it is ready. Then the old instance gets SIGTERM and drains: new requests
   get 503 `shutting_down`, which the web client retries once.
4. Point `hausbuch-web` at its new tag.
5. Check `/healthz` and sign in.

## Rolling back

- **A bad image:** point the container back at the previous tag. Tags are commit hashes and
  never reused, so "the previous version" is exact.
- **A failed deploy** (the new instance never becomes ready): Scaleway keeps the previous
  version serving for up to **24 hours**, then removes both. So fix or roll back the same
  day.
- **Migrations are not rolled back.** There is no `alembic downgrade` in production: a
  downgrade can drop data. Roll the app back to an image that works with the newer schema;
  additive migrations make that the normal case. If a migration itself damaged data,
  restore the database from a backup (below), after checking with yourself what the restore
  loses.

## Backups

- **Scaleway's automatic backup**: daily, kept **7 days** (step 3). This is the backup.
  Restoring creates the data as of that backup. Test one restore into a new instance or
  database once, early, and note the date here.
- **Object Storage** holds the originals and is not covered by the database backup.
  Versioning on the bucket (step 5) is the optional extra protection.
- **A nightly `pg_dump` to the bucket is optional, and not possible with Hausbuch's own
  roles** (point 5 at the top). It needs a role that bypasses row-level security, and only
  a role that has `BYPASSRLS` itself can create one. Check whether Scaleway's admin does:

  ```sql
  SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;
  ```

  If it does not, there is no supported way, and the automatic backup stays the only one.
  Never use `pg_dump --enable-row-security` as a workaround: it produces a valid-looking
  dump with no rows.

## Local development

`scripts/dev-db.sh` builds the same database layout on a local PostgreSQL 16:

- the two roles;
- a database owned by `hausbuch_owner`;
- CONNECT for the two roles only (in the corrected order);
- migrations as the owner.

It then prints the `HAUSBUCH_DATABASE_URL` and `HAUSBUCH_ADMIN_DATABASE_URL` to export. It is
safe to re-run, and `--help` lists its options.
