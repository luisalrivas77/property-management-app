"""A scripted stand-in for python-fints' FinTS3PinTanClient, for the facade and the job tests.

It has exactly the methods FintsSession calls, with the real client's parameter names, kinds
and defaults (tests/test_fints_facade.py checks this), and plays one `Script`: whether the
bank asks for a pushTAN at login and how many polls answer "not yet" (3956), the accounts,
the camt formats HICAZS lists, the booked streams, the HKSAL balance, and a failure at a
named step (a python-fints exception, or 9xxx answers the client keeps in `bank_answers`,
as the real client's guarded connection does, before raising a generic Exception whose text
quotes the login, as python-fints does).

Every bank code, login, IBAN and URL here is synthetic."""

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from fints.client import NeedTANResponse
from fints.models import SEPAAccount

from hausbuch.rules.bank_sync import MESSAGE_LEVEL, SEGMENT_LEVEL, BankAnswer
from hausbuch.schemas.bank_sync import BankParameters

FAKE_IBAN = "DE50100000000000000001"
FAKE_STATE = b"fake-client-state"
DECOUPLED = "946"  # a decoupled mechanism (pushTAN), as banks number it
CHIPTAN = "912"


def fake_account(iban: str = FAKE_IBAN) -> SEPAAccount:
    return SEPAAccount(iban, "TESTDEFFXXX", "0000000001", None, "10000000")


@dataclass
class Script:
    """What the fake bank does in one run."""

    mechanisms: dict[str, bool] = field(default_factory=lambda: {DECOUPLED: True})
    # None: no approval at login; n: n answers "not yet" before the approval arrives.
    approval_polls: int | None = None
    # When the state was restored, does the bank still ask at login? (Q-O35)
    approval_after_restore: bool = False
    # None: never confirms (the job's timeout ends it).
    never_confirms: bool = False
    challenge_decoupled: bool = True
    accounts: list[SEPAAccount] = field(default_factory=lambda: [fake_account()])
    camt_formats: list[str] = field(
        default_factory=lambda: ["urn:iso:std:iso:20022:tech:xsd:camt.052.001.08"]
    )
    streams: list[bytes] = field(default_factory=list)
    balance: Decimal | None = None
    # step name -> exception raised there; step names: bootstrap (fetch_tan_mechanisms),
    # open, poll, accounts, transactions, balance, state.
    fail_at: dict[str, BaseException] = field(default_factory=dict)
    # step name -> the answer codes the bank sends there, comma-separated: "9942" one
    # segment-level answer (HIRMS), "m9050,9942" a message-level one (HIRMG) and then the
    # segment's own, as banks send them.
    refuse_at: dict[str, str] = field(default_factory=dict)
    # 9xxx codes the bank sends during the bootstrap that python-fints tolerates there
    # (recorded, nothing raised).
    bootstrap_codes: list[str] = field(default_factory=list)
    state: bytes = FAKE_STATE
    # Kontoauszug PDF (D154): whether HIEKPS offers fetching by number; the statements the
    # bank delivers, (year, number) -> the HIEKP data of each answer (more than one: a 3040
    # continuation per part but the last); the answer codes for a number it does not
    # deliver (the same comma-separated form as refuse_at, on the HKEKP segment; "m9800,9210"
    # ends the dialog), per key or `statement_refusal` for every other key; an exception per
    # key; whether reopening after a 9800 asks for a pushTAN.
    statements_offered: bool = False
    statements: dict[tuple[int, int], list[bytes]] = field(default_factory=dict)
    statement_codes: dict[tuple[int, int], str] = field(default_factory=dict)
    statement_refusal: str = "3010"
    statement_fail_at: dict[tuple[int, int], BaseException] = field(default_factory=dict)
    reopen_asks_approval: bool = False
    reopen_codes: str | None = None
    # Phase 6 D173 (7): the bank's name in its parameters (HIBPA), None for none read; a
    # failure reading them (the run goes on, nothing is recorded).
    bank_name: str | None = "Testbank"
    parameters_fail: bool = False


class FakeClient:
    """Plays a Script; records the calls it got in `calls` and its constructor in `init`."""

    def __init__(self, script: Script, init: dict[str, Any]):
        self.script = script
        self.init = init
        self.calls: list[str] = []
        # what the real client's guarded connection keeps from every answer (D151)
        self.bank_answers: list[BankAnswer] = []
        self.allowed_security_functions: list[str] = []
        self.selected_security_function: str | None = None
        self.init_tan_response: Any = None
        self._polls_left = script.approval_polls or 0
        self._restored = init.get("client_data") is not None
        self.login_name = init.get("login_name", "")
        self.statement_requests: list[tuple[int, int, str | None]] = []
        self.dialog_ended = False
        self.parameters_read = 0

    # --- the scripted failures ------------------------------------------------------------

    def _answer(self, codes: str, segment: str = "HKCAZ:5") -> None:
        for code in codes.split(","):
            if code.startswith("m"):
                self.bank_answers.append(BankAnswer(MESSAGE_LEVEL, code[1:]))
            else:
                self.bank_answers.append(BankAnswer(SEGMENT_LEVEL, code, segment))

    def _step(self, name: str) -> None:
        self.calls.append(name)
        if name in self.script.refuse_at:
            self._answer(self.script.refuse_at[name])
            # python-fints' own text quotes the whole response, here with the login in it.
            raise Exception(f"Error response: {self.login_name!r}")  # noqa: TRY002
        if name in self.script.fail_at:
            raise self.script.fail_at[name]

    def _challenge(self, resume: str) -> NeedTANResponse:
        return NeedTANResponse(
            None,
            SimpleNamespace(challenge="Bitte in der App freigeben"),
            resume,
            decoupled=self.script.challenge_decoupled,
        )

    # --- the client's surface ---------------------------------------------------------------

    def fetch_tan_mechanisms(self):
        self.calls.append("fetch_tan_mechanisms")
        for code in self.script.bootstrap_codes:
            self._answer(code)
        if "bootstrap" in self.script.refuse_at:
            self._answer(self.script.refuse_at["bootstrap"])
            raise Exception(f"Error response: {self.login_name!r}")  # noqa: TRY002
        if "bootstrap" in self.script.fail_at:
            raise self.script.fail_at["bootstrap"]
        self.allowed_security_functions = list(self.script.mechanisms)
        return self.selected_security_function

    def get_tan_mechanisms(self):
        return {
            sf: SimpleNamespace(decoupled_max_poll_number=60 if decoupled else None)
            for sf, decoupled in self.script.mechanisms.items()
            if sf in self.allowed_security_functions
        }

    def set_tan_mechanism(self, security_function):
        self.selected_security_function = security_function

    def __enter__(self):
        self._step("open")
        asks = self.script.approval_polls is not None and (
            not self._restored or self.script.approval_after_restore
        )
        if asks:
            self.init_tan_response = self._challenge("_continue_dialog_initialization")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.calls.append("close")

    def send_tan(self, challenge: NeedTANResponse, tan: str):
        self._step("poll")
        if self.script.never_confirms or self._polls_left > 0:
            self._polls_left -= 1
            return self._challenge(challenge.resume_method)
        return None

    def get_sepa_accounts(self):
        self._step("accounts")
        return list(self.script.accounts)

    def supported_camt_formats(self) -> list[str]:
        return list(self.script.camt_formats)

    def get_transactions_xml(
        self,
        account: SEPAAccount,
        start_date=None,
        end_date=None,
        supported_camt_messages=None,
    ) -> list:
        self._step("transactions")
        self.window = (start_date, end_date, supported_camt_messages)
        return [list(self.script.streams), [b"<pending/>"]]

    def get_balance(self, account: SEPAAccount):
        self._step("balance")
        if self.script.balance is None:
            return None
        return SimpleNamespace(amount=SimpleNamespace(amount=self.script.balance))

    def statement_pdf_offered(self) -> bool:
        return self.script.statements_offered

    def bank_parameters(self) -> BankParameters | None:
        self.parameters_read += 1  # not in `calls`: the call order tests stay as they are
        if self.script.parameters_fail:
            raise RuntimeError("unreadable parameters")
        if self.script.bank_name is None:
            return None
        formats = list(self.script.camt_formats)
        return BankParameters(
            bank_name=self.script.bank_name,
            bpd_version=7,
            camt_formats=formats,
            camt_readable=any("camt.052" in f or "camt.053" in f for f in formats),
            statement_pdf_by_number=self.script.statements_offered,
            decoupled_methods=[sf for sf, decoupled in self.script.mechanisms.items() if decoupled],
            approval_versions=[7],
        )

    def get_statement_pdf(
        self, account: SEPAAccount, number: int, year: int, touchdown: str | None = None
    ) -> tuple[list[bytes], str | None]:
        self.calls.append("statement")
        self.statement_requests.append((year, number, touchdown))
        key = (year, number)
        if self.dialog_ended:  # the bank answers nothing but 9800 in an ended dialog
            self._answer("m9800")
            return [], None
        if key in self.script.statement_fail_at:
            raise self.script.statement_fail_at[key]
        parts = self.script.statements.get(key)
        if parts is None:
            codes = self.script.statement_codes.get(key, self.script.statement_refusal)
            self._answer(codes, "HKEKP:3")
            self.dialog_ended = "m9800" in codes.split(",")
            return [], None
        index = 0 if touchdown is None else int(touchdown.removeprefix("part-"))
        if index + 1 < len(parts):
            self._answer("m3060,3040", "HKEKP:3")
            return [parts[index]], f"part-{index + 1}"
        self._answer("m0010,0020", "HKEKP:3")
        return [parts[index]], None

    def restart_dialog(self):
        self.calls.append("reopen")
        self.dialog_ended = False
        self.init_tan_response = None
        if self.script.reopen_codes:
            self._answer(self.script.reopen_codes, "HKIDN:3")
        if self.script.reopen_asks_approval:
            self.init_tan_response = self._challenge("_continue_dialog_initialization")
        return self.init_tan_response

    def deconstruct(self, including_private: bool = False) -> bytes:
        self._step("state")
        self.deconstructed_private = including_private
        return self.script.state


def factory(script: Script, made: list[FakeClient] | None = None) -> Callable[..., FakeClient]:
    """A client_factory for open_session that builds a FakeClient playing `script`; every
    client built is appended to `made`."""

    def build(**kwargs: Any) -> FakeClient:
        client = FakeClient(script, kwargs)
        if made is not None:
            made.append(client)
        return client

    return build
