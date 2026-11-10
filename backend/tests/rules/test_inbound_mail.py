"""rules.inbound_mail (docs/phases/phase-5.md D121, §5.3, §12.1): the decision against its
fixture, and the parsers of what the IMAP server returns (no fixture covers those, so the
cases here are hand-built from the RFC 3501 and RFC 8601 grammars, with Gmail's shape).

Every address is synthetic (example.org, example.com, example.net)."""

import base64
from datetime import UTC, date, datetime

import pytest
from fixture_models_phase5 import load

from hausbuch.rules import errors as E
from hausbuch.rules.inbound_mail import (
    Literal,
    body_parts,
    decide,
    decode_part,
    imap_mailbox,
    imap_since,
    mail_facts,
    message_id_hash,
    parse_authentication_results,
    parse_fetch_response,
    parse_internaldate,
    section,
)
from hausbuch.schemas.inbound_mail import AuthResult, MailPart

CASES = load("inbound_mail").cases


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_decide_matches_the_fixture(case):
    assert decide(case.facts, case.policy) == case.expect


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_decide_does_not_depend_on_the_order_of_the_parts(case):
    facts = case.facts.model_copy(update={"parts": list(reversed(case.facts.parts))})
    assert decide(facts, case.policy) == case.expect


# --- Authentication-Results --------------------------------------------------------------

GMAIL_AR = (
    "mx.google.com;\r\n"
    "       dkim=pass header.i=@example.org header.s=20230601 header.b=AbCdEf12;\r\n"
    "       spf=pass (google.com: domain of eigentuemer.a@example.org designates 192.0.2.1 "
    "as permitted sender; checked (nested) twice) smtp.mailfrom=eigentuemer.a@example.org;\r\n"
    "       dmarc=pass (p=NONE sp=NONE dis=NONE) header.from=example.org;\r\n"
    "       arc=none"
)


def test_a_gmail_header_gives_dkim_spf_and_dmarc():
    assert parse_authentication_results(GMAIL_AR) == [
        AuthResult(authserv_id="mx.google.com", method="dkim", result="pass", domain="example.org"),
        AuthResult(authserv_id="mx.google.com", method="spf", result="pass", domain="example.org"),
        AuthResult(
            authserv_id="mx.google.com", method="dmarc", result="pass", domain="example.org"
        ),
    ]


def test_header_d_wins_over_header_i_and_results_are_lower_case():
    value = "MX.Google.com 1; dkim=PASS header.i=@other.example header.d=Example.ORG"
    (dkim,) = parse_authentication_results(value)
    assert (dkim.authserv_id, dkim.result, dkim.domain) == ("mx.google.com", "pass", "Example.ORG")


def test_spf_without_an_at_sign_takes_the_whole_value_and_none_gives_no_row():
    assert parse_authentication_results("mx.google.com; none") == []
    (spf,) = parse_authentication_results('mx.google.com; spf=softfail smtp.mailfrom="example.net"')
    assert (spf.result, spf.domain) == ("softfail", "example.net")
    assert parse_authentication_results("") == []


# --- FETCH answers and BODYSTRUCTURE -----------------------------------------------------

ALT = (
    b'("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 12 1 NIL NIL NIL)'
    b'("TEXT" "HTML" ("CHARSET" "UTF-8") NIL NIL "QUOTED-PRINTABLE" 34 1 NIL NIL NIL)'
    b' "ALTERNATIVE" ("BOUNDARY" "000") NIL NIL'
)
PDF = (
    b'("APPLICATION" "PDF" ("NAME" "rechnung.pdf") "<f_1>" NIL "BASE64" 160000 NIL'
    b' ("ATTACHMENT" ("FILENAME" "rechnung.pdf")) NIL NIL)'
)


def _structure(raw: bytes):
    (message,) = parse_fetch_response([b"1 (UID 41 BODYSTRUCTURE " + raw + b")"])
    assert message["UID"] == "41"
    return body_parts(message["BODYSTRUCTURE"])


def _summary(parts):
    return [
        (p.part.index, p.part.filename, p.part.media_type, p.part.size_bytes, p.encoding)
        for p in parts
    ]


def test_mixed_with_alternative_body_and_a_pdf():
    parts = _structure(b"((" + ALT + b")" + PDF + b' "MIXED" ("BOUNDARY" "001") NIL NIL)')
    assert _summary(parts) == [
        ("1.1", None, "text/plain", 12, "7bit"),
        ("1.2", None, "text/html", 34, "quoted-printable"),
        ("2", "rechnung.pdf", "application/pdf", 160000, "base64"),
    ]


def test_a_single_part_message_is_part_one():
    parts = _structure(PDF)
    assert _summary(parts) == [("1", "rechnung.pdf", "application/pdf", 160000, "base64")]


ENVELOPE = b'(NIL "Rechnung" NIL NIL NIL NIL NIL NIL NIL "<orig@example.net>")'


def test_a_forwarded_message_lists_itself_and_its_parts_one_level_down():
    inner = (
        b'(("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 300 5 NIL NIL NIL)'
        + PDF
        + b' "MIXED" ("BOUNDARY" "in") NIL NIL)'
    )
    fwd = (
        b'("MESSAGE" "RFC822" ("NAME" "weitergeleitet.eml") NIL NIL "7BIT" 400000 '
        + ENVELOPE
        + b" "
        + inner
        + b' 80 NIL ("ATTACHMENT" ("FILENAME" "weitergeleitet.eml")) NIL NIL)'
    )
    raw = b'(("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 10 1 NIL NIL NIL)' + fwd
    parts = _structure(raw + b' "MIXED" ("BOUNDARY" "x") NIL NIL)')
    assert [(p.part.index, p.part.filename, p.part.nested_rfc822) for p in parts] == [
        ("1", None, False),
        ("2", "weitergeleitet.eml", True),
        ("2.1", None, False),
        ("2.2", "rechnung.pdf", False),
    ]


def test_a_forwarded_single_part_message_body_is_n_dot_1():
    fwd = (
        b'("MESSAGE" "RFC822" NIL NIL NIL "7BIT" 9000 ' + ENVELOPE + b" " + PDF + b" 120 NIL NIL"
        b" NIL NIL)"
    )
    parts = _structure(b'(("TEXT" "PLAIN" NIL NIL NIL "7BIT" 1 1)' + fwd + b' "MIXED")')
    assert [(p.part.index, p.part.filename) for p in parts] == [
        ("1", None),
        ("2", None),
        ("2.1", "rechnung.pdf"),
    ]


def test_a_related_resource_is_part_of_the_body_not_an_attachment():
    logo = (
        b'("IMAGE" "PNG" ("NAME" "logo.png") "<logo@x>" NIL "BASE64" 4000 NIL'
        b' ("INLINE" ("FILENAME" "logo.png")) NIL NIL)'
    )
    related = b'(("TEXT" "HTML" NIL NIL NIL "7BIT" 34 1)' + logo + b' "RELATED")'
    parts = _structure(b"(" + related + PDF + b' "MIXED")')
    assert [(p.part.index, p.part.filename) for p in parts] == [
        ("1.1", None),
        ("1.2", None),
        ("2", "rechnung.pdf"),
    ]


def test_encoded_filenames_and_a_literal_are_decoded():
    word = "=?UTF-8?B?" + base64.b64encode("Rächnung.pdf".encode()).decode() + "?="
    raw = (
        b'(("APPLICATION" "PDF" ("NAME" "' + word.encode() + b'") NIL NIL "BASE64" 10 NIL NIL'
        b' NIL NIL)("IMAGE" "JPEG" NIL NIL NIL "BASE64" 20 NIL ("ATTACHMENT"'
        b' ("FILENAME*" "utf-8\'\'Foto%20K%C3%BCche.jpg")) NIL NIL)'
    )
    literal_name = "Übergabe.pdf".encode()
    data = [
        (
            b"1 (UID 9 BODYSTRUCTURE "
            + raw
            + b'("APPLICATION" "PDF" ("NAME" {'
            + str(len(literal_name)).encode()
            + b"}",
            literal_name,
        ),
        b') NIL NIL "BASE64" 30 NIL NIL NIL NIL) "MIXED"))',
    ]
    (message,) = parse_fetch_response(data)
    names = [p.part.filename for p in body_parts(message["BODYSTRUCTURE"])]
    assert names == ["Rächnung.pdf", "Foto Küche.jpg", "Übergabe.pdf"]


def test_two_messages_with_header_literals():
    header_a = b"Message-ID: <a@example.org>\r\nFrom: a@example.org\r\n\r\n"
    header_b = b"Message-ID: <b@example.org>\r\n\r\n"
    name = b"BODY[HEADER.FIELDS (MESSAGE-ID FROM TO CC DELIVERED-TO AUTHENTICATION-RESULTS)]"
    data = [
        (
            b'1 (UID 5 INTERNALDATE "10-Nov-2026 12:34:56 +0100" BODYSTRUCTURE '
            + PDF
            + b" "
            + name
            + b" {%d}" % len(header_a),
            header_a,
        ),
        b")",
        (b"2 (UID 6 BODYSTRUCTURE " + PDF + b" " + name + b" {%d}" % len(header_b), header_b),
        b")",
    ]
    first, second = parse_fetch_response(data)
    assert first["UID"] == "5" and second["UID"] == "6"
    assert section(first, "BODY[HEADER.FIELDS") == header_a
    assert section(second, "BODY[HEADER.FIELDS") == header_b
    assert section(first, "BODY[2]") is None
    assert parse_internaldate(first["INTERNALDATE"]) == datetime(
        2026, 11, 10, 11, 34, 56, tzinfo=UTC
    )


def test_a_part_section_comes_back_as_bytes():
    (message,) = parse_fetch_response([(b"1 (UID 5 BODY[2] {4}", b"QUJD"), b")"])
    assert section(message, "BODY[2]") == b"QUJD"
    assert isinstance(message["BODY[2]"], Literal)


@pytest.mark.parametrize(
    "data",
    [[b"1 (UID 5"], [b"1 (UID)"], [(b"1 (UID 5 BODY[2] {40}", b"short"), b")"]],
    ids=["unclosed", "odd-pairs", "short-literal"],
)
def test_a_malformed_answer_raises_value_error(data):
    with pytest.raises(ValueError):
        parse_fetch_response(data)


# --- Header facts --------------------------------------------------------------------------

PDF_PART = MailPart(index="2", filename="r.pdf", media_type="application/pdf", size_bytes=10)


def _header(*lines: str) -> bytes:
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def test_mail_facts_reads_the_addresses_and_only_the_topmost_authentication_results():
    header = _header(
        "Delivered-To: haushalt+x7k2q9@example.com",
        "Authentication-Results: mx.google.com; dkim=fail header.d=example.org",
        "Message-ID: <CAF+abc@mail.example.org>",
        'From: "Eigentuemer A" <eigentuemer.a@example.org>',
        "To: Haushalt <haushalt+x7k2q9@example.com>, other@example.net",
        "Cc: third@example.net",
        # A header the sender wrote into the original: below Gmail's, so never read.
        "Authentication-Results: mx.google.com; dkim=pass header.d=example.org",
    )
    got = mail_facts(header, [PDF_PART])
    assert got.message_id == "<CAF+abc@mail.example.org>"
    assert got.facts.message_id_present is True
    assert got.facts.from_address == "eigentuemer.a@example.org"
    assert got.facts.recipients == [
        "haushalt+x7k2q9@example.com",
        "other@example.net",
        "third@example.net",
        "haushalt+x7k2q9@example.com",
    ]
    assert [(a.method, a.result) for a in got.facts.auth_results] == [("dkim", "fail")]
    assert got.facts.parts == [PDF_PART]


def test_a_mail_from_two_addresses_has_no_sender_and_no_message_id_is_reported():
    got = mail_facts(_header("From: a@example.org, b@example.org"), [])
    assert got.facts.from_address == ""
    assert got.message_id is None and got.facts.message_id_present is False
    assert got.facts.auth_results == []


def test_the_message_id_hash_is_the_sha256_of_the_stripped_id():
    first = message_id_hash(" <a@example.org> ")
    assert first == message_id_hash("<a@example.org>") and len(first) == 32
    assert first != message_id_hash("<b@example.org>")


# --- Small rest ------------------------------------------------------------------------------


def test_decode_part():
    assert decode_part(b"QUJD\r\nREVG", "BASE64") == b"ABCDEF"
    assert decode_part(b"caf=C3=A9", "quoted-printable") == "café".encode()
    assert decode_part(b"plain", "7bit") == b"plain"
    for data, encoding in ((b"!!!", "base64"), (b"x", "x-uuencode")):
        with pytest.raises(ValueError):
            decode_part(data, encoding)


def test_imap_dates_use_english_month_names():
    assert imap_since(date(2026, 10, 1)) == "1-Oct-2026"
    assert imap_since(date(2026, 3, 11)) == "11-Mar-2026"
    assert parse_internaldate(" 1-Mar-2026 00:00:00 -0130") == datetime(
        2026, 3, 1, 1, 30, tzinfo=UTC
    )
    assert parse_internaldate("not a date") is None
    assert parse_internaldate(None) is None


def test_the_label_is_quoted_modified_utf7():
    assert imap_mailbox("Hausbuch") == '"Hausbuch"'
    assert imap_mailbox("Finanzen/Haus & Hof") == '"Finanzen/Haus &- Hof"'
    assert imap_mailbox("Hausbücher") == '"Hausb&APw-cher"'
    assert imap_mailbox('a"b') == '"a\\"b"'


def test_codes_are_the_spec_strings():
    assert E.EMAIL_ACCEPTED == "email.accepted"
