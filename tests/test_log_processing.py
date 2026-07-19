import time, pytest
import sources.logs as logs_mod


@pytest.fixture(autouse=True)
def _clear_buckets():
    logs_mod._rate_buckets.clear()
    yield
    logs_mod._rate_buckets.clear()


# --- _detect_level ------------------------------------------------------------

@pytest.mark.parametrize("line,expected", [
    ("connection Error",            "error"),
    ("Unhandled exception thrown",  "error"),
    ("CRITICAL: disk full",         "error"),
    ("fatal signal 11",             "error"),
    ("panic: nil pointer",          "error"),
    ("warn: slow query",            "warning"),
    ("WARNING: deprecated API",     "warning"),
    ("deprecated function used",    "warning"),
    ("Server started on port 8080", "info"),
    ("",                            "info"),
    ("generror is not a word",      "info"),
    ("forwarded traffic log",       "info"),
])
def test_detect_level(line, expected):
    assert logs_mod._detect_level(line) == expected


# --- _parse_json --------------------------------------------------------------

@pytest.mark.parametrize("line,exp_level,exp_msg", [
    ('{"msg":"hello","level":"info"}',        "info",    "hello"),
    ('{"message":"hi","level":"debug"}',       "debug",   "hi"),
    ('{"MESSAGE":"sys","level":"info"}',        "info",    "sys"),
    ('{"msg":"fail","severity":"ERROR"}',       "error",   "fail"),
    ('{"msg":"ping","lvl":"warn"}',             "warning", "ping"),
    ('{"msg":"oom","level":"critical"}',        "error",   "oom"),
    ('{"msg":"disk","level":"fatal"}',          "error",   "disk"),
    ('{"msg":"conn error","level":"verbose"}',  "error",   "conn error"),
    ('{"level":"info","ts":123}',               "info",    ""),
    ('"just a string"',                         "info",    ""),
    ('[1,2,3]',                                 "info",    ""),
    ("not json {",                              "info",    ""),
])
def test_parse_json(line, exp_level, exp_msg):
    level, msg = logs_mod._parse_json(line)
    assert level == exp_level
    assert msg == exp_msg


# --- _rate_ok -----------------------------------------------------------------

def test_rate_ok_under_limit():
    for _ in range(logs_mod.RATE_LIMIT):
        assert logs_mod._rate_ok("ctr") is True


def test_rate_ok_over_limit():
    for _ in range(logs_mod.RATE_LIMIT):
        logs_mod._rate_ok("ctr")
    assert logs_mod._rate_ok("ctr") is False


def test_rate_ok_independent_buckets():
    for _ in range(logs_mod.RATE_LIMIT):
        logs_mod._rate_ok("a")
    assert logs_mod._rate_ok("b") is True


def test_rate_ok_window_expiry():
    old = time.time() - 90
    logs_mod._rate_buckets["ctr"] = [old] * logs_mod.RATE_LIMIT
    assert logs_mod._rate_ok("ctr") is True


def test_rate_ok_partial_expiry():
    old = time.time() - 90
    recent = time.time() - 5
    logs_mod._rate_buckets["ctr"] = [old] * 10 + [recent] * (logs_mod.RATE_LIMIT - 11)
    assert logs_mod._rate_ok("ctr") is True


def test_rate_ok_bucket_full():
    recent = time.time() - 5
    logs_mod._rate_buckets["ctr"] = [recent] * logs_mod.RATE_LIMIT
    assert logs_mod._rate_ok("ctr") is False


# --- _is_muted ----------------------------------------------------------------

def _f(pattern, container=None, is_regex=False, compiled=None):
    return {"pattern": pattern, "container": container, "is_regex": int(is_regex), "_re": compiled}


def test_is_muted_substring_match():
    assert logs_mod._is_muted("GET /health HTTP/1.1 200", "web-1", [_f("GET /health")]) is True


def test_is_muted_substring_case_insensitive():
    assert logs_mod._is_muted("GET /HEALTH 200", "web-1", [_f("get /health")]) is True


def test_is_muted_no_match():
    assert logs_mod._is_muted("connection error", "web-1", [_f("GET /health")]) is False


def test_is_muted_regex_match():
    import re as _re
    compiled = _re.compile(r'GET /health', _re.I)
    assert logs_mod._is_muted("GET /health HTTP/1.1", "web-1", [_f(r'GET /health', is_regex=True, compiled=compiled)]) is True


def test_is_muted_regex_no_match():
    import re as _re
    compiled = _re.compile(r'POST /api', _re.I)
    assert logs_mod._is_muted("GET /health 200", "web-1", [_f(r'POST /api', is_regex=True, compiled=compiled)]) is False


def test_is_muted_invalid_regex_no_crash():
    assert logs_mod._is_muted("anything", "web-1", [_f(r'[invalid', is_regex=True, compiled=None)]) is False


def test_is_muted_container_scoped_matches():
    assert logs_mod._is_muted("healthcheck", "web-1", [_f("healthcheck", container="web-1")]) is True


def test_is_muted_container_scoped_skips_other():
    assert logs_mod._is_muted("healthcheck", "redis-1", [_f("healthcheck", container="web-1")]) is False


def test_is_muted_global_filter_any_container():
    assert logs_mod._is_muted("GET /health 200", "redis-1", [_f("GET /health", container=None)]) is True


def test_is_muted_empty_filters():
    assert logs_mod._is_muted("anything", "web-1", []) is False


def test_is_muted_multiple_filters_or_logic():
    fs = [_f("healthcheck"), _f("error")]
    assert logs_mod._is_muted("error occurred", "web-1", fs) is True
    assert logs_mod._is_muted("healthcheck", "web-1", fs) is True
    assert logs_mod._is_muted("all good", "web-1", fs) is False
