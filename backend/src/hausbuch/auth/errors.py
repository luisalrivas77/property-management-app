"""Auth errors. Messages are fixed strings: they never carry a token, a credential id, an email
or anything else the client sent, so they are safe to log."""


class AuthError(Exception):
    """Base class of every hausbuch.auth error."""


class AuthenticationFailed(AuthError):
    """A ceremony, invite or credential did not verify. The API answers 401 with no detail.

    `reason` is a fixed, short code for the warning log line (e.g. "ceremony.expired")."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class LastPasskeyError(AuthError):
    """The user's last passkey cannot be deleted from the UI (§6.7): 409."""


class PasskeyNotFoundError(AuthError, LookupError):
    """No such passkey of the signed-in user: 404."""


class InviteNotFoundError(AuthError, LookupError):
    """No such pending invite in the workspace: 404."""


class DuplicateUserError(AuthError):
    """An app_user with that email exists already (emails are unique across workspaces): 409."""


class DuplicateCredentialError(AuthError):
    """This credential is registered already: 409."""


class WorkspaceExistsError(AuthError):
    """`hausbuch bootstrap` without --another-workspace on a database that has one."""


class UserNotFoundError(AuthError, LookupError):
    """`hausbuch invite`: no user with that email (in the given workspace)."""
