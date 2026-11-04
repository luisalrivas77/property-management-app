"""Pydantic schemas: the typed contract for the API, the database and rule fixtures.

Phase 1 (vault and ledger). See docs/phases/phase-1.md.
Every enum here mirrors a CHECK constraint in migrations/versions/0001_phase1_vault_ledger.py;
tests/db/test_schema_consistency.py fails if the two drift apart.
"""
