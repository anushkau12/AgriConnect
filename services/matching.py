"""
Matching engine.

Given a buyer's requirement (crop + quantity + location), find the smallest
set of nearby farmers whose combined available produce satisfies the order,
minimizing total pickup distance where possible.

Strategy (greedy nearest-first, good enough for this scale and keeps the
routing problem that follows small and sane):
  1. Pull all Produce rows for the crop with quantity_remaining_kg > 0.
  2. Sort candidates by distance from the buyer (closest farmers first) —
     this biases the selection toward farmers that are already convenient,
     so the OR-Tools route optimizer downstream has a good starting set.
  3. Walk the sorted list, allocating from each farmer until the requested
     quantity is covered. The last farmer used may be partially allocated.

This is intentionally simple (not a bin-packing optimizer) because farmers
are perishable-goods suppliers, not interchangeable units — closeness and
freshness matter more than a mathematically perfect combination.

NOTE (Flask -> FastAPI change): Flask-SQLAlchemy's `Model.query` shortcut
doesn't exist in plain SQLAlchemy, so every function here now takes an
explicit `db: Session` (the same session FastAPI injected into the route via
`Depends(get_db)`) and queries through it: `db.query(Produce)...` instead of
`Produce.query...`.
"""
from dataclasses import dataclass
from sqlalchemy.orm import Session
from geopy.distance import geodesic

from models import Produce, Farmer, Transporter


@dataclass
class Allocation:
    farmer_id: int
    produce_id: int
    farmer_name: str
    farmer_lat: float
    farmer_lon: float
    allocated_qty_kg: float
    distance_km: float


class InsufficientSupplyError(Exception):
    def __init__(self, available_qty, requested_qty):
        self.available_qty = available_qty
        self.requested_qty = requested_qty
        super().__init__(
            f"Only {available_qty:.1f} kg available across all farmers, "
            f"but {requested_qty:.1f} kg was requested."
        )


def find_farmers_for_order(db: Session, crop_key: str, requested_qty_kg: float,
                            buyer_lat: float, buyer_lon: float):
    """
    Returns a list[Allocation] that together sum to requested_qty_kg,
    drawn from the nearest available farmers first.
    Raises InsufficientSupplyError if total supply can't cover the order.
    """
    candidates = (
        db.query(Produce)
        .join(Farmer, Produce.farmer_id == Farmer.id)
        .filter(Produce.crop_key == crop_key, Produce.quantity_remaining_kg > 0)
        .all()
    )

    buyer_point = (buyer_lat, buyer_lon)
    scored = []
    for p in candidates:
        f = p.farmer
        dist_km = geodesic(buyer_point, (f.lat, f.lon)).km
        scored.append((dist_km, p, f))

    scored.sort(key=lambda t: t[0])  # nearest first

    total_available = sum(p.quantity_remaining_kg for _, p, _ in scored)
    if total_available < requested_qty_kg:
        raise InsufficientSupplyError(total_available, requested_qty_kg)

    allocations = []
    remaining = requested_qty_kg
    for dist_km, p, f in scored:
        if remaining <= 0:
            break
        take = min(p.quantity_remaining_kg, remaining)
        if take <= 0:
            continue
        allocations.append(Allocation(
            farmer_id=f.id,
            produce_id=p.id,
            farmer_name=f.name,
            farmer_lat=f.lat,
            farmer_lon=f.lon,
            allocated_qty_kg=take,
            distance_km=round(dist_km, 2),
        ))
        remaining -= take

    return allocations


def find_available_transporter(db: Session, total_weight_kg: float, pickup_points, dropoff_point):
    """
    Picks the best available transporter: must have enough capacity, and
    among those, prefer the one whose depot is closest to the first pickup
    (minimizes deadhead travel before the route even starts).
    pickup_points: list of (lat, lon)
    dropoff_point: (lat, lon)
    """
    candidates = (
        db.query(Transporter)
        .filter(Transporter.available == True, Transporter.capacity_kg >= total_weight_kg)  # noqa: E712
        .all()
    )

    if not candidates:
        return None

    anchor = pickup_points[0] if pickup_points else dropoff_point
    best = min(candidates, key=lambda t: geodesic(anchor, (t.lat, t.lon)).km)
    return best
