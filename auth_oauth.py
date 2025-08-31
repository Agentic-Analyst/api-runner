# auth_oauth.py (drop-in)
import os
import hmac
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from authlib.integrations.starlette_client import OAuth

router = APIRouter(prefix="/auth", tags=["auth"])

def _csv_env(name: str, default: str = ""):
    raw = os.getenv(name, default)
    return [v.strip() for v in raw.split(",") if v.strip()]

# ---------- Config ----------
FRONTEND_ORIGINS = _csv_env("FRONTEND_ORIGIN", "http://localhost:3000")
DEFAULT_FRONTEND = FRONTEND_ORIGINS[0]

# When serving behind a proxy at a fixed public base (recommended in prod),
# set this to "https://your-domain.com" or "https://your-domain.com/api"
# If set, we'll build callback URLs from this base instead of request.url_for.
OAUTH_REDIRECT_BASE = os.getenv("OAUTH_REDIRECT_BASE", "").rstrip("/")

AUTH_SECRET = os.getenv("AUTH_SECRET", "dev-insecure-secret-change-me")
AUTH_JWT_TTL = int(os.getenv("AUTH_JWT_TTL", "120"))  # minutes

SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "session")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
COOKIE_DOMAIN = os.getenv("COOKIE_DOMAIN", "localhost") or None  # None => host-only
COOKIE_PATH = os.getenv("COOKIE_PATH", "/")
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "lax")  # 'lax' or 'strict' or 'none'


# ---------- Helpers ----------
def _now() -> datetime:
    return datetime.now(timezone.utc)

def _issue_token(email: str, ttl_min: int = AUTH_JWT_TTL) -> str:
    exp = int((_now() + timedelta(minutes=ttl_min)).timestamp())
    body = f"{email}.{exp}".encode()
    sig = hmac.new(AUTH_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"{email}.{exp}.{sig}"

def _verify_token(token: str) -> Optional[str]:
    try:
        email, exp_s, sig = token.split(".", 2)
        body = f"{email}.{exp_s}".encode()
        good = hmac.new(AUTH_SECRET.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good):
            return None
        if int(exp_s) < int(_now().timestamp()):
            return None
        return email
    except Exception:
        return None

def _absolute_cb_url(request: Request, name: str) -> str:
    # Prefer explicit base (helps behind proxies), else derive from request
    if OAUTH_REDIRECT_BASE:
        # Root path awareness: if base ends with "/api", and route has prefix "/auth",
        # this correctly becomes ".../api/auth/..."
        return f"{OAUTH_REDIRECT_BASE}{router.url_path_for(name)}"
    return str(request.url_for(name))

# ---------- OAuth Providers ----------
oauth = OAuth()

# Google (OIDC)
oauth.register(
    name="google",
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_id=os.getenv("OAUTH_GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("OAUTH_GOOGLE_CLIENT_SECRET"),
    client_kwargs={"scope": "openid email profile"},
)

# GitHub (OAuth2)
oauth.register(
    name="github",
    client_id=os.getenv("OAUTH_GITHUB_CLIENT_ID"),
    client_secret=os.getenv("OAUTH_GITHUB_CLIENT_SECRET"),
    access_token_url="https://github.com/login/oauth/access_token",
    authorize_url="https://github.com/login/oauth/authorize",
    api_base_url="https://api.github.com/",
    client_kwargs={"scope": "read:user user:email"},
)

# ---------- Routes: Google ----------
@router.get("/google/login")
async def google_login(request: Request, redirect_url: Optional[str] = None):
    """
    Initiate Google OAuth. Optionally accept a 'redirect_url' that we store
    in the server-side session and use after successful login.
    """
    # Ensure sessions are available; otherwise Authlib will raise
    _ = request.session  # access to trigger helpful errors if missing

    if redirect_url:
        request.session["post_login_redirect"] = redirect_url

    redirect_uri = _absolute_cb_url(request, "google_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)

@router.get("/google/callback", name="google_callback")
async def google_callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    # Prefer userinfo; fallback to id_token parsing
    userinfo = token.get("userinfo")
    if not userinfo:
        userinfo = await oauth.google.parse_id_token(request, token)
    email = (userinfo or {}).get("email")
    if not email:
        raise HTTPException(status_code=400, detail="No email provided by Google")

    session_token = _issue_token(email)
    # Read and clear post-login redirect from the session
    redirect_to = request.session.pop("post_login_redirect", f"{DEFAULT_FRONTEND}/chat")
    resp = RedirectResponse(url=redirect_to, status_code=302)
    resp.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        samesite=COOKIE_SAMESITE,   # <= from env
        secure=COOKIE_SECURE,
        domain=COOKIE_DOMAIN,       # <= explicit for localhost
        path=COOKIE_PATH,           # <= explicit
        max_age=AUTH_JWT_TTL * 60,
    )
    return resp

# ---------- Routes: GitHub ----------
@router.get("/github/login")
async def github_login(request: Request, redirect_url: Optional[str] = None):
    _ = request.session
    if redirect_url:
        request.session["post_login_redirect"] = redirect_url
    redirect_uri = _absolute_cb_url(request, "github_callback")
    return await oauth.github.authorize_redirect(request, redirect_uri)

@router.get("/github/callback", name="github_callback")
async def github_callback(request: Request):
    token = await oauth.github.authorize_access_token(request)

    # Try /user first
    user = await oauth.github.get("user", token=token)
    email = (user.json() or {}).get("email")

    # Many GitHub users have email hidden; fetch from /user/emails
    if not email:
        emails = await oauth.github.get("user/emails", token=token)
        for item in emails.json() or []:
            if item.get("primary") and item.get("verified"):
                email = item.get("email")
                break

    if not email:
        raise HTTPException(status_code=400, detail="No email from GitHub")

    session_token = _issue_token(email)
    redirect_to = request.session.pop("post_login_redirect", f"{DEFAULT_FRONTEND}/chat")
    resp = RedirectResponse(url=redirect_to, status_code=302)
    resp.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        samesite=COOKIE_SAMESITE,   # <= from env
        secure=COOKIE_SECURE,
        domain=COOKIE_DOMAIN,       # <= explicit for localhost
        path=COOKIE_PATH,           # <= explicit
        max_age=AUTH_JWT_TTL * 60,
    )
    return resp

# ---------- Session / Me / Logout ----------
@router.get("/session/me")
async def session_me(request: Request):
    tok = request.cookies.get(SESSION_COOKIE_NAME)
    email = _verify_token(tok) if tok else None
    return {"authenticated": bool(email), "email": email}

@router.get("/debug/set")
async def debug_set():
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        key=SESSION_COOKIE_NAME, value="testcookie",
        httponly=True, samesite=COOKIE_SAMESITE, secure=COOKIE_SECURE,
        domain=COOKIE_DOMAIN, path=COOKIE_PATH, max_age=3600,
    )
    return resp

@router.post("/logout")
async def logout(_: Request):
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return resp

# Convenience utilities for other routes
def current_user_email(request: Request) -> Optional[str]:
    tok = request.cookies.get(SESSION_COOKIE_NAME)
    return _verify_token(tok) if tok else None

def require_user(request: Request):
    email = current_user_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return email
