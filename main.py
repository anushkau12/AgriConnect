import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from database import Base, engine, SessionLocal
from models import User, Farmer, Produce, Buyer, Transporter
from auth import hash_password

# Routers
from routers import home, farmers, buyers, transporters, orders, auth_pages

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# CHANGE THIS in production — e.g. `export AGRICONNECT_SECRET_KEY=$(openssl rand -hex 32)`.
# It signs the session cookie; anyone who has it can forge a login session.
SECRET_KEY = os.environ.get("AGRICONNECT_SECRET_KEY", "dev-only-change-me")


def _seed_demo_data_if_empty(db: Session):
    if db.query(Farmer).count() > 0:
        return

    farmers = [
        Farmer(name="Ramesh Yadav", phone="9800000001", village="Bilaspur", lat=28.62, lon=77.30),
        Farmer(name="Suresh Kumar", phone="9800000002", village="Dadri", lat=28.55, lon=77.55),
        Farmer(name="Geeta Devi", phone="9800000003", village="Sikandrabad", lat=28.45, lon=77.70),
        Farmer(name="Manoj Sharma", phone="9800000004", village="Jewar", lat=28.13, lon=77.55),
    ]
    db.add_all(farmers)
    db.commit()
    for f in farmers:
        db.refresh(f)

    produce = [
        Produce(farmer_id=farmers[0].id, crop_name_hindi="टमाटर", crop_name_en="tomato",
                crop_key="tomato", quantity_kg=300, quantity_remaining_kg=300, price_per_kg=18, source="text"),
        Produce(farmer_id=farmers[1].id, crop_name_hindi="टमाटर", crop_name_en="tomato",
                crop_key="tomato", quantity_kg=250, quantity_remaining_kg=250, price_per_kg=17, source="voice",
                raw_transcript="मेरे पास 250 किलो टमाटर हैं, 17 रुपये किलो"),
        Produce(farmer_id=farmers[2].id, crop_name_hindi="टमाटर", crop_name_en="tomato",
                crop_key="tomato", quantity_kg=200, quantity_remaining_kg=200, price_per_kg=19, source="text"),
        Produce(farmer_id=farmers[3].id, crop_name_hindi="आलू", crop_name_en="potato",
                crop_key="potato", quantity_kg=500, quantity_remaining_kg=500, price_per_kg=12, source="text"),
    ]
    db.add_all(produce)

    buyers = [Buyer(name="Sunrise Mandi Traders", phone="9811111111", lat=28.50, lon=77.45)]
    db.add_all(buyers)

    transporters = [
        Transporter(name="Rakesh Transport Co.", phone="9822222222", vehicle_number="UP16-AB-1234",
                    capacity_kg=1000, lat=28.58, lon=77.40, cost_per_km=15),
        Transporter(name="Highway Logistics", phone="9822222233", vehicle_number="UP16-CD-5678",
                    capacity_kg=500, lat=28.40, lon=77.60, cost_per_km=12),
    ]
    db.add_all(transporters)
    db.commit()


def _ensure_admin_account(db: Session):
    """
    Creates the single admin account on first run. There is deliberately no
    UI path to create another admin — this is the only place it happens.
    """
    if db.query(User).filter_by(role="admin").first():
        return
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD", "changeme123")
    db.add(User(username=username, password_hash=hash_password(password), role="admin"))
    db.commit()
    print(
        f"[AgriConnect] Created the admin account — username: {username!r}, "
        f"password: {'(from ADMIN_PASSWORD env var)' if 'ADMIN_PASSWORD' in os.environ else password!r}. "
        f"Log in at /login and change this. Set ADMIN_USERNAME / ADMIN_PASSWORD env "
        f"vars before first run to customize instead."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        _seed_demo_data_if_empty(db)
        _ensure_admin_account(db)
    finally:
        db.close()
    yield


app = FastAPI(title="AgriConnect", lifespan=lifespan)

app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# Mount Routers
app.include_router(auth_pages.router)
app.include_router(home.router)
app.include_router(farmers.router)
app.include_router(buyers.router)
app.include_router(transporters.router)
app.include_router(orders.router)
