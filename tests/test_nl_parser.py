import re, time, datetime, pytest

pytestmark = pytest.mark.usefixtures("frozen_now")


def _fn():
    from main import _nl_time_window
    return _nl_time_window


def _parse(q, **kw):
    from main import _quick_nl_parse
    return _quick_nl_parse(q, **kw)


def _now():
    return int(time.time())


def _midnight():
    dt = datetime.datetime.now()
    return int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def _match(n):
    return re.search(r'(\d+)', str(n))


# --- _nl_time_window ----------------------------------------------------------

def test_last_N_min():
    since, until = _fn()('last_N_min', _match(5))
    assert since == pytest.approx(_now() - 300, abs=2)
    assert until is None


def test_last_1_min():
    since, until = _fn()('last_1_min')
    assert since == pytest.approx(_now() - 60, abs=2)
    assert until is None


def test_last_N_hr():
    since, until = _fn()('last_N_hr', _match(3))
    assert since == pytest.approx(_now() - 10800, abs=2)
    assert until is None


def test_last_1_hr():
    since, until = _fn()('last_1_hr')
    assert since == pytest.approx(_now() - 3600, abs=2)
    assert until is None


def test_last_night():
    # frozen: 2025-06-15 14:30 -> since=Jun14 20:00, until=Jun15 06:00
    since, until = _fn()('last_night')
    midnight = _midnight()
    assert since == midnight - 4 * 3600
    assert until == midnight + 6 * 3600


def test_last_week():
    since, until = _fn()('last_week')
    assert since == pytest.approx(_now() - 7 * 86400, abs=2)
    assert until is None


def test_last_N_days():
    since, until = _fn()('last_N_days', _match(14))
    assert since == pytest.approx(_now() - 14 * 86400, abs=2)
    assert until is None


def test_tonight():
    midnight = _midnight()
    since, until = _fn()('tonight')
    assert since == midnight + 18 * 3600
    assert until == midnight + 86400


def test_today():
    since, until = _fn()('today')
    assert since == _midnight()
    assert until is None


def test_yesterday():
    midnight = _midnight()
    since, until = _fn()('yesterday')
    assert since == midnight - 86400
    assert until == midnight


def test_N_min_ago():
    since, until = _fn()('N_min_ago', _match(45))
    assert since == pytest.approx(_now() - 2700, abs=2)
    assert until is None


def test_N_hr_ago():
    since, until = _fn()('N_hr_ago', _match(2))
    assert since == pytest.approx(_now() - 7200, abs=2)
    assert until is None


def test_recent():
    since, until = _fn()('recent')
    assert since == pytest.approx(_now() - 1800, abs=2)
    assert until is None


def test_unknown_key_defaults_to_1hr():
    since, until = _fn()('???')
    assert since == pytest.approx(_now() - 3600, abs=2)
    assert until is None


# --- _quick_nl_parse: level aliases -------------------------------------------

@pytest.mark.parametrize("q,expected_level", [
    ("errors",           "error"),
    ("error logs",       "error"),
    ("critical failure", "error"),
    ("fatal crash",      "error"),
    ("crit issue",       "error"),
    ("warnings",         "warning"),
    ("warn: slow query", "warning"),
    ("info logs",        "info"),
])
def test_level_aliases(q, expected_level):
    assert _parse(q).get("level") == expected_level


def test_errors_today():
    result = _parse("errors today")
    assert result.get("level") == "error"
    assert result.get("since_ts") == _midnight()
    assert "until_ts" not in result


def test_yesterday_has_bounded_window():
    result = _parse("errors yesterday")
    midnight = _midnight()
    assert result.get("since_ts") == midnight - 86400
    assert result.get("until_ts") == midnight


def test_container_level_time_combined():
    result = _parse("web errors last hour", known_containers=["web-1", "worker-1"])
    assert result.get("container") == "web-1"
    assert result.get("level") == "error"
    assert result.get("since_ts") == pytest.approx(_now() - 3600, abs=2)


def test_partial_container_match():
    result = _parse("web logs", known_containers=["web-1"])
    assert result.get("container") == "web-1"


def test_unknown_container_not_matched():
    result = _parse("postgres logs", known_containers=["web-1", "worker-1"])
    assert result.get("container") is None


def test_keyword_extracted():
    result = _parse("connection timeout")
    assert result.get("search") is not None
    assert result.get("level") is None
    assert result.get("since_ts") is None


def test_stopwords_stripped():
    result = _parse("show me the errors")
    assert result.get("level") == "error"


def test_empty_query():
    assert _parse("") == {}


def test_uppercase_input():
    assert _parse("ERRORS").get("level") == "error"


def test_first_time_phrase_wins():
    # N_min_ago comes before 'today' in phrase list
    result = _parse("45 minutes ago today errors")
    assert result.get("since_ts") == pytest.approx(_now() - 2700, abs=2)
    assert "until_ts" not in result


def test_no_container_without_known_list():
    result = _parse("web-1 errors")
    assert result.get("container") is None


def test_ago_minutes():
    result = _parse("errors 10 minutes ago")
    assert result.get("since_ts") == pytest.approx(_now() - 600, abs=2)


def test_ago_hours():
    result = _parse("errors 3 hours ago")
    assert result.get("since_ts") == pytest.approx(_now() - 10800, abs=2)


def test_recent_keyword():
    result = _parse("recent errors")
    assert result.get("level") == "error"
    assert result.get("since_ts") == pytest.approx(_now() - 1800, abs=2)
