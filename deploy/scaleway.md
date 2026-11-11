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
- **Admin commands, run from your own computer** with `uv` from the repo checkout (no
  Docker): database migrations, `hausbuch bootstrap` (first workspace and user) and
  `hausbuch invite` (a fresh enrollment link, e.g. after a lost phone). Only these use the
  database **owner** password, which stays on your computer; the API never has it.
- **Managed PostgreSQL 16** (DB-DEV-S) on a private network for the API, plus a public
  endpoint that only your computer's IP address may reach, for the admin commands.
- **An Object Storage bucket** (private, EU) for the original documents.
- **A Container Registry namespace** for the two images, which a GitHub Actions job builds
  and pushes (`.github/workflows/publish-images.yml`), so your computer needs no Docker.
- **Secret Manager** as the one place where every secret value is kept.

Everything lives in one Scaleway project in region **fr-par** (Paris). The expected cost is
**about €20/month excluding VAT** at this load (`docs/ARCHITECTURE.md`, stack table). That
is an estimate from November 2026. Re-check it on Scaleway's live price pages before you
create anything; the API container's min-scale 1 means it is billed around the clock.

## Read this first: five things the spec did not know

The first deploy will run into these. All five are handled in the steps below.

1. **Admin commands run from your computer, not as Serverless Jobs** (decided 5 Nov 2026,
   `docs/ARCHITECTURE.md`, Decisions, "Admin commands"). Migrations, `hausbuch bootstrap`
   and `hausbuch invite` run on your own computer with `uv`, straight from the repo
   checkout. They reach the database through its public endpoint, whose allow-list holds
   only your computer's current IP address, so the rest of the internet cannot reach the
   database port. The owner URL (`HAUSBUCH_ADMIN_DATABASE_URL`) lives only on your computer;
   it is never stored at Scaleway. The root of trust is "the owner password plus an allowed
   IP". The cost: you must be at an allowed IP to migrate or recover a passkey, and you
   update the allow-list when your IP changes ("Admin commands from your computer" below).

   *Not used: Serverless Jobs.* The spec first planned Jobs. Scaleway's Jobs FAQ says
   "Serverless Jobs does not currently support Scaleway VPC or Private Networks, though this
   feature is under development", and Jobs have no fixed IP address. A Job could therefore
   only reach the database through a public endpoint open to all of Scaleway's IP ranges,
   which is much wider than one home IP. Revisit Jobs once they can join a private network.

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
  Containers are not linked with Secret Manager product yet"). So Secret Manager is where
  each container value lives, and you paste the value into the container's *secret
  environment variables*.
- **Scaleway ignores Docker's `HEALTHCHECK`.** The health check is configured on the
  container itself (step 10).

## Before you start

On your computer:

- A checkout of this repository.
- `uv`, for the admin commands (installed in "Admin commands from your computer" below).
- No Docker: the images are built and pushed by GitHub Actions (step 7), and the admin
  commands use `uv`.
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
With the bank sync set up (step 14) it also costs one pushTAN approval: the stored FinTS
connection is signed under a key derived from it, so the next sync discards it and asks again
(D119).

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
- **Public endpoint**: Scaleway creates one by default, open to `0.0.0.0/0`. Keep the
  endpoint (the admin commands use it) but close it to everyone except you: in **Allowed
  IPs**, add your computer's current public IPv4 address as `x.x.x.x/32` and **delete
  `0.0.0.0/0`**. `curl -4 https://ifconfig.me` prints that address. How to update it when
  it changes is in "Admin commands from your computer" below. (Removing the public endpoint
  makes the instance re-initialise, and re-adding one gets a new IP and port, so it is not
  something to toggle per migration.)
- Note the public endpoint's **IP and port**: they go into the owner URL.
- Download the instance's **TLS certificate**. Step 4's `psql` uses it
  (`sslmode=verify-full`), and the owner URL may too (stronger than its `sslmode=require`
  minimum, see step 8). The CLI prints it as a JSON string; this saves it as a PEM file
  outside the repository:

  ```bash
  mkdir -p ~/.hausbuch
  scw rdb instance get-certificate <instance-id> region=fr-par -o json \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).rstrip())' > ~/.hausbuch/hausbuch-db.pem
  ```

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

Run it once, from your computer, through the public endpoint, as the admin. The script
`deploy/scaleway-db-setup.sql` holds the SQL below and asks for the two role passwords with
psql's `\password`, which hides what you type and sends only a hash, so no password lands on
screen, in your shell history or in the server log. Run it in your own terminal (it needs
typed input):

```bash
psql "host=<public-ip> port=<public-port> dbname=rdb user=scwadmin sslmode=verify-full sslrootcert=$HOME/.hausbuch/hausbuch-db.pem" \
  -f deploy/scaleway-db-setup.sql
```

(`rdb` is the default database of a Scaleway instance. Any database the admin may connect to
works.) What the script runs, with passwords inline instead of `\password`:

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
- encryption at rest on. **Scaleway creates buckets with it off** ("Bucket encryption:
  disabled"), and neither `scw object bucket create` nor the app turns it on: enable it in
  the console (bucket → settings → Bucket encryption, Scaleway-managed keys) **before the
  first upload**, since it covers objects written from then on. Done 5 Nov 2026;
- **no lifecycle rules**: retention is the app's decision, never a timer's;
- Object Lock off;
- versioning **on** (switched on 5 Nov 2026): a deleted original is kept as a hidden
  "noncurrent" version and can be restored. It does not stop a leaked API key, which may
  delete versions too, and once on it can only be suspended, never switched off.
  **Before the retention auto-delete job ships**, add one lifecycle rule that expires
  noncurrent versions after 30 days. Without it, a document the app deletes for retention
  (tenant data included) survives as a hidden copy, so GDPR erasure would not be real. That
  rule never touches a current object, so the app still decides when a file goes.

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

**Scaleway API keys expire after one year** (the key created here on 5 Nov 2026 expires around
5 Nov 2027; IAM → API keys shows the exact date). Nothing warns you: when it lapses, every
upload and download fails with an S3 auth error. Put the date in your calendar a few weeks
ahead. To rotate: create a new key for the same application, put it into the container's
secret variables (step 8, step 10), redeploy, check an upload, then delete the old key. The
GitHub registry key (step 7) expires the same way; when it lapses, `publish-images` fails at
login.

## Step 7: Registry and images

Each image gets an immutable tag (the git commit), so a rollback is "deploy the previous
tag".

The images are built and pushed by a GitHub Actions job, `publish-images`
(`.github/workflows/publish-images.yml`), started by hand. No Docker on your computer
(decided 5 Nov 2026). It builds only from `main`, and it refuses to overwrite a tag that
already exists.

**The registry namespace.** Console: Containers → Container Registry → **Create namespace**
`hausbuch` in fr-par, **private**. Or: `scw registry namespace create name=hausbuch
region=fr-par is-public=false`. Namespace names are shared by everyone in the region. If
`hausbuch` is taken, pick another name, then in GitHub (Settings → Secrets and variables →
Actions → **Variables**) create the variable `SCW_REGISTRY_NAMESPACE` holding that name.
Below, `hausbuch` stands for whichever name you got.

**A key that can only push images** (once):

1. Console: IAM → Applications → **Create application** `hausbuch-ci-registry`. Give it a
   policy with **ContainerRegistryFullAccess** in the Hausbuch project only. It must not
   reach the database, the bucket or the secrets.
2. Create an **API key** for that application. Scaleway shows the secret key **once**.
3. Go straight to GitHub: Settings → Secrets and variables → Actions → **New repository
   secret**, name `SCW_REGISTRY_SECRET_KEY`, value the secret key. Paste it only there:
   not into a chat, a terminal you share, or a file. GitHub never shows it again either; to
   replace it, create a new key, update the secret, and delete the old key.

**Run it:** GitHub → Actions → **publish-images** → **Run workflow**, branch `main`:

- **First the API image:** image `api`. It is tagged with the first 12 characters of the
  commit, e.g. `api:3f9c2a1b7d4e`, shown on the run's summary page.
- **The web image comes after step 10**, once the API container's URL exists. It needs that
  URL at **build time** (§10.6): Next.js writes the `/api/*` forwarding rule into the build,
  and setting `HAUSBUCH_API_URL` on the running container changes nothing (checked: a
  container started with a different value still forwarded to the build-time one). Run with
  image `web` and **api_url** set to `https://<api-container-endpoint>` (host only, no path).
  Its tag is `<commit>-<8 characters derived from the URL>`, so a new API URL on the same
  commit gets a new tag instead of overwriting the old one.
- `both` builds the two together, for updates once the API URL is fixed.

The summary page lists each pushed image's full name; that is what you point the container
at in steps 10 and 11.

## Step 8: Secrets

Secret Manager is the one place every secret value is kept, versioned.

Console: Security & Identity → Secret Manager, in fr-par. Create one secret per value the
containers use:

| Secret | Value | Used by |
| --- | --- | --- |
| `hausbuch-app-database-url` | `postgresql+psycopg://hausbuch_app:<app password>@<private-ip>:5432/hausbuch?sslmode=require` | API container |
| `hausbuch-iban-hmac-key` | 64 hex characters | API container |
| `hausbuch-session-key` | 64 hex characters (a different value) | API container |
| `hausbuch-s3-access-key` | the step 6 access key | API container |
| `hausbuch-s3-secret-key` | the step 6 secret key | API container |
| `hausbuch-anthropic-api-key` | the Claude API key of the `hausbuch` Console workspace (step 13) | API container |
| `hausbuch-inbox-imap-user` | the Gmail address whose label is read (step 16) | API container |
| `hausbuch-inbox-imap-password` | the Google app password named `Hausbuch` (step 16) | API container |
| `hausbuch-inbox-address` | the token address `<user>+<token>@gmail.com` (step 16) | API container |
| `hausbuch-inbox-label` | `Hausbuch` (step 16) | API container |

`sslmode=require` encrypts the connection but does not check the server's certificate: the
container image does not carry the instance's CA.

**The owner URL is not stored at Scaleway.** It is kept only on your computer, in your
password manager and in the admin env file described in "Admin commands from your
computer" below:

```
postgresql+psycopg://hausbuch_owner:<owner password>@<public-ip>:<public-port>/hausbuch?sslmode=require
```

`sslmode=require` is the minimum: never connect to the public endpoint without TLS. Since
your computer has the certificate from step 3, you can make it stronger with
`?sslmode=verify-full&sslrootcert=/absolute/path/to/<instance-cert>.pem`, which also checks
that you are talking to your instance.

## Admin commands from your computer

Migrations, `hausbuch bootstrap` and `hausbuch invite` run on your computer, from the repo
checkout, as `hausbuch_owner`. No Docker and no image: `uv` installs the backend's exact
locked dependencies and Python 3.12 into `backend/.venv`. Set this up once; steps 9 and 12
and every later migration use it.

**1. Install uv** (once):

```bash
# macOS or Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# or, on a Mac with Homebrew:  brew install uv
# Windows (PowerShell):  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv --version
```

**2. Install the `hausbuch` CLI from the checkout** (once, and again after each `git pull`):

```bash
cd backend && uv sync --python 3.12
```

That one line creates `backend/.venv` with the `hausbuch` command and Alembic. Run them
from `backend/` as `uv run hausbuch …` and `uv run alembic …`. `uv sync` downloads Python
3.12 itself if your computer does not have it.

**3. The admin env file** (once). It holds the owner URL from step 8 and the web origin
that enrollment links point to. Create it so that only you can read it, and paste the
URL in rather than typing it into a command, so it never lands in your shell history:

```bash
umask 077
cat > ~/.hausbuch-owner.env     # paste the two lines below, then press Ctrl-D
ls -l ~/.hausbuch-owner.env     # -rw------- : readable by you only
```

```
HAUSBUCH_ADMIN_DATABASE_URL=postgresql+psycopg://hausbuch_owner:<owner password>@<public-ip>:<public-port>/hausbuch?sslmode=require
HAUSBUCH_ORIGIN=https://hausbuch.example.org
```

This file and your password manager are the only places the owner URL exists. Never copy
it to Scaleway, into the API container, into the repository, or into a chat. `uv run
--env-file ~/.hausbuch-owner.env …` loads it for one command only, so it is not left in
your shell's environment either.

**4. Your IP must be on the database's allow-list.** The public endpoint accepts only the
addresses in its **Allowed IPs** (step 3). A home connection's public IP changes now and
then, and a different network (travel, a phone hotspot) has a different one. Before an
admin command, compare:

```bash
curl -4 https://ifconfig.me; echo                                    # your current IPv4
scw rdb instance list region=fr-par                                  # the instance's ID
scw rdb acl list instance-id=<instance-id> region=fr-par             # what is allowed
```

If they differ, replace the allow-list with your current address only. In the console:
the instance → **Allowed IPs** → add `<new-ip>/32`, then delete the old entry. With the
CLI, one command replaces every rule (check the argument names with
`scw rdb acl set --help` if it complains; this form was run with scw 2.65 on 5 Nov 2026):

```bash
scw rdb acl set <new-ip>/32 instance-id=<instance-id> descriptions.0="Luis admin" region=fr-par
```

Never add `0.0.0.0/0` back. After a trip, set the list back to your home IP. A connection
from an address that is not allowed does not get an error from Postgres; it times out
(`connection timeout expired` or `Operation timed out`), so a hang at connect usually means
the allow-list.

## Step 9: Migrations (one-off, as the owner)

Migrations create and change the tables. They run as `hausbuch_owner` and must run before a
new API image that needs them. Run them from your computer, from a checkout of **the same
commit as the image you are about to deploy**, so the migrations and the code match:

```bash
git checkout <commit of the image tag> && cd backend && uv sync --python 3.12
uv run --env-file ~/.hausbuch-owner.env sh -c \
  'HAUSBUCH_DATABASE_URL="$HAUSBUCH_ADMIN_DATABASE_URL" exec alembic upgrade head'
```

Alembic reads `HAUSBUCH_DATABASE_URL`; the `sh -c` hands it the owner URL for this one
process only. That is what the image's `hausbuch-migrate` script does too, so the owner URL
never sits in a variable under the API's name. (On Windows, run these from WSL or Git Bash,
which have `sh`.) The upgrade prints nothing when it succeeds; confirm the version with the
same command and `alembic current` instead of `alembic upgrade head`. It must print the
newest migration followed by `(head)`.

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
- privacy **Public** (the app signs users in itself; a Private container would demand a
  Scaleway token from the web container), and **HTTPS connections only** ticked;
- protocol **HTTP/1**. The console's HTTP/2 option means the container itself speaks
  cleartext HTTP/2 (h2c), which uvicorn does not: every request would fail. Browsers still
  get HTTP/2 from Scaleway's edge;
- leave the command and arguments empty (the image starts uvicorn) and the request timeout
  at its default of 300 s (at least the web proxy's 120 s);
- autoscaling: any rule, with minimum and maximum both `1`;
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
curl -i https://<api-container-endpoint>/healthz      # 200 {"status":"ok","worker":"ok"}
```

### Environment of each container and command

The last two columns are your computer's `~/.hausbuch-owner.env`, not anything at Scaleway.

| Variable | `hausbuch-api` | `hausbuch-web` | migrate (your computer) | bootstrap / invite (your computer) |
| --- | --- | --- | --- | --- |
| `HAUSBUCH_DATABASE_URL` | **secret**: app role, private IP | — | — | — |
| `HAUSBUCH_ADMIN_DATABASE_URL` | **never** | — | owner, public endpoint | owner, public endpoint |
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
| `HAUSBUCH_ANTHROPIC_API_KEY` | **secret** (step 13) | — | — | — |
| `HAUSBUCH_AI_ENABLED` | `true` once step 13 is done (default `false`) | — | — | — |
| `HAUSBUCH_FINTS_PIN` | **secret** (step 14), optional | — | — | — |
| `HAUSBUCH_FINTS_PRODUCT_ID` | **secret** (step 14), optional | — | — | — |
| `HAUSBUCH_INBOX_IMAP_USER` | **secret** (step 16), optional | — | — | — |
| `HAUSBUCH_INBOX_IMAP_PASSWORD` | **secret** (step 16), optional | — | — | — |
| `HAUSBUCH_INBOX_ADDRESS` | **secret** (step 16), optional | — | — | — |
| `HAUSBUCH_INBOX_LABEL` | `Hausbuch` (step 16), optional | — | — | — |
| `HAUSBUCH_API_URL` | — | **build argument**, not a variable (step 7) | — | — |

The API's optional settings keep their defaults unless you have a reason to change them:

- `HAUSBUCH_COOKIE_SECURE` (`true`);
- `HAUSBUCH_MAX_UPLOAD_BYTES` (25 MiB);
- `HAUSBUCH_SESSION_MAX_AGE_S` (12 h) and `HAUSBUCH_SESSION_IDLE_S` (2 h);
- `HAUSBUCH_WORKER_CONCURRENCY` (2);
- `HAUSBUCH_SHUTDOWN_GRACE_S` (20, which must stay below the kill timeout, Q15).

`PORT` is set by Scaleway; `SCW_*` names are reserved.

## Step 11: The web container and the domain

Push the web image now: run `publish-images` with image `web` and the API endpoint from
step 10 as `api_url` (step 7).

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
24 hours. It refuses to run a second time. Run it from your computer, in `backend/` (set
up in "Admin commands from your computer"; `HAUSBUCH_ORIGIN` comes from the env file):

```bash
uv run --env-file ~/.hausbuch-owner.env \
  hausbuch bootstrap --workspace "Haushalt Rivas" --email luis@example.org --name "Luis"
```

It prints:

```
Workspace 8f1c… created. Enrol the first passkey within 24 h:
https://hausbuch.example.org/enroll#token=…   (shown once; not stored)
```

**The rule for enrollment links (§10.7):** the link is printed to standard output, here
your own terminal, and it is the only key to the account until a passkey is enrolled. It
works once and expires after 24 hours. So:

- **open the link and enroll your passkey immediately** after the command finishes. Do not
  paste it into a chat, a ticket or a note; clear the terminal's scrollback once you have
  enrolled;
- **if the link is refused** ("already used" or "invalid"), assume someone else used it.
  Run `hausbuch invite` at once with both revoke flags, which deletes every passkey and
  session of that user (the intruder's included), then enroll with the new link:

  ```bash
  uv run --env-file ~/.hausbuch-owner.env \
    hausbuch invite --email luis@example.org --revoke-passkeys --revoke-sessions
  ```

The same `invite` command is the recovery path when all passkeys are lost (§6.7). Your
spouse joins from the app: Einstellungen → "Mitglied einladen".

## Step 13: The Claude API key (phase 2, AI pre-fill)

Phase 2 reads uploaded documents with the Claude API (phase-2.md §5, §11.3). The API key is
the only new secret. It is never in the database, the repository, a log line, an `ai_call`
row or the frontend; `Settings` hides it in `repr` like the other secrets.

**Order: migration `0005` first.** It adds the job queue's tables, `ai_call` and
`proposal`. Run it from your computer exactly as in step 9, from the commit of the step 1
image, **before** deploying that image (as with `0004`). `alembic current` must print
`0005_jobs_ai_proposals (head)` or later.

1. **Two Console workspaces, two keys** (platform.claude.com → Settings → Workspaces), so
   either key can be revoked without touching the other:
   - `hausbuch`, monthly spend limit **$25**, for the deployed app;
   - `hausbuch-evals`, monthly spend limit **$10**, whose key stays on your computer for the
     eval recordings (`backend/tests/evals/README.md`).

   In each workspace set **`default_inference_geo` = `us`** (decided 6 Nov 2026, D17: one
   named processing location; it costs 1.1x). The app also sends `inference_geo` on every
   request (`HAUSBUCH_AI_INFERENCE_GEO`, default `us`), and each `ai_call` row records the
   geo the API reports back, so a mismatch is visible.
2. Create **one API key** in the `hausbuch` workspace. Copy it once; the Console does not
   show it again.
3. **Secret Manager** (step 8): create the secret `hausbuch-anthropic-api-key` with the key
   as its value.
4. **API container** (step 10): add the secret environment variable
   `HAUSBUCH_ANTHROPIC_API_KEY`, pasting the secret's value (containers cannot read Secret
   Manager directly), and the plain variable `HAUSBUCH_AI_ENABLED=true`. The container
   refuses to start with `HAUSBUCH_AI_ENABLED=true` and no key. Leave `HAUSBUCH_RUN_WORKER`
   at `true`: the reading job runs in this container.
5. **Redeploy** the container and check that the worker is up:

   ```bash
   curl -s https://<api-container-endpoint>/healthz    # {"status":"ok","worker":"ok"}
   ```

   `"worker":"stopped"` means the job worker is not running (it restarts itself after a
   crash, with backoff); look at the container logs for `worker_crashed` and its
   `exc_type`. The health check stays green either way, since requests are still served.
6. Upload one short, non-tenant document (an invoice) with AI on, and check that its
   proposal turns `ready`. Tenant documents stay blocked until the workspace setting
   `ai_tenant_documents` is switched on (D16, §11).

**Rotation** (yearly, or at once if a key may have leaked):

1. create a new key in the `hausbuch` Console workspace;
2. add it to `hausbuch-anthropic-api-key` as a **new secret version**;
3. paste it into the container's `HAUSBUCH_ANTHROPIC_API_KEY`;
4. redeploy, and check `/healthz` and one upload as above;
5. **delete the old key** in the Console. Do this last, so the running container never holds
   a revoked key.

The `hausbuch-evals` key is rotated the same way, minus the container steps: replace it in
your computer's env file only.

## Step 14: The bank sync of the rental account (phase 5, D115–D120)

The API container can fetch the rental account's statements from the Sparkasse itself, over
FinTS (the German banks' online-banking protocol), read-only: the app has no method that
could send a transfer, and every transfer would still need an approval on the phone. A sync
is started with **Jetzt abrufen** on the account page (Konten › the account › Bankabruf); a
daily schedule comes with a later step. No migration is needed (the tables came with
`0019`).

Two values are secrets: the **online-banking PIN** and the app's **FinTS product id**, the
registration number the Deutsche Kreditwirtschaft (DK) sends after the product registration.
They are never in the database, a job, an event, a log line or the repository, and the page
never asks for the PIN.

**Where they live: the API container's own secret environment variables.** Serverless
Containers cannot reference Secret Manager (their secrets are not linked with it), so the
container's secret variables hold the values and are what the app reads (decided by Luis,
11 Nov 2026). A copy in Secret Manager is optional, only as a recoverable record of the
values; it is not a step, and the container never reads it.

1. **Before you start**, have three things the page asks for and two the container needs:
   - the **BLZ** (Bankleitzahl): eight digits, characters 5 to 12 of the account's IBAN;
   - the **FinTS address** (an `https://` URL): a Sparkasse publishes it on its website,
     usually on the online-banking help page under "FinTS" or "HBCI PIN/TAN" (finance
     software settings); the DK's bank list has it too (next point);
   - the **Anmeldename**: the login name or Legitimations-ID you use for online banking;
   - the **PIN** of that login, and the **product id** from the DK's registration email (25
     characters at most).
2. **Subscribe to the DK's bank list** (the FinTS institute list the DK offers to
   registered products, sent by email when it changes). It is how you learn that the
   Sparkasse's FinTS address moved before a sync fails with `bank.unreachable`.
3. **API container** (step 10): in **one update**, add `HAUSBUCH_FINTS_PIN` and
   `HAUSBUCH_FINTS_PRODUCT_ID` as **secret environment variables**, then **redeploy**. Both
   or neither: the container refuses to start with a PIN and no product id, or the reverse.
   A product id that is longer than 25 characters does not stop the container; every sync
   fails with `bank.not_configured` instead. `HAUSBUCH_RUN_WORKER` stays `true`: the sync is
   a job of this container's worker.
4. **The account page**: open Konten › the rental account. In the Bankabruf card, fill in
   BLZ, FinTS-Adresse and Anmeldename and press **Bankabruf einrichten**. The page shows the
   login shortened only (first two and last two characters).
5. **The first sync**: press **Jetzt abrufen**. The S-pushTAN app on your phone asks for an
   approval; approve it within five minutes, while the card counts down. The first sync
   fetches everything since the day of the account's Anfangssaldo, stores the bank's camt
   files as one ZIP (it appears in the imports list labelled "Bankabruf") and imports it
   exactly like an uploaded file. Later syncs inside about 90 days need no approval. The
   card shows "Freigabe gültig bis etwa …" as an estimate, never a guarantee.
6. **Record the first real sync's answers** in phase-5.md (Q-O34 to Q-O36: which camt
   format the bank offers and whether its file matches the export, whether a later sync
   runs without an approval, whether the bank's references equal the export's). The
   container log has one INFO line from `hausbuch.jobs.bank_sync` naming the camt formats
   the bank offered; it holds no account data.

**When a sync fails**, the card and Zu prüfen › Automatisch say why in plain words, with
the code. `bank.refused` means the bank turned the login down: check the Anmeldename, and
the PIN in the container. **Three wrong PINs lock the online-banking login** at the bank,
so after one `bank.refused` correct the PIN before trying again. `bank.not_configured` means
the two variables are missing or the product id is too long. `bank.unreachable` (three tries
over about two and a half minutes) usually clears by itself; if it persists, compare the
FinTS address with the bank's website and the DK list.

**Rotation**: change the container variable and redeploy. A new **PIN** (changed in online
banking) goes into `HAUSBUCH_FINTS_PIN` the same day, or every sync fails with
`bank.refused`. A new **product id** (the DK re-registers a product version) goes into
`HAUSBUCH_FINTS_PRODUCT_ID`. If you keep a copy in Secret Manager, add a new version there
too. Changing the FinTS address or the Anmeldename on the page (Zugang ändern) drops the
stored connection, so the next sync asks for an approval again; so does a new
`HAUSBUCH_SESSION_KEY`.

**To switch the sync off**, remove both variables in one update and redeploy: every start
is then refused with `bank.not_configured` and the bank is never contacted.

## Step 16: Email-in from the Gmail label (phase 5, D121)

The API container reads one Gmail label of the household's own account every 10 minutes
over IMAP, read-only, and keeps only the attachments of mails a member forwarded to a token
address (phase-5.md §5, D121). Nothing in the mailbox is changed: the app opens the label
with `EXAMINE`, fetches with `BODY.PEEK` (so a mail is not even marked read), and never
creates the label. No migration is needed (the tables came with `0019`).

**What the app password can do.** A Google app password opens the **whole** mailbox. The
limit to one label is the app's own code, which sends only `CAPABILITY`, `LOGIN`, `EXAMINE`,
`UID SEARCH`, `UID FETCH` and `LOGOUT` (a test enforces the list). That is the informed
choice of 10 Nov 2026 over a separate account. The password is a container secret, never a
database column, a log line or an event; revoking it on Google's page stops the poll at
once.

1. **Google account**: turn on 2-Step Verification, by a method other than security keys
   only (an app password is not offered under Advanced Protection). Then Security →
   2-Step Verification → **App passwords**: create one named `Hausbuch` and copy it once.
2. **Choose a token**: 8 or more random characters, for example from
   `python3 -c "import secrets; print(secrets.token_hex(5))"`. The forwarding address is
   `<user>+<token>@gmail.com`. The token is what keeps a stranger's mail out of the label,
   so it is shown only to signed-in members (Einstellungen › Posteingang-Adresse).
3. **Gmail filter** (Settings → Filters → Create a new filter): To
   `<user>+<token>@gmail.com` → *Apply the label* `Hausbuch` (create the label here), and
   optionally *Skip the Inbox*. Only mails to the token address get the label; the app reads
   nothing else. If the label is missing or renamed, the poll logs `email.label_missing`,
   shows a card on Zu prüfen and creates nothing.
4. **Secret Manager** (step 8): four secrets, `hausbuch-inbox-imap-user` (the Gmail
   address), `hausbuch-inbox-imap-password` (the app password), `hausbuch-inbox-address`
   (the token address) and `hausbuch-inbox-label` (`Hausbuch`).
5. **API container** (step 10): add `HAUSBUCH_INBOX_IMAP_USER`,
   `HAUSBUCH_INBOX_IMAP_PASSWORD` and `HAUSBUCH_INBOX_ADDRESS` as secret environment
   variables, pasting the secrets' values (the token address is what keeps strangers out,
   so it is treated as a secret too), and `HAUSBUCH_INBOX_LABEL=Hausbuch` as a plain one. `HAUSBUCH_INBOX_IMAP_HOST` defaults to
   `imap.gmail.com` and `HAUSBUCH_INBOX_AUTHSERV_ID` to `mx.google.com`; leave both. The
   container refuses to start with the user and no password (or the reverse), or with a
   login and no address or label. While the four are unset the poll does nothing.
   `HAUSBUCH_RUN_WORKER` stays `true`: the poll is a periodic job of this container's
   worker.
6. **Redeploy**, open Einstellungen › Posteingang-Adresse, and check that the token address
   is shown.
7. **Test mail (Q-O47)**: from a member's own address (the email the member signed up
   with; any other sender is rejected as `email.sender_not_allowed`), forward one
   non-tenant PDF, for example an invoice, to the token address. Within 10 minutes Zu prüfen
   shows "Anhang aus einer weitergeleiteten Mail übernommen." in the Automatisch section.
   The container log has one JSON line per poll with `"event": "inbound_mail_polled"`,
   for example `"status": "done", "accepted": 1, "rejected": 0, "failed": 0, "codes": {…}`.

   If the mail was rejected instead, its `codes` say why. **`email.auth_missing`** answers
   Q-O47 with "no": Gmail did not put its own `Authentication-Results` header on the copy it
   delivered to itself. Record the answer in phase-5.md Q-O47 either way. The log line names
   counts and codes only, never an address, a subject or a file name.

**What the log line's `status` can say** (anything but `done` is logged as a warning):
`"email.auth_rejected"` means Google refused the login (the app password revoked or
mistyped, or 2-Step Verification turned off); `"email.label_missing"` means the label is
gone or renamed; `"connect_failed"` means the connection to `imap.gmail.com` never came up
(the line's `exc_type` names the error); `"connection_lost"` means it broke in the middle,
and the next poll starts that mail again; `"skipped"` means `imaplib.Debug` was on or not
exactly one workspace exists. The line's `unreadable` counts mails whose listing Gmail
refused (or answered in a form the app cannot read) even when asked for alone: they are
skipped and asked for again at the next poll, and the other mails go on.

**The card for a poll that needs you.** Three of these outcomes show a card in Zu prüfen ›
Automatisch, titled "Posteingang per E-Mail", until a later poll completes:
`email.auth_rejected` (the text says to create a new app password and paste it as the
secret), `email.label_missing` (create the label again or correct its name in the secret),
and `email.unreachable`, which is `connect_failed` three polls in a row, about 30 minutes
(one failed connect is usually a blip and shows nothing; the count is kept in the running
container only, so a restart starts it again). Each change is one History event on the
workspace (`workspace.inbox_poll_failed` with the code, then
`workspace.inbox_poll_recovered`), never one per poll. `connection_lost`, `skipped` and a
database error stay log lines only.

**Rotation or revocation**: create a new app password, add it as a new version of
`hausbuch-inbox-imap-password`, paste it into the container, redeploy, then revoke the old
one on Google's page. To switch email-in off, remove the four variables and redeploy, then
revoke the app password.

**Tenant documents are better uploaded than forwarded.** A forwarded lease meets the
`ai_tenant_documents` switch exactly as an upload does (D16), but it has passed through
Google first.

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
ignores SIGTERM and logs once a second until it is killed. Push it with the
`publish-images` job (step 7), image `sigterm-probe`; it lands as
`sigterm-probe:<commit>`. Then:

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
| Q16 If-None-Match honoured on the bucket | 6 Nov 2026 | **Honoured.** A conditional PUT over an existing key gets 412 `PreconditionFailed` and the original stays byte-for-byte intact, with a single version kept. **But** the next request on the same connection then fails: Scaleway's front end answers it with a bodiless `400` (`text/html`, `connection: close`, no S3 error code) and drops the connection; the request after that, or one on a fresh connection, succeeds. Seen on GET twice out of two, not on one HEAD. The real-bucket test `test_real_bucket_honours_if_none_match` fails on exactly that GET (75 other real-bucket tests pass). Effect on the app: only `S3BlobStore.put`'s lost-race path (or a botocore retry after a lost PUT response) follows a 412 with a request, so a rare racing upload can fail with a 500 instead of succeeding; nothing stored is harmed. Fixed the same day (#21): the S3 client retries such a 400 (no `x-amz-request-id`) once; see `docs/phases/phase-1.md` implementation status item 6. Rerun against the real bucket after the fix, the same day: all real-bucket tests pass, `test_real_bucket_honours_if_none_match` included. Found with two throwaway diagnostics run from Luis's computer, which cleaned up after themselves. |
| Q13 platform body limit (25 MiB straight to the API) | 5 Nov 2026 | Passes: a 25 MiB multipart POST to `/api/documents` without a session got the app's 401 in 5.4 s, so the platform let the whole body through. The same body through `hausbuch.rcubed.fr` (web container proxy) got 401 in 6.5 s, so the `next.config.ts` limits hold too. |
| Q13 end-to-end 25 MiB upload through the web container | 6 Nov 2026 | Not run, by decision: the largest real document is about 300 KB, the platform limit above already passes, and a signed-in upload of a real document stored and opened correctly. Run it only if large scans appear. |
| Q15 SIGTERM → kill timeout | 6 Nov 2026 | About 270 s. The probe (`sigterm-probe:9426e23715cb`, 100 mVCPU / 128 MB, min = max = 1) was rolled by an environment-variable change; its last log line was `alive 269s after SIGTERM` and nothing followed in the next 11 minutes. Well above the 30 s the API needs (20 s worker grace inside uvicorn's 25 s), so `HAUSBUCH_SHUTDOWN_GRACE_S` and the uvicorn timeout stay as they are. Probe container and image deleted afterwards. Note: `scw container container logs` returns at most 100 entries, so read the last one, not the first. |
| Public endpoint reachable only from your IP (from another network, `psql` times out) | 6 Nov 2026 | Passes: from home (the allow-listed IP) `pg_isready` against the public endpoint answers "accepting connections"; from a phone hotspot (a different IPv4, checked with `curl -4 ifconfig.me`) it got "no response" twice with a 10 s timeout. `pg_isready` needs no credentials, so no secret was used. |

## Deploying an update

1. Merge the change to `main`, then run `publish-images` (step 7) with `api`, `web` or
   `both`. The web image only needs a rebuild when `frontend/` changed or the API URL
   changed.
2. If the commit adds migrations, wait until `publish-images` has finished, then run them,
   from your computer with the **new** commit checked out (step 9; check the allow-list holds
   your current IP), and go straight on to step 3 in the same sitting. Most migrations are
   additive, so the old API keeps working against the new schema while the new one rolls
   out. **A migration that removes or renames a column the previous image reads breaks the
   old image until the new one serves.** The first is 0016 (phase 4): it drops
   `owner.marginal_tax_rate`, so every screen that loads owners (overview, Eigentümer,
   Steuern, Immobilien tabs, Mieter, Mietverhältnisse, Einheiten, document detail) fails
   from the migration until the new API tag is live. The reverse order fails too (the new
   image reads `owner.church_tax`), so keep migration-first and keep the gap to minutes.
3. Point `hausbuch-api` at the new tag (console, or `scw container container update <id>
   region=fr-par image=rg.fr-par.scw.cloud/hausbuch/api:<tag>`; the field is `image`, and the
   older `registry-image` is accepted but silently changes nothing). Scaleway starts the new instance and switches
   traffic once it is ready. Then the old instance gets SIGTERM and drains: new requests
   get 503 `shutting_down`, which the web client retries once.
4. Point `hausbuch-web` at its new tag.
5. Check `/healthz` and sign in.

## Rolling back

- **A bad image:** point the container back at the previous tag. Tags are built from commit hashes
  and never overwritten (the job refuses), so "the previous version" is exact.
- **A failed deploy** (the new instance never becomes ready): Scaleway keeps the previous
  version serving for up to **24 hours**, then removes both. So fix or roll back the same
  day.
- **Migrations are not rolled back.** There is no `alembic downgrade` in production: a
  downgrade can drop data. Roll the app back to an image that works with the newer schema;
  additive migrations make that the normal case. After a migration that removed a column the
  older images read (0016 onwards), no older image works, so fix forward instead. If a migration itself damaged data,
  restore the database from a backup (below), after checking with yourself what the restore
  loses.

## Backups

- **Scaleway's automatic backup**: daily, kept **7 days** (step 3). This is the backup.
  Restoring creates the data as of that backup. Test one restore into a new instance or
  database once, early, and note the date here.
- **Object Storage** holds the originals and is not covered by the database backup.
  Versioning on the bucket (step 5, on since 5 Nov 2026) is the extra protection.
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
