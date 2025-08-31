# auth_code_login.py
import os
import hmac
import json
import time
import base64
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field

router = APIRouter(prefix="/auth", tags=["auth:code"])

# ---- Config (env-overridable) ----
HARDCODED_LOGIN_CODE = os.getenv("HARDCODED_LOGIN_CODE", "246810")  # <— your temp code
REQUEST_COOLDOWN_SEC = int(os.getenv("LOGIN_CODE_RESEND_COOLDOWN_SEC", "30"))
CODE_TTL_SEC = int(os.getenv("LOGIN_CODE_TTL_SEC", "600"))  # not very relevant since code is fixed
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-session-secret-change-me")

# In-memory “sent” bookkeeping so we can simulate cooldown.
_sent_index: Dict[str, float] = {}  # email -> last_sent_epoch

def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _mint_hs256_token(email: str) -> str:
    """
    Minimal HS256 JWT-like token so your frontend can stash it.
    Not a full JWT implementation—just enough for your temp flow.
    """
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": email,
        "iat": int(time.time()),
        "exp": int(time.time()) + 8 * 60 * 60,  # 8h
        "amr": ["email_code"],
    }
    h_b64 = _base64url(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode())
    p_b64 = _base64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
    signing_input = f"{h_b64}.{p_b64}".encode()
    sig = hmac.new(SESSION_SECRET.encode(), signing_input, hashlib.sha256).digest()
    s_b64 = _base64url(sig)
    return f"{h_b64}.{p_b64}.{s_b64}"

class RequestCodeBody(BaseModel):
    email: EmailStr = Field(...)

class VerifyCodeBody(BaseModel):
    email: EmailStr = Field(...)
    code: str = Field(..., min_length=4, max_length=12)

@router.post("/request-code")
async def request_code(body: RequestCodeBody):
    """
    Pretend to send a one-time code to the provided email.
    Enforces a simple cooldown per email.
    """
    email = body.email.strip().lower()
    now = time.time()

    last = _sent_index.get(email, 0)
    wait = REQUEST_COOLDOWN_SEC - int(now - last)
    if wait > 0:
        # still cooling down
        raise HTTPException(
            status_code=429,
            detail=f"Please wait {wait}s before requesting another code."
        )

    # “Send” (hard-coded) code by recording timestamp only.
    _sent_index[email] = now

    # Return some metadata the UI can optionally show
    return {
        "sent": True,
        "cooldown_sec": REQUEST_COOLDOWN_SEC,
        "expires_in_sec": CODE_TTL_SEC,
        # Do NOT return the actual code in prod; we keep it hidden even in this temp version.
        "note": "Temporary dev login: code has been generated (hard-coded on server)."
    }

@router.post("/verify-code")
async def verify_code(body: VerifyCodeBody, request: Request, response: Response):
    """
    Verify the hard-coded code, set a server-side session, and return a token.
    """
    email = body.email.strip().lower()
    code = body.code.strip()

    # Optional: require that a code was “requested” recently
    last = _sent_index.get(email, 0)
    if last <= 0 or (time.time() - last) > CODE_TTL_SEC:
        raise HTTPException(status_code=400, detail="Code expired or not requested. Please request a new code.")

    # Hard-coded check
    if code != HARDCODED_LOGIN_CODE:
        raise HTTPException(status_code=401, detail="Invalid verification code.")

    # Create/refresh server-side session
    # Starlette SessionMiddleware stores/loads via request.session (dict-like)
    request.session["user"] = {
        "email": email,
        "method": "email_code",
        "login_at": datetime.utcnow().isoformat() + "Z",
    }

    # Mint a lightweight signed token for your frontend’s persistSession()
    token = _mint_hs256_token(email)

    # (Optional) You could also set a separate cookie if you want, but not necessary
    # because SessionMiddleware already set a session cookie.
    # response.set_cookie("session_user", email, httponly=True, samesite="lax")

    return {
        "ok": True,
        "email": email,
        "token": token,
        "session": True
    }
