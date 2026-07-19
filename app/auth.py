import os, secrets, time
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Cookie, HTTPException, Request

_USER     = os.getenv("FATHOM_USER", "admin")
_PASSWORD = os.getenv("FATHOM_PASSWORD", "")
_SECRET   = os.getenv("FATHOM_SECRET") or secrets.token_hex(32)
_SIGNER   = URLSafeTimedSerializer(_SECRET, salt="fathom-session")
_MAX_AGE  = 7 * 86400

AUTH_DISABLED = not _PASSWORD

def make_session_cookie() -> str:
    return _SIGNER.dumps({"u": _USER, "t": int(time.time())})

def verify_session(token: str) -> bool:
    try:
        _SIGNER.loads(token, max_age=_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False

def check_credentials(username: str, password: str) -> bool:
    return (
        secrets.compare_digest(username, _USER)
        and secrets.compare_digest(password, _PASSWORD)
    )

def require_auth(request: Request, fathom_session: str | None = Cookie(default=None)):
    if AUTH_DISABLED:
        return
    if fathom_session and verify_session(fathom_session):
        return
    raise HTTPException(status_code=302, headers={"Location": "/login"})
