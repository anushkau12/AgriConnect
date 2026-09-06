"""
Order fulfillment pipeline.

This is the "try to actually satisfy this order right now" logic, pulled
out of routers/orders.py so it can be run from two different triggers:

  1. Right after a buyer places an order (routers/orders.py).
  2. Right after a farmer adds a new produce listing (routers/farmers.py) —
     so an order that couldn't be matched earlier because a crop "didn't
     exist" yet gets picked up automatically the moment it does, instead
     of staying failed forever.

Why this matters: previously, `POST /api/order` ran the match once, and if
supply was insufficient at that exact instant the order was marked
"failed" — permanently. A buyer ordering tomatoes five minutes before a
farmer lists tomatoes would just get an error and have to re-submit by
hand. Now that case is a normal, expected part of the flow: the order is
parked in "awaiting_supply" and gets automatically retried whenever
matching produce shows up.
"""
import json

from sqlalchemy.orm import Session

from models import Order, OrderAllocation, Produce
from services.matching import find_farmers_for_order, find_available_transporter, InsufficientSupplyError
from services.routing import optimize_route


def attempt_fulfillment(db: Session, order: Order) -> dict:
    """
    Run the full match -> transporter -> route pipeline for an order that
    already exists in the DB (buyer_id/crop_key/quantity_requested_kg set).

    Safe to call more than once for the same order: if it's already past
    the matching stage this just re-derives the same response from current
    state (matching only happens while allocations are empty).
    """
    buyer = order.buyer

    # Step 1: matching. If supply still can't cover the order, park it —
    # this is NOT a terminal failure, just "not yet". It'll be retried the
    # next time matching produce is listed (see retry_awaiting_orders).
    if not order.allocations:
        try:
            allocations = find_farmers_for_order(
                db, order.crop_key, order.quantity_requested_kg, buyer.lat, buyer.lon
            )
        except InsufficientSupplyError as e:
            order.status = "awaiting_supply"
            db.commit()
            return {
                "order_id": order.id, "status": "awaiting_supply", "reason": str(e),
            }

        for a in allocations:
            db.add(OrderAllocation(
                order_id=order.id, farmer_id=a.farmer_id, produce_id=a.produce_id,
                allocated_qty_kg=a.allocated_qty_kg,
            ))
            produce = db.get(Produce, a.produce_id)
            produce.quantity_remaining_kg -= a.allocated_qty_kg
        order.status = "matched"
        db.commit()
        db.refresh(order)

    # Step 2: transporter. Picked by whichever available truck has the
    # shortest total driving distance to visit ALL matched farmers —
    # not just whichever is closest to the first one — with the buyer
    # left out of this comparison entirely (see services.matching).
    pickup_points = [(a.farmer.lat, a.farmer.lon) for a in order.allocations]
    pickup_labels = [a.farmer.name for a in order.allocations]

    transporter = find_available_transporter(db, order.quantity_requested_kg, pickup_points)
    if not transporter:
        order.status = "matched"  # farmers matched, but no truck yet
        db.commit()
        return {
            "order_id": order.id, "status": "matched_no_transporter",
            "reason": "No available transporter has enough capacity right now.",
        }

    order.transporter_id = transporter.id
    order.status = "transporter_assigned"
    db.commit()

    # Step 3: OR-Tools route optimization — now the buyer IS part of the
    # route, since this is the actual delivery trip the truck will drive.
    route = optimize_route(
        depot_point=(transporter.lat, transporter.lon),
        pickup_points=pickup_points,
        pickup_labels=pickup_labels,
        dropoff_point=(buyer.lat, buyer.lon),
        dropoff_label=f"Buyer: {buyer.name}",
    )

    stop_index_by_label = {s["label"]: i for i, s in enumerate(route["ordered_stops"])}
    for a in order.allocations:
        a.pickup_sequence = stop_index_by_label.get(a.farmer.name)

    order.total_distance_km = route["total_distance_km"]
    order.total_cost_estimate = round(route["total_distance_km"] * transporter.cost_per_km, 2)
    order.route_json = json.dumps(route)
    order.status = "route_planned"
    db.commit()

    return {
        "order_id": order.id,
        "status": order.status,
        "transporter": transporter.name,
        "total_distance_km": order.total_distance_km,
        "total_cost_estimate": order.total_cost_estimate,
        "route": route,
    }


def retry_awaiting_orders(db: Session, crop_key: str):
    """
    Call this right after new produce is saved. Finds every order that's
    been parked with status "awaiting_supply" for this crop and tries to
    fulfill each one against current stock, oldest first (FIFO) so the
    buyer who ordered first gets first claim on the new listing.

    A newly-listed 40kg won't necessarily satisfy every waiting order, but
    processing oldest-first and re-querying live remaining stock on each
    attempt means whatever *can* be filled now, is — and anything still
    short just stays "awaiting_supply" for the next listing to trigger.
    """
    crop_key = crop_key.strip().lower()
    waiting_orders = (
        db.query(Order)
        .filter(Order.crop_key == crop_key, Order.status == "awaiting_supply")
        .order_by(Order.created_at.asc())
        .all()
    )
    for order in waiting_orders:
        attempt_fulfillment(db, order)


def retry_orders_needing_transporter(db: Session):
    """
    Call this right after a new transporter registers (or becomes
    available). Farmers were already matched for these orders — they're
    just stuck because no truck had enough capacity at the time — so this
    is the transporter-side equivalent of retry_awaiting_orders above.

    Without this, an order placed before any transporter existed (or
    before one with enough capacity did) would stay stuck at "matched"
    forever, even after a suitable truck registers a minute later — the
    buyer would have no way to know, and no reason to re-place the order
    since it never "failed" in the first place.

    A "matched" order always already has its farmer allocations set (see
    attempt_fulfillment) — matching and transporter-search happen in the
    same call, so status == "matched" here specifically means "farmers
    found, transporter search came up empty", not "not matched yet".
    """
    stuck_orders = (
        db.query(Order)
        .filter(Order.status == "matched")
        .order_by(Order.created_at.asc())
        .all()
    )
    for order in stuck_orders:
        attempt_fulfillment(db, order)
