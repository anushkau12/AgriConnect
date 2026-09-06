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
        db.add(Buyer(user_id=user.id, name=name, phone=phone, lat=lat, lon=lon))
    elif role == "transporter":
        db.add(Transporter(user_id=user.id, name=name, phone=phone, vehicle_number=vehicle_number,
                            capacity_kg=capacity_kg, lat=lat, lon=lon, cost_per_km=cost_per_km))
    db.commit()

    # Register -> login page (not auto-logged-in) -> then your own page.
    return RedirectResponse(url="/login?registered=1", status_code=303)


@router.get("/login")
def login_page(request: Request, db: Session = Depends(get_db)):
    registered = request.query_params.get("registered") == "1"
    return render(request, db, "login.html", {"error": None, "registered": registered})


@router.post("/login")
def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
):
    user = db.query(User).filter_by(username=username).first()
    if not user or not verify_password(password, user.password_hash):
        return render(request, db, "login.html", {"error": "Incorrect username or password.", "registered": False})

    log_in_user(request, user)
    return RedirectResponse(url=DASHBOARD_BY_ROLE[user.role], status_code=303)


@router.get("/logout")
def logout(request: Request):
    log_out_user(request)
    return RedirectResponse(url="/", status_code=303)
