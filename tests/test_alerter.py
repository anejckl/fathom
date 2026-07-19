import time, pytest
from unittest.mock import patch
import alerter
import database as db

pytestmark = pytest.mark.usefixtures("tmp_db")


# --- _matches -----------------------------------------------------------------

@pytest.mark.parametrize("line,pattern,expected", [
    ("connection error occurred",  r"error",        True),
    ("server started ok",          r"error",        False),
    ("DISK FULL WARNING",          r"disk",         True),
    ("",                           r"error",        False),
    ("error [module] failed",      r"[module",      True),
    ("all good here",              r"[module",      False),
    ("connection timeout",         r"conn\w+",      True),
    ("auth FAILURE",               r"(?i)failure",  True),
])
def test_matches(line, pattern, expected):
    assert alerter._matches(line, pattern) == expected


# --- _check_rules -------------------------------------------------------------

def _add_rule(threshold=2, window_minutes=5, pattern="error"):
    db.add_alert_rule("web-1", pattern, threshold, window_minutes,
                      "http://ntfy.example/x", "ntfy")
    return db.get_alert_rules()[-1]["id"]


def test_fires_when_threshold_met(monkeypatch):
    now = int(time.time())
    _add_rule(threshold=2)
    db.insert_log(now - 10, "web-1", None, None, "error", "connection error", None, "stdout")
    db.insert_log(now - 5,  "web-1", None, None, "error", "disk error",       None, "stdout")

    fired = []
    monkeypatch.setattr(alerter, "_fire", lambda rule, lines: fired.append(rule["container"]))
    alerter._check_rules(db.conn)
    assert fired == ["web-1"]


def test_no_fire_below_threshold(monkeypatch):
    now = int(time.time())
    _add_rule(threshold=5)
    db.insert_log(now - 10, "web-1", None, None, "error", "connection error", None, "stdout")

    fired = []
    monkeypatch.setattr(alerter, "_fire", lambda rule, lines: fired.append(rule))
    alerter._check_rules(db.conn)
    assert fired == []


def test_respects_cooldown(monkeypatch):
    now = int(time.time())
    rid = _add_rule(threshold=1, window_minutes=5)
    db.update_alert_fired(rid)
    db.insert_log(now, "web-1", None, None, "error", "error now", None, "stdout")

    fired = []
    monkeypatch.setattr(alerter, "_fire", lambda rule, lines: fired.append(rule))
    alerter._check_rules(db.conn)
    assert fired == []


def test_fires_after_cooldown_elapsed(monkeypatch):
    now = int(time.time())
    rid = _add_rule(threshold=1, window_minutes=5)
    with db.conn() as c:
        c.execute("UPDATE alert_rules SET last_fired=? WHERE id=?", (now - 600, rid))
    db.insert_log(now - 10, "web-1", None, None, "error", "fresh error", None, "stdout")

    fired = []
    monkeypatch.setattr(alerter, "_fire", lambda rule, lines: fired.append(rule["container"]))
    alerter._check_rules(db.conn)
    assert fired == ["web-1"]


def test_no_rules_no_error():
    alerter._check_rules(db.conn)


def test_no_matching_lines(monkeypatch):
    now = int(time.time())
    _add_rule(threshold=1)
    db.insert_log(now - 10, "web-1", None, None, "info", "all good here", None, "stdout")

    fired = []
    monkeypatch.setattr(alerter, "_fire", lambda rule, lines: fired.append(rule))
    alerter._check_rules(db.conn)
    assert fired == []


def test_update_fired_called_after_fire(monkeypatch):
    now = int(time.time())
    rid = _add_rule(threshold=1)
    db.insert_log(now - 10, "web-1", None, None, "error", "bad error", None, "stdout")

    monkeypatch.setattr(alerter, "_fire", lambda rule, lines: None)
    alerter._check_rules(db.conn)

    rule = next(r for r in db.get_alert_rules() if r["id"] == rid)
    assert rule["last_fired"] is not None


def test_samples_capped_at_three(monkeypatch):
    now = int(time.time())
    _add_rule(threshold=1)
    for i in range(10):
        db.insert_log(now - i, "web-1", None, None, "error", f"error {i}", None, "stdout")

    captured = []
    monkeypatch.setattr(alerter, "_fire", lambda rule, lines: captured.append(lines))
    alerter._check_rules(db.conn)
    assert captured and len(captured[0]) <= 3


def test_multiple_rules_independent(monkeypatch):
    now = int(time.time())
    db.add_alert_rule("web-1",   "error", 1, 5, "http://ntfy/x", "ntfy")
    db.add_alert_rule("redis-1", "error", 1, 5, "http://ntfy/y", "ntfy")
    db.insert_log(now - 5, "web-1",   None, None, "error", "web error",   None, "stdout")
    db.insert_log(now - 5, "redis-1", None, None, "error", "redis error", None, "stdout")

    fired = []
    monkeypatch.setattr(alerter, "_fire", lambda rule, lines: fired.append(rule["container"]))
    alerter._check_rules(db.conn)
    assert "web-1" in fired
    assert "redis-1" in fired


# --- _fire --------------------------------------------------------------------

def _rule(wtype):
    return {
        "id": 1,
        "container":      "web-1",
        "pattern":        "error",
        "threshold":      3,
        "window_minutes": 5,
        "webhook_url":    f"http://example.com/{wtype}",
        "webhook_type":   wtype,
    }


def test_fire_ntfy_posts_to_url():
    rule = _rule("ntfy")
    with patch("alerter.requests.post") as mock_post:
        alerter._fire(rule, ["line1", "line2"])
        assert mock_post.called
        assert mock_post.call_args[0][0] == rule["webhook_url"]


def test_fire_ntfy_includes_title_header():
    rule = _rule("ntfy")
    with patch("alerter.requests.post") as mock_post:
        alerter._fire(rule, ["line1"])
        headers = mock_post.call_args[1].get("headers", {})
        assert "Title" in headers


def test_fire_discord_sends_content():
    rule = _rule("discord")
    with patch("alerter.requests.post") as mock_post:
        alerter._fire(rule, ["line1"])
        payload = mock_post.call_args[1].get("json", {})
        assert "content" in payload


def test_fire_slack_sends_text():
    rule = _rule("slack")
    with patch("alerter.requests.post") as mock_post:
        alerter._fire(rule, ["line1"])
        payload = mock_post.call_args[1].get("json", {})
        assert "text" in payload


def test_fire_network_error_no_raise():
    with patch("alerter.requests.post", side_effect=Exception("network down")):
        alerter._fire(_rule("ntfy"), ["line1"])


def test_fire_empty_sample_lines():
    with patch("alerter.requests.post") as mock_post:
        alerter._fire(_rule("ntfy"), [])
        assert mock_post.called
