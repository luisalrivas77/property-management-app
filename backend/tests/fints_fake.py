"""A scripted stand-in for python-fints' FinTS3PinTanClient, for the facade and the job tests.

It has exactly the methods FintsSession calls, with the real client's parameter names, kinds
and defaults (tests/test_fints_facade.py checks this), and plays one `Script`: whether the
bank asks for a pushTAN at login and how many polls answer "not yet" (3956), the accounts,
the camt formats HICAZS lists, the booked streams, the HKSAL balance, and a failure at a
named step (a python-fints exception, or a 9xxx answer the client records before raising a
generic Exception whose text quotes the login, as python-fints does).

Every bank code, login, IBAN and URL here is synthetic."""

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from fints.client import NeedTANResponse
from fints.models import SEPAAccount

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
    # step name -> 9xxx answer code the bank sends there.
    refuse_at: dict[str, str] = field(default_factory=dict)
    # 9xxx codes the bank sends during the bootstrap that python-fints tolerates there
    # (recorded, nothing raised).
    bootstrap_codes: list[str] = field(default_factory=list)
    state: bytes = FAKE_STATE


class FakeClient:
    """Plays a Script; records the calls it got in `calls` and its constructor in `init`."""

    def __init__(self, script: Script, init: dict[str, Any]):
        self.script = script
        self.init = init
        self.calls: list[str] = []
        self.refused_codes: list[str] = []
        self.allowed_security_functions: list[str] = []
        self.selected_security_function: str | None = None
        self.init_tan_response: Any = None
        self._polls_left = script.approval_polls or 0
        self._restored = init.get("client_data") is not None
        self.login_name = init.get("login_name", "")

    # --- the scripted failures ------------------------------------------------------------

    def _step(self, name: str) -> None:
        self.calls.append(name)
        if name in self.script.refuse_at:
            # "9050,9942": the message-level code, then the segment's own, as banks send them
            self.refused_codes.extend(self.script.refuse_at[name].split(","))
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
        self.refused_codes.extend(self.script.bootstrap_codes)
        if "bootstrap" in self.script.refuse_at:
            self.refused_codes.append(self.script.refuse_at["bootstrap"])
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
