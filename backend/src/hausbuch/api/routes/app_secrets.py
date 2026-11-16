"""Provider keys and URLs typed in the app (docs/phases/phase-6.md D260): the card
„Zugangsschlüssel (API keys)“ on Einstellungen.

- GET /app-secrets: every allowlisted name with its label, what it is for, its container
  variable's name and where its value comes from now (`source`: app, container, none or
  unreadable) with when it was typed; never a value, its length or its ciphertext.
- PUT /app-secrets/{name}: store the value, encrypted; 200 with the name's state, never the
  value. 422 rule_violation app_secret.invalid (empty, a blank or control character, more
  than 512 characters) or app_secret.url_invalid (an RPC address Settings would refuse as a
  container variable too); the body is never echoed. After a chain key or URL, each wallet
  with an active address on a chain that reads it is queued for a sync in the same
  transaction (principle 7: the addresses waiting for the key are read now, not tomorrow).
- DELETE /app-secrets/{name}: delete it; the container variable, if any, applies again. 204,
  or 404 when none was stored.

Any signed-in member may set or delete a value; nobody can read one (D196's rule).
"""

from uuid import UUID

from fastapi import Response
from pydantic import SecretStr
from sqlalchemy import select

from hausbuch.db.tables import wallet_address as address_table
from hausbuch.jobs.sync_wallets import enqueue_sync_wallet
from hausbuch.rules.chain.registry import registry
from hausbuch.schemas.api import ErrorKind
from hausbuch.schemas.app_secrets import AppSecretName, AppSecretPut, AppSecrets, AppSecretState
from hausbuch.schemas.chain import Chain
from hausbuch.services.app_secrets import delete_secret, secret_states, set_secret
from hausbuch.services.chain.base import SOURCE_SECRETS
from hausbuch.services.context import CommandContext
from hausbuch.settings import Settings

from ..deps import Cmd, SettingsDep, domain_router
from ..errors import ApiError

router = domain_router()


def wallets_reading(ctx: CommandContext, name: AppSecretName) -> list[UUID]:
    """The wallets (RLS) with an active address on a chain whose source reads `name`."""
    rows = ctx.conn.execute(
        select(address_table.c.account_id, address_table.c.chain)
        .where(address_table.c.active.is_(True))
        .distinct()
    ).all()
    wallets: list[UUID] = []
    for account_id, chain in rows:
        kind = registry().chain(Chain(chain)).source
        if name in SOURCE_SECRETS.get(kind, ()) and account_id not in wallets:
            wallets.append(account_id)
    return sorted(wallets)


def set_and_requeue(
    ctx: CommandContext, name: AppSecretName, value: SecretStr, *, settings: Settings
) -> AppSecretState:
    state = set_secret(ctx, name, value, settings=settings)
    for account_id in wallets_reading(ctx, name):
        enqueue_sync_wallet(ctx.conn, workspace_id=ctx.workspace_id, account_id=account_id)
    return state


@router.get("/app-secrets")
def app_secrets_get(cmd: Cmd, settings: SettingsDep) -> AppSecrets:
    return AppSecrets(secrets=cmd.read(secret_states, settings))


@router.put("/app-secrets/{name}")
def app_secret_set(
    name: AppSecretName, body: AppSecretPut, cmd: Cmd, settings: SettingsDep
) -> AppSecretState:
    """The value is encrypted before it reaches the database; the answer names its source."""
    return cmd.run(set_and_requeue, name, body.value, settings=settings)


@router.delete("/app-secrets/{name}", status_code=204)
def app_secret_delete(name: AppSecretName, cmd: Cmd) -> Response:
    if not cmd.run(delete_secret, name):
        raise ApiError(404, ErrorKind.NOT_FOUND, "no value is stored in the app for this name")
    return Response(status_code=204)
