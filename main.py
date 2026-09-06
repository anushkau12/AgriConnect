import os
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from database import Base, engine, SessionLocal
from models import User, Farmer, Produce, Buyer, Transporter, Order
from auth import hash_password

# Routers
from routers import home, farmers, buyers, transporters, orders, auth_pages

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# CHANGE THIS in production — e.g. `export AGRICONNECT_SECRET_KEY=$(openssl rand -hex 32)`.
# It signs the session cookie; anyone who has it can forge a login session.
SECRET_KEY = os.environ.get("AGRICONNECT_SECRET_KEY", "dev-only-change-me")

# Columns added to models after the table already existed somewhere. SQLite
# doesn't add these automatically — `Base.metadata.create_all()` only
# creates missing *tables*, never adds columns to one that's already there.
# Without this, an old agrilogix.db throws "no such column" the instant any
# query touches the new field (e.g. viewing an order pulls in
# `order.buyer.address`). This is a lightweight stand-in for a real
# migration tool (Alembic) — fine for this project's scale, and it's a
# no-op on a freshly created database since create_all already includes
# these columns there.
_COLUMNS_TO_BACKFILL = [
    ("buyers", "address", "VARCHAR(250)"),
]


def _backfill_missing_columns(engine):
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, coltype in _COLUMNS_TO_BACKFILL:
            if table not in existing_tables:
                continue  # brand-new db — create_all already added it correctly
            existing_columns = {c["name"] for c in inspector.get_columns(table)}
            if column not in existing_columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))
                print(f"[AgriConnect] Migrated existing database: added {table}.{column}")


def _refresh_stale_route_labels(db: Session):
    """
    Orders routed before the buyer-label fix have their route's final stop
    frozen as the old generic "Buyer (delivery)" string, baked into
    route_json at the time the route was planned — the order page only
    ever reads that stored JSON back (see routers/orders.py), it never
    recomputes it, so old orders would show the generic label forever
    without this. This just patches the label text in place; it doesn't
    touch distances/stop order, so it's cheap and doesn't need OSRM.
    """
    orders = db.query(Order).filter(Order.route_json.isnot(None)).all()
    changed = 0
    for o in orders:
        route = json.loads(o.route_json)
        stops = route.get("ordered_stops", [])
        if stops and stops[-1].get("label") == "Buyer (delivery)":
            stops[-1]["label"] = f"Buyer: {o.buyer.name}"
            o.route_json = json.dumps(route)
            changed += 1
    if changed:
        db.commit()
        print(f"[AgriConnect] Refreshed {changed} existing order route(s) with the buyer's real name.")


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

    buyers = [Buyer(name="Sunrise Mandi Traders", phone="9811111111",
                     address="Plot 14, Sunrise Mandi Yard, Sector 12, Dadri", lat=28.50, lon=77.45)]
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
    _backfill_missing_columns(engine)
    db = SessionLocal()
    try:
        _seed_demo_data_if_empty(db)
        _ensure_admin_account(db)
        _refresh_stale_route_labels(db)
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
