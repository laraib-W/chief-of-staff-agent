"""Fuzz tests for the sanitize pipeline (specs.md §3.1.1 acceptance criterion:
"Fuzz test with adversarial inputs: zero-width chars, bidi overrides, oversized
bodies"). Property-based via Hypothesis — random adversarial generation, not
just the hand-picked examples already covered in test_sanitize_message.py.
"""

import base64
import unicodedata

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.providers import _sanitize

pytestmark = pytest.mark.fuzz

ZERO_WIDTH = "​‌‍﻿"
BIDI_OVERRIDES = "‪‫‬‭‮⁦⁧⁨⁩"
ADVERSARIAL_CHARS = ZERO_WIDTH + BIDI_OVERRIDES

# Base text excludes surrogates (invalid) and format chars (Cf) — the
# adversarial codepoints are injected deliberately and separately below,
# so this pool only supplies "ordinary" filler content.
_base_text = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs", "Cf"),
        min_codepoint=0x20,
        max_codepoint=0x2FFFF,
    ),
    max_size=200,
)
_adversarial_text = st.text(alphabet=ADVERSARIAL_CHARS, min_size=0, max_size=20)


@given(text=_base_text, injected=_adversarial_text)
@settings(max_examples=200, deadline=None)
def test_normalize_unicode_always_strips_zero_width_and_bidi(text, injected):
    mixed = injected.join(text) if text else injected
    result = _sanitize.normalize_unicode(mixed)
    assert not any(ch in ADVERSARIAL_CHARS for ch in result)


@given(text=_base_text)
@settings(max_examples=200, deadline=None)
def test_normalize_unicode_output_is_nfc(text):
    result = _sanitize.normalize_unicode(text)
    assert result == unicodedata.normalize("NFC", result)


@given(text=st.text(max_size=5000), max_chars=st.integers(min_value=0, max_value=3000))
@settings(max_examples=200, deadline=None)
def test_truncate_never_exceeds_max_chars(text, max_chars):
    result = _sanitize.truncate(text, max_chars)
    assert len(result) <= max_chars


@given(
    body=_base_text,
    injected=_adversarial_text,
    max_chars=st.integers(min_value=1, max_value=500),
)
@settings(max_examples=100, deadline=None)
def test_sanitize_message_survives_adversarial_oversized_body(
    body, injected, max_chars
):
    # (body + injected) * 20 guarantees an oversized body regardless of what
    # Hypothesis draws for `body`/`injected` on a given example.
    raw_body = (body + injected) * 20
    raw = {
        "id": "fuzz-1",
        "threadId": "fuzz-t1",
        "internalDate": "1752652800000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [],
            "body": {
                "data": base64.urlsafe_b64encode(
                    raw_body.encode("utf-8", "ignore")
                ).decode()
            },
        },
    }
    email = _sanitize.sanitize_message(raw, max_body_chars=max_chars)
    assert len(email.clean_body) <= max_chars
    assert not any(ch in ADVERSARIAL_CHARS for ch in email.clean_body)
