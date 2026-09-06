"""
Registration -> login -> your own page.

Deliberately plain HTML <form> POSTs here (not JSON + fetch like the other
routers) because the whole point of this flow is server-side redirects —
register succeeds -> redirect to /login; login succeeds -> redirect to your
page. That's simpler as normal form submissions than as JS intercepting a
fetch response and doing the redirect itself.
"""
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from models import User, Farmer, Buyer, Transporter
from auth import hash_password, verify_password, log_in_user, log_out_user
from services.fulfillment import retry_orders_needing_transporter
from utils import render

router = APIRouter(tags=["auth"])

DASHBOARD_BY_ROLE = {
    "admin": "/",
    "farmer": "/farmer",
    "buyer": "/buyer",
    "transporter": "/transporter",
}


@router.get("/register")
def register_page(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "register.html", {"error": None})


@router.post("/register")
def register_submit(
    request: Request,
    db: Session = Depends(get_db),
    role: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    name: str = Form(...),
    phone: str = Form(""),
    village: str = Form(""),
    address: str = Form(""),
    lat: float = Form(...),
    lon: float = Form(...),
    vehicle_number: str = Form(""),
    capacity_kg: float = Form(0),
    cost_per_km: float = Form(15.0),
):
    if role not in ("farmer", "buyer", "transporter"):
        return render(request, db, "register.html", {"error": "Pick a valid account type."})

    if db.query(User).filter_by(username=username).first():
        return render(request, db, "register.html", {"error": "That username is already taken."})

    if role == "transporter" and capacity_kg <= 0:
        return render(request, db, "register.html", {"error": "Enter your vehicle's capacity in kg."})

    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.commit()
    db.refresh(user)

    if role == "farmer":
        db.add(Farmer(user_id=user.id, name=name, phone=phone, village=village, lat=lat, lon=lon))
    elif role == "buyer":
        db.add(Buyer(user_id=user.id, name=name, phone=phone, address=address, lat=lat, lon=lon))
    elif role == "transporter":
        db.add(Transporter(user_id=user.id, name=name, phone=phone, vehicle_number=vehicle_number,
                            capacity_kg=capacity_kg, lat=lat, lon=lon, cost_per_km=cost_per_km))
    db.commit()

    if role == "transporter":
        # Same idea as when a farmer lists new produce: any order that was
        # stuck waiting on a truck should get picked up automatically now
        # that one exists, instead of sitting stuck forever.
        retry_orders_needing_transporter(db)

    # Register -> login page (not auto-logged-in) -> then your own page.
    return RedirectResponse(url="/login?registered=1", status_code=303)


@router.get("/login")
def login_page(request: Request, db: Session = Depends(get_db)):
    registered = request.query_params.get("registered") == "1"
    # "role" is an optional hint carried from the farmer/buyer/transporter
    # page's own "log in" link (e.g. /login?role=buyer) — it's only used to
    # warn someone before they end up on a dashboard they didn't mean to;
    # it never overrides which account they actually log into.
    role = request.query_params.get("role")
    if role not in DASHBOARD_BY_ROLE:
        role = None
    return render(request, db, "login.html", {"error": None, "registered": registered, "role": role})


@router.post("/login")
def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
    expected_role: str = Form(None),
):
    user = db.query(User).filter_by(username=username).first()
    if not user or not verify_password(password, user.password_hash):
        return render(request, db, "login.html", {
            "error": "Incorrect username or password.", "registered": False, "role": expected_role,
        })

    # This account is a real login — but if they arrived via the buyer or
    # transporter page's "log in" link and this account is actually a
    # farmer (or vice versa), redirecting straight to their real dashboard
    # would silently land them somewhere they didn't expect with no
    # explanation. Tell them plainly instead of guessing what they meant.
    if expected_role and expected_role in DASHBOARD_BY_ROLE and user.role != expected_role:
        return render(request, db, "login.html", {
            "error": (
                f"That username belongs to a {user.role} account, not a {expected_role} account. "
                f"Log in from the {user.role} page instead, or register a new {expected_role} account."
            ),
            "registered": False, "role": expected_role,
        })

    log_in_user(request, user)
    return RedirectResponse(url=DASHBOARD_BY_ROLE[user.role], status_code=303)


@router.get("/logout")
def logout(request: Request):
    log_out_user(request)
    return RedirectResponse(url="/", status_code=303)
