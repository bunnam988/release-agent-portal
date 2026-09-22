import secrets
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..config import settings
from ..schemas import LoginRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "portal_session"
# 7 days -- long enough that a shared internal tool doesn't nag people to
# re-enter one shared password constantly, short enough that a stolen
# cookie doesn't grant access forever.
MAX_AGE_SECONDS = 7 * 24 * 60 * 60

Role = Literal["admin", "user"]

_serializer = URLSafeTimedSerializer(settings.session_secret, salt="portal-session")


def auth_enabled() -> bool:
    return bool(settings.admin_password or settings.user_password)


def _role_for_password(password: str) -> Role | None:
    # Guard against a blank configured password matching a blank submitted
    # one -- compare_digest("", "") is True, which would otherwise let an
    # empty password in in the (mis)configuration where one role's
    # password was never set.
    if settings.admin_password and secrets.compare_digest(password, settings.admin_password):
        return "admin"
    if settings.user_password and secrets.compare_digest(password, settings.user_password):
        return "user"
    return None


def get_role(request: Request) -> Role | None:
    """Used by /api/auth/status, the enforcement middleware in main.py, and
    the per-workflow admin_only check in routers/workflows.py + sessions.py
    -- kept here, next to where the cookie is issued, so all of them can't
    drift out of sync on what a valid token means."""
    if not auth_enabled():
        # Auth disabled entirely -- treat every request as admin so
        # nothing changes for anyone not using this feature.
        return "admin"
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    role = data.get("role")
    return role if role in ("admin", "user") else None


@router.get("/status")
async def status(request: Request):
    role = get_role(request)
    return {"auth_enabled": auth_enabled(), "authenticated": role is not None, "role": role}


@router.post("/login")
async def login(req: LoginRequest, response: Response):
    if not auth_enabled():
        return {"ok": True, "role": "admin"}
    role = _role_for_password(req.password)
    if role is None:
        raise HTTPException(status_code=401, detail="Incorrect password.")
    token = _serializer.dumps({"role": role})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        # Not "secure": this is served over plain HTTP today (localhost /
        # internal network). Revisit once this sits behind real TLS.
    )
    return {"ok": True, "role": role}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}
