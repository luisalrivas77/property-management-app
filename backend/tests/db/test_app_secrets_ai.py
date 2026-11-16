"""An Anthropic key typed in the app (docs/phases/phase-6.md D260): the worker's jobs resolve
their workspace's key when they run, a typed key wins over the container's, one client serves
every job with the same key, a typed key that no longer decrypts gives no client (never the
container's), and AI enabled with no key anywhere ends a read at gate 1 (ai.disabled) and
leaves a line unmatched. A client handed in (tests, evals) is kept. Over the fakes of
test_jobs_document_read and test_jobs_matching; invented keys only."""

import dataclasses
from datetime import date

import pytest
from db.test_jobs_document_read import _pdf, _run, recorder, setup  # noqa: F401  (fixtures)
from db.test_jobs_matching import _answer, _rows, world  # noqa: F401  (world is a fixture)
from db.test_jobs_matching import _deps as matching_deps
from db.test_jobs_matching import _run as run_match
from db.test_jobs_queue import _letter_responses
from pydantic import SecretStr

from hausbuch.ai.fake import FakeModelClient
from hausbuch.ai.per_workspace import WorkspaceModelClients
from hausbuch.schemas.app_secrets import AppSecretName as N
from hausbuch.services import app_secrets

pytestmark = pytest.mark.db

CONTAINER = "sk-ant-container-invented-0001"
TYPED = "sk-ant-typed-invented-0002"
OTHER_KEY = bytes(range(101, 133))


class Factory:
    """Builds a FakeModelClient per key and remembers which keys it was asked for."""

    def __init__(self, responses=None) -> None:
        self.keys: list[str] = []
        self.clients: list[FakeModelClient] = []
        self.responses = responses

    def __call__(self, key: str) -> FakeModelClient:
        self.keys.append(key)
        client = FakeModelClient(self.responses() if self.responses else [])
        self.clients.append(client)
        return client


def _resolving(deps, factory):
    """The worker's deps: no client handed in, a resolver per workspace."""
    return dataclasses.replace(
        deps, model_client=None, model_clients=WorkspaceModelClients(deps.settings, factory)
    )


def _type(engine, ws, settings, value=TYPED):
    from hausbuch.db.session import workspace_transaction
    from hausbuch.schemas.enums import ActorType
    from hausbuch.services.context import Actor, new_command

    with workspace_transaction(engine, ws) as conn:
        ctx = new_command(conn, ws, Actor(ActorType.USER, "user:test"))
        app_secrets.set_secret(ctx, N.ANTHROPIC_API_KEY, SecretStr(value), settings=settings)


def test_the_resolver_prefers_the_typed_key_and_caches_one_client_per_key(
    setup,  # noqa: F811
    read,
    app_engine,
):
    deps, ws, *_ = setup(_pdf(1), None, anthropic_api_key=CONTAINER)
    factory = Factory()
    resolver = WorkspaceModelClients(deps.settings, factory)
    first = read(ws, resolver.client, deps.settings)
    assert read(ws, resolver.client, deps.settings) is first and factory.keys == [CONTAINER]
    _type(app_engine, ws, deps.settings)
    typed = read(ws, resolver.client, deps.settings)
    assert typed is not first and factory.keys == [CONTAINER, TYPED]
    off = dataclasses.replace(deps.settings, ai_enabled=False)
    assert read(ws, resolver.client, off) is None
    rotated = dataclasses.replace(deps.settings, session_key=OTHER_KEY)
    assert read(ws, resolver.client, rotated) is None  # never the container's key
    assert factory.keys == [CONTAINER, TYPED]
    assert TYPED not in repr(resolver) and TYPED not in repr(typed)


def test_a_read_with_no_key_ends_at_gate_1_and_reads_with_the_typed_key(
    setup,  # noqa: F811
    recorder,  # noqa: F811
    app_engine,
):
    deps, ws, doc, proposal = setup(_pdf(1), None, anthropic_api_key=None)
    factory = Factory(_letter_responses)
    deps = _resolving(deps, factory)
    _run(deps, ws, doc, proposal)
    [(_, _, failure)] = recorder.failures
    assert failure["error_code"] == "ai.disabled" and factory.keys == []
    _type(app_engine, ws, deps.settings)
    _run(deps, ws, doc, proposal)
    assert factory.keys == [TYPED] and len(factory.clients[0].requests) == 2
    assert len(recorder.results) == 1 and len(recorder.failures) == 1


def test_a_handed_in_client_is_kept(setup, recorder, app_engine):  # noqa: F811
    client = FakeModelClient(_letter_responses())
    deps, ws, doc, proposal = setup(_pdf(1), client, anthropic_api_key=None)
    _type(app_engine, ws, deps.settings)
    _run(deps, ws, doc, proposal)
    assert len(client.requests) == 2 and len(recorder.results) == 1


def test_a_line_is_categorized_with_the_typed_key(world, app_engine, tmp_path):  # noqa: F811
    factory = Factory(lambda: [_answer(("insurance", "-312.40", None, True))])
    deps = _resolving(matching_deps(app_engine, tmp_path, None, anthropic_api_key=None), factory)
    tx = world.line(date(2026, 3, 3), "-312.40", name="Allianz", purpose="Beitrag")
    plan = run_match(deps, world, tx)
    assert plan.reason == "routing.no_match" and _rows(world, tx) == [] and factory.keys == []
    _type(app_engine, world.ws, deps.settings)
    run_match(deps, world, tx)
    (row,) = _rows(world, tx)
    assert (row.category, row.method) == ("insurance", "llm") and factory.keys == [TYPED]
