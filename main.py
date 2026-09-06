import os
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from database import Base, engine, SessionLocal
from models import User, Order
from auth import hash_password
from services.fulfillment import retry_orders_needing_transporter

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


def _ensure_admin_account(db: Session):
    """
    Creates the single admin account on first run. There is deliberately no
    UI path to create another admin — this is the only place it happens.

    The password is never printed to the terminal — logs get copy-pasted
    into chats, screen-shared during demos, and captured by CI, and a
    plaintext credential sitting in scrollback is an easy way to leak it
    even on a "local dev" project. Set ADMIN_USERNAME / ADMIN_PASSWORD
    before first run to choose your own; otherwise see README.md for the
    documented default and change it immediately after logging in.
    """
    if db.query(User).filter_by(role="admin").first():
        return
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD", "changeme123")
    db.add(User(username=username, password_hash=hash_password(password), role="admin"))
    db.commit()
    print(
        f"[AgriConnect] Created the admin account — username: {username!r}. "
        f"Password was NOT printed here for security "
        f"({'set via ADMIN_PASSWORD env var — use that value' if 'ADMIN_PASSWORD' in os.environ else 'see README.md for the default, and change it after logging in'})."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _backfill_missing_columns(engine)
    db = SessionLocal()
    try:
        _ensure_admin_account(db)
        _refresh_stale_route_labels(db)
        # Catch-all safety net: retry any order still stuck at "matched"
        # (farmers found, no truck had capacity at the time) against
        # whatever transporters exist right now. Covers cases the
        # register-time retry can miss — e.g. a transporter that was
        # already registered before this fix was deployed, or the server
        # being down at the moment a transporter registered.
        retry_orders_needing_transporter(db)
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
