"""Workspace = the row-level-security tenant. Not the same thing as an Owner."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from .common import Record, Schema


class Workspace(Schema):
    """One household. Every other table carries workspace_id and is filtered by RLS."""

    id: UUID
    name: str = Field(min_length=1, max_length=200)
    created_at: datetime


class AppUser(Record):
    """A person who can sign in. Auth credentials (passkey / TOTP) land with the API PR."""

    email: str = Field(pattern=r"^[^@\sA-Z]+@[^@\sA-Z]+$")  # stored lower-case
    display_name: str = Field(min_length=1)
