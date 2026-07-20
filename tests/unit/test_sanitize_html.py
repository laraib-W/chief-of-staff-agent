from app.providers._internal import sanitize as _sanitize


def test_strip_html_removes_tags():
    assert _sanitize.strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_drops_script_and_style():
    html = "<style>.x{}</style><p>keep</p><script>evil()</script>"
    assert _sanitize.strip_html(html) == "keep"


def test_strip_html_plain_text_passthrough():
    assert _sanitize.strip_html("just text") == "just text"


def test_strip_tracking_removes_tracking_url():
    text = "See http://click.mailchimp.com/abc now"
    assert "click.mailchimp.com" not in _sanitize.strip_tracking(text)


def test_strip_tracking_strips_utm_params():
    text = "Visit https://example.com/page?utm_source=x&id=5&utm_medium=y"
    out = _sanitize.strip_tracking(text)
    assert "utm_source" not in out
    assert "id=5" in out


def test_strip_tracking_trailing_comma_survives_no_double_space():
    text = "Please review http://click.mailchimp.com/abc now, thanks!"
    out = _sanitize.strip_tracking(text)
    assert "click.mailchimp.com" not in out
    assert "now, thanks!" in out
    assert "  " not in out


def test_strip_tracking_trailing_period_not_glued_to_url():
    text = "Go to https://example.com/page. Done"
    out = _sanitize.strip_tracking(text)
    assert out == "Go to https://example.com/page. Done"


def test_strip_tracking_removed_url_with_glued_punctuation():
    # Tracking URL immediately followed by a comma (no space) must be removed
    # while the comma survives, and no doubled space is introduced.
    result = _sanitize.strip_tracking(
        "Please review http://click.mailchimp.com/abc123, thanks!"
    )
    assert "click.mailchimp.com" not in result
    assert ", thanks!" in result
    assert "  " not in result
