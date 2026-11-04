"""Trello's key and token travel as URL parameters, and requests puts the URL in its errors: the service log and
the System card's last error must never show them (seen in service.log, 2026-11-03)."""

import logging

from autocam_service.app import _HidingFormatter, hide_secrets

URL = "/1/cards/abc?name=System&key=KEYVALUE123&token=TOKENVALUE456 (Caused by NameResolutionError(...))"


def test_key_and_token_values_are_hidden():
    out = hide_secrets(f"ConnectionError: Max retries exceeded with url: {URL}")
    assert "KEYVALUE123" not in out and "TOKENVALUE456" not in out
    assert "key=<hidden>&token=<hidden> (Caused by" in out and "name=System" in out


def test_the_log_formatter_hides_them_in_tracebacks_too():
    try:
        raise ConnectionError(URL)
    except ConnectionError:
        record = logging.LogRecord("autocam", logging.ERROR, __file__, 1, "tick failed", None,
                                   exc_info=__import__("sys").exc_info())
    text = _HidingFormatter("%(message)s").format(record)
    assert "tick failed" in text and "Traceback" in text and "TOKENVALUE456" not in text
