"""Phase 5: automation. Six tables, two CHECK widenings and one workspace switch.

Spec: docs/phases/phase-5.md §8 (D117, D119, D121-D123, D125, D126).
- `bank_connection` (D119): one FinTS connection per account; BLZ, https URL, login name, the
  chosen TAN security function, the client state blob and its HMAC (both null or both set; the
  MAC is HMAC-SHA256, 32 bytes), the auto-sync switch and the last approval / success. Mutable
  with events. **No secret column**: the PIN and the product id are container secrets.
- `bank_sync` (D118): one row per run with its status (a restartable state machine), window,
  counts, balance source, the import it produced (same account: composite FK) and error code.
  Mutable with events.
- `inbound_mail` (D121): one row per attempt at a forwarded mail, keyed by the SHA-256 of its
  Message-ID; **no address, subject, body or header column**. Append-only.
- `inbound_mail_attachment`: the documents (and imports) an accepted mail produced. Append-only.
- `reminder_ack` (D122, §6.3): done / snoozed / muted / unmuted; scope_id is polymorphic (no
  FK), null exactly for the workspace scope. Append-only.
- `notification` (D123): one row per send attempt for a reminder instance, lead, channel and
  recipient, written in the same transaction as the send's result. A failed send is retried on
  a later run, at most three attempts (`attempt` 1-3, unique per item); at most one `sent` row
  per item (partial unique index). Append-only.
- `statement_import.balance_source` gains `bank` (D117). 0006 wrote the CHECK inline, so
  Postgres named it statement_import_balance_source_check; it is dropped by name without IF
  EXISTS, so a wrong name fails loudly. `(balance_source = 'none') = (declared_closing IS NULL)`
  is a separate CHECK and unchanged.
- `tax_input` (D126): kind `grundsteuer_schedule`, year-scoped (the first year the Bescheid
  applies to) and document-required (the Bescheid); property-scoped with no allocation, unit or
  loan, which 0013's other CHECKs already ensure. The three kind CHECKs (named by 0014/0015)
  are dropped and recreated as 0015 and 0018 do.
- `workspace.reminder_emails boolean NOT NULL DEFAULT false` (D123), as 0005's two switches;
  the app role's table-level UPDATE on workspace (0001) covers it.
Every new table carries workspace_id, forced row-level security keyed on it, and composite FKs.
Grants: SELECT, INSERT, UPDATE on the two mutable tables; SELECT, INSERT on the four
append-only ones (forbid_mutation on UPDATE, DELETE and TRUNCATE).

The downgrade is refused while it would drop data or leave a row the old CHECKs refuse (as
0013 to 0018): any row of the six tables, an import with balance_source = 'bank', a
grundsteuer_schedule tax input, an account with source = 'fints' (0006 allows the value; a
fints account means a connection existed), or a workspace with reminder_emails on. It lifts
FORCE RLS for each check, since the owner has no workspace set. It then drops the tables and
the column and restores the earlier CHECKs under the same names.

Revision ID: 0019_automation
Revises: 0018_income_tax_return
Create Date: 2026-11-10
"""

from alembic import op

revision = "0019_automation"
down_revision = "0018_income_tax_return"
branch_labels = None
depends_on = None

# Parents before children. Reversed for downgrade.
TABLES = [
    "bank_connection",
    "bank_sync",
    "inbound_mail",
    "inbound_mail_attachment",
    "reminder_ack",
    "notification",
]
APPEND_ONLY = ["inbound_mail", "inbound_mail_attachment", "reminder_ack", "notification"]

APP_ROLE = "hausbuch_app"
APP_GRANTS = {
    "bank_connection": "SELECT, INSERT, UPDATE",  # mutable + events
    "bank_sync": "SELECT, INSERT, UPDATE",  # mutable + events
    "inbound_mail": "SELECT, INSERT",  # append-only
    "inbound_mail_attachment": "SELECT, INSERT",  # append-only
    "reminder_ack": "SELECT, INSERT",  # append-only
    "notification": "SELECT, INSERT",  # append-only
}

# hausbuch.schemas.bank_sync.SyncTrigger / SyncStatus.
SYNC_TRIGGERS = ("manual", "scheduled")
SYNC_STATUSES = (
    "queued",
    "running",
    "needs_approval",
    "importing",
    "done",
    "nothing_new",
    "failed",
)
FINAL_SYNC_STATUSES = ("done", "nothing_new", "failed")
# hausbuch.schemas.inbound_mail.InboundDecision.
INBOUND_DECISIONS = ("accepted", "rejected", "failed")
# hausbuch.schemas.reminders.ReminderScope / ReminderAckAction / NotificationChannel / Status.
REMINDER_SCOPES = ("workspace", "property", "loan", "tenancy", "owner", "account")
ACK_ACTIONS = ("done", "snoozed", "muted", "unmuted")
CHANNELS = ("email",)
NOTIFICATION_STATUSES = ("sent", "failed")

# 0006's statement_import.balance_source list, in its order; hausbuch.schemas.enums.BalanceSource.
OLD_BALANCE_SOURCES = ("file", "user", "none")
BANK = "bank"
BALANCE_SOURCES = (*OLD_BALANCE_SOURCES, BANK)

# 0018's tax_input lists, in its order (0015 + private_cost).
OLD_TAX_INPUT_KINDS = (
    "spread_82b",
    "renovation_treatment",
    "exclude_from_15pct",
    "market_rent",
    "ruecklage_spent",
    "interest_certified",
    "pre_handover_income",
    "useful_life",
    "movable_asset",
    "private_cost",
)
OLD_YEAR_SCOPED = (
    "ruecklage_spent",
    "interest_certified",
    "useful_life",
    "movable_asset",
    "private_cost",
)
OLD_DOCUMENT_REQUIRED = ("ruecklage_spent", "interest_certified", "useful_life")
GRUNDSTEUER_SCHEDULE = "grundsteuer_schedule"

KIND_CHECK = "tax_input_kind_check"
YEAR_CHECK = "tax_input_year_scope_check"
DOCUMENT_CHECK = "tax_input_year_document_check"
BALANCE_SOURCE_CHECK = "statement_import_balance_source_check"


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{name}'" for name in names)


DDL = rf"""
CREATE TABLE bank_connection (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id      uuid NOT NULL REFERENCES workspace (id),
    account_id        uuid NOT NULL UNIQUE,
    bank_code         text NOT NULL CHECK (bank_code ~ '^[0-9]{{8}}$'),
    fints_url         text NOT NULL CHECK (fints_url LIKE 'https://%'
                          AND char_length(fints_url) <= 500),
    login_name        text NOT NULL CHECK (char_length(login_name) BETWEEN 1 AND 100),
    tan_mechanism     text CHECK (char_length(tan_mechanism) BETWEEN 1 AND 8),
    client_data       bytea,
    client_data_mac   bytea CHECK (octet_length(client_data_mac) = 32),
    auto_sync         boolean NOT NULL DEFAULT false,
    sca_confirmed_at  timestamptz,
    last_sync_at      timestamptz,
    last_booking_on   date,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, account_id, id),
    FOREIGN KEY (workspace_id, account_id) REFERENCES account (workspace_id, id),
    -- D119: the client state is stored only with its MAC.
    CHECK ((client_data IS NULL) = (client_data_mac IS NULL))
);
COMMENT ON TABLE bank_connection IS
    'D119: one FinTS connection per account. No secret column: the PIN and the product id are '
    'container secrets; client_data is deconstruct(including_private=False), MAC''d.';

CREATE TABLE bank_sync (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id     uuid NOT NULL REFERENCES workspace (id),
    account_id       uuid NOT NULL,
    connection_id    uuid NOT NULL,
    trigger          text NOT NULL CHECK (trigger IN ({_sql_list(SYNC_TRIGGERS)})),
    status           text NOT NULL DEFAULT 'queued'
                         CHECK (status IN ({_sql_list(SYNC_STATUSES)})),
    requested_from   date,
    requested_to     date,
    import_id        uuid,
    lines_total      integer CHECK (lines_total >= 0),
    lines_new        integer CHECK (lines_new >= 0),
    balance_source   text CHECK (balance_source IN ({_sql_list(BALANCE_SOURCES)})),
    balance_checked  boolean NOT NULL DEFAULT false,
    error_code       text CHECK (char_length(error_code) <= 64),
    started_at       timestamptz,
    finished_at      timestamptz,
    created_by       uuid,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, account_id, id),
    FOREIGN KEY (workspace_id, account_id) REFERENCES account (workspace_id, id),
    -- The connection and the import are the same account's.
    FOREIGN KEY (workspace_id, account_id, connection_id)
        REFERENCES bank_connection (workspace_id, account_id, id),
    FOREIGN KEY (workspace_id, account_id, import_id)
        REFERENCES statement_import (workspace_id, account_id, id),
    FOREIGN KEY (workspace_id, created_by) REFERENCES app_user (workspace_id, id),
    CHECK (requested_from <= requested_to),
    CHECK ((requested_from IS NULL) = (requested_to IS NULL)),
    CHECK (lines_new <= lines_total),
    -- D127: an import exists only for a run that found new lines; a run that fails after its
    -- import was written keeps the link to the lines it stored. nothing_new never has one.
    CHECK (import_id IS NULL OR status IN ('importing', 'done', 'failed')),
    CHECK (status <> 'nothing_new' OR lines_new = 0),
    CHECK ((status IN ({_sql_list(FINAL_SYNC_STATUSES)})) = (finished_at IS NOT NULL)),
    CHECK (finished_at >= started_at)
);
CREATE INDEX bank_sync_account_started
    ON bank_sync (workspace_id, account_id, started_at DESC);

CREATE TABLE inbound_mail (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id          uuid NOT NULL REFERENCES workspace (id),
    message_id_hash       bytea NOT NULL CHECK (octet_length(message_id_hash) = 32),
    attempt               smallint NOT NULL CHECK (attempt BETWEEN 1 AND 3),
    received_at           timestamptz,
    decision              text NOT NULL CHECK (decision IN ({_sql_list(INBOUND_DECISIONS)})),
    code                  text CHECK (char_length(code) <= 64
                              AND code ~ '^[a-z_]+\.[a-z_]+$'),
    attachments_total     smallint CHECK (attachments_total >= 0),
    attachments_accepted  smallint CHECK (attachments_accepted >= 0),
    recorded_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, message_id_hash, attempt),
    CHECK (attachments_accepted <= attachments_total)
);
COMMENT ON TABLE inbound_mail IS
    'D121: one attempt at a forwarded mail. The SHA-256 of its Message-ID only: no address, '
    'subject, body or header is stored.';

CREATE TABLE inbound_mail_attachment (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES workspace (id),
    mail_id       uuid NOT NULL,
    document_id   uuid NOT NULL,
    import_id     uuid,
    recorded_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, mail_id, document_id),
    FOREIGN KEY (workspace_id, mail_id) REFERENCES inbound_mail (workspace_id, id),
    FOREIGN KEY (workspace_id, document_id) REFERENCES document (workspace_id, id),
    FOREIGN KEY (workspace_id, import_id) REFERENCES statement_import (workspace_id, id)
);

CREATE TABLE reminder_ack (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id   uuid NOT NULL REFERENCES workspace (id),
    reminder_key   text NOT NULL CHECK (char_length(reminder_key) <= 64
                       AND reminder_key ~ '^[a-z_]+\.[a-z_]+$'),
    scope_type     text NOT NULL CHECK (scope_type IN ({_sql_list(REMINDER_SCOPES)})),
    scope_id       uuid,
    due_on         date,
    action         text NOT NULL CHECK (action IN ({_sql_list(ACK_ACTIONS)})),
    snoozed_until  date,
    recorded_at    timestamptz NOT NULL DEFAULT now(),
    recorded_by    uuid,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, recorded_by) REFERENCES app_user (workspace_id, id),
    CHECK ((scope_type = 'workspace') = (scope_id IS NULL)),
    CHECK ((action = 'snoozed') = (snoozed_until IS NOT NULL)),
    -- muted / unmuted are for the key and scope, every instance; done / snoozed one instance.
    CHECK ((action IN ('muted', 'unmuted')) = (due_on IS NULL))
);
CREATE INDEX reminder_ack_key_scope ON reminder_ack (workspace_id, reminder_key, scope_id);

CREATE TABLE notification (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id       uuid NOT NULL REFERENCES workspace (id),
    reminder_id        text NOT NULL CHECK (char_length(reminder_id) BETWEEN 1 AND 160),
    lead_days          smallint NOT NULL,
    channel            text NOT NULL CHECK (channel IN ({_sql_list(CHANNELS)})),
    recipient_user_id  uuid NOT NULL,
    sent_at            timestamptz NOT NULL DEFAULT now(),
    status             text NOT NULL CHECK (status IN ({_sql_list(NOTIFICATION_STATUSES)})),
    error_code         text CHECK (char_length(error_code) <= 64),
    -- A failed send is retried on a later run, up to three attempts in all.
    attempt            smallint NOT NULL CHECK (attempt BETWEEN 1 AND 3),
    UNIQUE (workspace_id, id),
    -- Two rows never claim one attempt.
    UNIQUE (workspace_id, reminder_id, lead_days, channel, recipient_user_id, attempt),
    FOREIGN KEY (workspace_id, recipient_user_id) REFERENCES app_user (workspace_id, id),
    CHECK ((status = 'failed') = (error_code IS NOT NULL))
);
-- One successful send per reminder instance, lead, channel and recipient.
CREATE UNIQUE INDEX notification_sent_once
    ON notification (workspace_id, reminder_id, lead_days, channel, recipient_user_id)
    WHERE status = 'sent';
"""

APPEND_ONLY_TRIGGERS = "".join(
    f"""
CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
"""
    for table in APPEND_ONLY
)

WORKSPACE_SWITCH = """
ALTER TABLE workspace ADD COLUMN reminder_emails boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN workspace.reminder_emails IS
    'D123: the daily reminder digest is mailed. Changed with workspace.settings_changed.';
"""


def _set_balance_sources(names: tuple[str, ...]) -> None:
    op.execute(f"ALTER TABLE statement_import DROP CONSTRAINT {BALANCE_SOURCE_CHECK}")
    op.execute(
        f"ALTER TABLE statement_import ADD CONSTRAINT {BALANCE_SOURCE_CHECK}"
        f" CHECK (balance_source IN ({_sql_list(names)}))"
    )


def _drop_tax_input_checks() -> None:
    for name in (DOCUMENT_CHECK, YEAR_CHECK, KIND_CHECK):
        op.execute(f"ALTER TABLE tax_input DROP CONSTRAINT {name}")


def _add_tax_input_checks(
    kinds: tuple[str, ...], year_scoped: tuple[str, ...], document_required: tuple[str, ...]
) -> None:
    op.execute(
        f"ALTER TABLE tax_input ADD CONSTRAINT {KIND_CHECK} CHECK (kind IN ({_sql_list(kinds)}))"
    )
    op.execute(
        f"ALTER TABLE tax_input ADD CONSTRAINT {YEAR_CHECK}"
        f" CHECK ((kind IN ({_sql_list(year_scoped)})) = (tax_year IS NOT NULL))"
    )
    op.execute(
        f"ALTER TABLE tax_input ADD CONSTRAINT {DOCUMENT_CHECK}"
        f" CHECK (kind NOT IN ({_sql_list(document_required)}) OR document_id IS NOT NULL)"
    )


def _rls(table: str) -> str:
    return f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON {table}
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
"""


def _if_app_role(body: str) -> str:
    return f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        {body}
    ELSE
        RAISE NOTICE 'role {APP_ROLE} does not exist; skipping grants';
    END IF;
END
$$;
"""


# What the downgrade would drop or leave behind the old CHECKs.
_DATA_CHECKS = (
    *((table, f"SELECT 1 FROM {table}", f"table {table} holds rows") for table in TABLES),
    (
        "statement_import",
        f"SELECT 1 FROM statement_import WHERE balance_source = '{BANK}'",
        "an import declares the bank balance",
    ),
    (
        "tax_input",
        f"SELECT 1 FROM tax_input WHERE kind = '{GRUNDSTEUER_SCHEDULE}'",
        "a tax input is grundsteuer_schedule",
    ),
    (
        "account",
        "SELECT 1 FROM account WHERE source = 'fints'",
        "an account is synced by FinTS",
    ),
    (
        "workspace",
        "SELECT 1 FROM workspace WHERE reminder_emails",
        "a workspace has reminder emails switched on",
    ),
)

REFUSE_IF_DATA = (
    "DO $$\nBEGIN"
    + "".join(
        f"""
    ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
    IF EXISTS ({query}) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: {what}'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;
    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;"""
        for table, query, what in _DATA_CHECKS
    )
    + "\nEND\n$$;"
)


def upgrade() -> None:
    op.execute(DDL)
    op.execute(APPEND_ONLY_TRIGGERS)
    for table in TABLES:
        op.execute(_rls(table))
    _set_balance_sources(BALANCE_SOURCES)
    _drop_tax_input_checks()
    _add_tax_input_checks(
        (*OLD_TAX_INPUT_KINDS, GRUNDSTEUER_SCHEDULE),
        (*OLD_YEAR_SCOPED, GRUNDSTEUER_SCHEDULE),
        (*OLD_DOCUMENT_REQUIRED, GRUNDSTEUER_SCHEDULE),
    )
    op.execute(WORKSPACE_SWITCH)
    lines = [f"GRANT {privs} ON {table} TO {APP_ROLE};" for table, privs in APP_GRANTS.items()]
    op.execute(_if_app_role("\n        ".join(lines)))


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    op.execute("ALTER TABLE workspace DROP COLUMN reminder_emails")
    _drop_tax_input_checks()
    _add_tax_input_checks(OLD_TAX_INPUT_KINDS, OLD_YEAR_SCOPED, OLD_DOCUMENT_REQUIRED)
    _set_balance_sources(OLD_BALANCE_SOURCES)
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
