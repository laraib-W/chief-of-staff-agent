from app.providers._internal import sanitize as _sanitize


def test_strip_quotes_gt_lines():
    text = "My reply.\n> previous line\n> more quoted"
    assert _sanitize.strip_quotes(text) == "My reply."


def test_strip_quotes_on_wrote_block():
    text = (
        "Answer here.\nOn Mon, Jul 14, 2026 at 9:00 AM Bob <b@x.com> wrote:\nold stuff"
    )
    assert _sanitize.strip_quotes(text) == "Answer here."


def test_strip_quotes_outlook_separator():
    text = "Reply body.\n-----Original Message-----\nFrom: Bob"
    assert _sanitize.strip_quotes(text) == "Reply body."


def test_strip_quotes_no_marker_passthrough():
    assert _sanitize.strip_quotes("clean body") == "clean body"


def test_strip_signature_dashdash_delimiter():
    text = "Body text.\n-- \nJane Doe\nCEO"
    assert _sanitize.strip_signature(text) == "Body text."


def test_strip_signature_sent_from_my():
    text = "Quick note.\nSent from my iPhone"
    assert _sanitize.strip_signature(text) == "Quick note."


def test_strip_signature_regards_closing():
    text = "Please review.\nRegards,\nJane"
    assert _sanitize.strip_signature(text) == "Please review."
