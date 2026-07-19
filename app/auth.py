import os, secrets, time
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Cookie, HTTPException, Request

_USER     = os.getenv("FATHOM_USER", "admin")
_PASSWORD = os.getenv("FATHOM_PASSWORD", "")
_MAX_AGE  = 7 * 86400

AUTH_DISABLED = not _PASSWORD

_SIGNER: URLSafeTimedSerializer | None = None

def init_secret(conn_fn) -> None:
    """Called from lifespan after db.init_db(). Loads or generates a persistent secret."""
    global _SIGNER
    env_secret = os.getenv("FATHOM_SECRET")
    if env_secret:
        _SIGNER = URLSafeTimedSerializer(env_secret, salt="fathom-session")
        return
    with conn_fn() as c:
        row = c.execute("SELECT value FROM meta WHERE key='session_secret'").fetchone()
        if row:
            secret = row[0]
        else:
            secret = secrets.token_hex(32)
            c.execute("INSERT INTO meta(key, value) VALUES('session_secret', ?)", (secret,))
    _SIGNER = URLSafeTimedSerializer(secret, salt="fathom-session")

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
