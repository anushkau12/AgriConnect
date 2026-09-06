"""
Database models for AgriConnect — Smart Logistics & Route Optimization.

FastAPI has no ORM of its own, so these are plain SQLAlchemy models built on
the `Base` declared in database.py — no `db.Model`/`db.Column` magic, and no
`Model.query` shortcut (that was a Flask-SQLAlchemy convenience). Every query
in this app now goes through an explicit `db: Session` passed into each
function/route (see database.get_db).

Design notes (unchanged from the Flask version):
- Locations are stored as (lat, lon) so we can compute real distances
  (haversine) for both matching (nearest farmers to a buyer) and routing
  (OR-Tools).
- Produce listings carry both a Hindi name (as spoken/typed by the farmer)
  and a normalized crop_key (English, lowercase) used internally for
  matching, so a buyer searching "tomatoes" matches a farmer who listed
  "टमाटर".
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    """
    Login account. Every Farmer/Buyer/Transporter profile is owned by
    exactly one User (via the user_id FK on each of those tables) — this is
    what lets us scope "my listings" / "my orders" / "my routes" to
    whoever's logged in, and lets the admin account see everything without
    owning any profile itself.
    """
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=False)
    role = Column(String(20), nullable=False)  # "admin" | "farmer" | "buyer" | "transporter"
    created_at = Column(DateTime, default=datetime.utcnow)


class Farmer(Base):
    __tablename__ = "farmers"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=True)
    name = Column(String(120), nullable=False)
    phone = Column(String(20))
    village = Column(String(120))
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")
    produce = relationship("Produce", back_populates="farmer")


class Produce(Base):
    """One listing of a crop a farmer has available."""
    __tablename__ = "produce"
    id = Column(Integer, primary_key=True)
    farmer_id = Column(Integer, ForeignKey("farmers.id"), nullable=False)

    crop_name_hindi = Column(String(120))   # e.g. "टमाटर" (as spoken/typed)
    crop_name_en = Column(String(120))      # e.g. "tomato"
    crop_key = Column(String(120), index=True)  # normalized, lowercase, used for matching

    quantity_kg = Column(Float, nullable=False)
    quantity_remaining_kg = Column(Float, nullable=False)
    price_per_kg = Column(Float, default=0.0)

    source = Column(String(20), default="text")  # "text" or "voice"
    raw_transcript = Column(Text)  # original STT transcript, for audit

    created_at = Column(DateTime, default=datetime.utcnow)

    farmer = relationship("Farmer", back_populates="produce")


class Buyer(Base):
    __tablename__ = "buyers"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=True)
    name = Column(String(120), nullable=False)
    phone = Column(String(20))
    address = Column(String(250))  # free-text delivery address, shown to the assigned transporter
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")


class Transporter(Base):
    __tablename__ = "transporters"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=True)
    name = Column(String(120), nullable=False)
    phone = Column(String(20))
    vehicle_number = Column(String(40))
    capacity_kg = Column(Float, nullable=False)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    available = Column(Boolean, default=True)
    cost_per_km = Column(Float, default=15.0)

    user = relationship("User")


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    buyer_id = Column(Integer, ForeignKey("buyers.id"), nullable=False)
    crop_key = Column(String(120))
    crop_name_display = Column(String(120))
    quantity_requested_kg = Column(Float, nullable=False)

    status = Column(String(30), default="pending")
    # pending -> matched -> transporter_assigned -> route_planned -> picked_up -> delivered -> failed

    transporter_id = Column(Integer, ForeignKey("transporters.id"), nullable=True)
    total_distance_km = Column(Float)
    total_cost_estimate = Column(Float)
    route_json = Column(Text)  # ordered list of stops, JSON-encoded

    created_at = Column(DateTime, default=datetime.utcnow)

    buyer = relationship("Buyer")
    transporter = relationship("Transporter")
    allocations = relationship("OrderAllocation", back_populates="order")


class OrderAllocation(Base):
    """How much of an order is being sourced from a specific farmer/produce listing."""
    __tablename__ = "order_allocations"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    farmer_id = Column(Integer, ForeignKey("farmers.id"), nullable=False)
    produce_id = Column(Integer, ForeignKey("produce.id"), nullable=False)
    allocated_qty_kg = Column(Float, nullable=False)
    pickup_sequence = Column(Integer)  # order in the optimized route

    order = relationship("Order", back_populates="allocations")
    farmer = relationship("Farmer")
    produce = relationship("Produce")
