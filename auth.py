"""
Minimal session-based authentication.

Design choices:
- Session cookies (Starlette's SessionMiddleware, signed with itsdangerous)
  rather than JWT. This app is server-rendered pages, not a JSON API behind
  a separate frontend, so a cookie the browser sends automatically is
  simpler than issuing/refreshing tokens client-side.
- Password hashing uses only the Python standard library — PBKDF2-HMAC-
  SHA256 with a random per-user salt, no bcrypt/passlib dependency. That's
  one less native package that can fail to install. It's a legitimate,
  still-recommended algorithm; bcrypt/argon2 are nicer at very large scale
  but this is plenty for now.

Two ways routes check auth, on purpose:
- API routes (return JSON) use `require_role(...)` as a dependency — it
  raises a clean 401/403 the caller's JS can handle.
- Page routes (return HTML) check `get_current_user(...)` manually and
  return a RedirectResponse to /login — an HTTP 401 isn't useful to a
  browser tab, a redirect is.
"""
import hashlib
import hmac
import secrets

from fastapi import Depends, Request, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import User

PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest_hex = stored.split("$")
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return hmac.compare_digest(check.hex(), digest_hex)


def log_in_user(request: Request, user: User) -> None:
    request.session["user_id"] = user.id


def log_out_user(request: Request) -> None:
    request.session.clear()


def get_current_user(request: Request, db: Session = Depends(get_db)):
    """Returns the logged-in User, or None. Never raises."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.get(User, user_id)


def require_role(*roles: str):
    """
    Dependency factory for API routes, e.g.:
        @router.post("/api/produce")
        def add_produce(..., user: User = Depends(require_role("farmer"))):
    Raises 401 if not logged in, 403 if logged in as the wrong role.
    """
    def dependency(request: Request, db: Session = Depends(get_db)):
        user = get_current_user(request, db)
        if user is None:
            raise HTTPException(status_code=401, detail="Login required.")
        if roles and user.role not in roles:
            raise HTTPException(status_code=403, detail="Not allowed for your role.")
        return user
    return dependency


DASHBOARD_BY_ROLE = {
    "admin": "/",
    "farmer": "/farmer",
    "buyer": "/buyer",
    "transporter": "/transporter",
}


def dashboard_url_for(user: User) -> str:
    """Where a logged-in user's own dashboard lives, for bouncing them off
    a page meant for a different role instead of showing them a panel that
    tells them they can't use it."""
    return DASHBOARD_BY_ROLE.get(user.role, "/")
