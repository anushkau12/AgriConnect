import json

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from sqlalchemy.orm import Session

from database import get_db
from models import Buyer, Order, Transporter, User
from schemas import OrderIn, OrderStatusIn
from services.fulfillment import attempt_fulfillment
from services.forecasting import forecast_for_crop, forecast_all_crops
from auth import get_current_user, require_role
from utils import render, get_or_404


router = APIRouter(tags=["Orders"])


def _transporter_id_for_user(db: Session, user: User):
    t = db.query(Transporter).filter_by(user_id=user.id).first()
    return t.id if t else None


#PAGES#
@router.get("/orders", response_class=HTMLResponse)
def orders_page(request: Request, db: Session = Depends(get_db)):
    # Full order book (farmer allocations, buyers, transporters, costs) is
    # sensitive business data — only the admin gets the list view. Everyone
    # else is bounced to login/home instead of seeing every order in the system.
    user = get_current_user(request, db)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if user.role != "admin":
        return RedirectResponse(url="/", status_code=303)
    orders = db.query(Order).order_by(Order.id.desc()).all()
    return render(request, db, "orders.html", {"orders": orders})


@router.get("/order/{order_id}", response_class=HTMLResponse)
def order_detail_page(order_id: int, request: Request, db: Session = Depends(get_db)):
    order = get_or_404(db, Order, order_id)
    route = json.loads(order.route_json) if order.route_json else None
    if route and len(route.get("ordered_stops", [])) > 2:
        # Deep-link so the transporter can jump straight into turn-by-turn
        # nav for the whole route: skip the depot (stop 0) since Google Maps
        # uses the phone's live GPS as the starting point anyway, route
        # through every pickup as a waypoint, and end at the buyer.
        stops = route["ordered_stops"]
        waypoints = "|".join(f"{s['lat']},{s['lon']}" for s in stops[1:-1])
        dest = stops[-1]
        route["full_route_maps_url"] = (
            "https://www.google.com/maps/dir/?api=1"
            f"&destination={dest['lat']},{dest['lon']}"
            f"&waypoints={waypoints}"
            "&travelmode=driving"
        )

    user = get_current_user(request, db)
    is_assigned_transporter = bool(
        user and user.role == "transporter"
        and order.transporter_id == _transporter_id_for_user(db, user)
    )
    is_owning_buyer = bool(
        user and user.role == "buyer" and order.buyer.user_id == user.id
    )
    is_admin = bool(user and user.role == "admin")

    # Order data belongs to the buyer who placed it. Only that buyer, the
    # transporter assigned to it, or the admin may view it at all — not
    # "anyone with the link" or a logged-in user from an unrelated account.
    if not (is_admin or is_assigned_transporter or is_owning_buyer):
        return RedirectResponse(url="/login" if user is None else "/", status_code=303)

    # Of those who can see the order at all, only the assigned transporter
    # or admin also gets the turn-by-turn route and the status-update
    # control — the buyer sees status/cost but not operational routing.
    is_operator = is_admin or is_assigned_transporter

    return render(request, db, "order_detail.html", {
        "order": order, "route": route if is_operator else None, "is_operator": is_operator,
    })


#ENDPOINTS#
@router.post("/api/order")
def api_place_order(payload: OrderIn, user: User = Depends(require_role("buyer")),
                     db: Session = Depends(get_db)):
    buyer = db.query(Buyer).filter_by(user_id=user.id).first()
    if not buyer:
        raise HTTPException(status_code=404, detail="No buyer profile linked to this account.")

    crop_key = payload.crop_key.strip().lower()
    qty = payload.quantity_kg

    order = Order(buyer_id=buyer.id, crop_key=crop_key, crop_name_display=crop_key,
                   quantity_requested_kg=qty, status="pending")
    db.add(order)
    db.commit()
    db.refresh(order)

    # Run the match -> transporter -> route pipeline. If there isn't enough
    # supply for this crop right now, attempt_fulfillment doesn't fail the
    # order — it parks it as "awaiting_supply" so it's automatically
    # retried the moment a farmer lists more of that crop (see
    # services.fulfillment.retry_awaiting_orders, called from
    # routers/farmers.py after every new listing).
    return attempt_fulfillment(db, order)


@router.post("/api/order/{order_id}/status")
def api_update_order_status(order_id: int, payload: OrderStatusIn,
                             user: User = Depends(require_role("transporter", "admin")),
                             db: Session = Depends(get_db)):
    """Advance delivery status. Only the assigned transporter (or admin) may do this."""
    order = get_or_404(db, Order, order_id)
    if user.role == "transporter" and order.transporter_id != _transporter_id_for_user(db, user):
        raise HTTPException(status_code=403, detail="This order isn't assigned to you.")

    allowed = {"route_planned", "picked_up", "delivered", "failed"}
    if payload.status not in allowed:
        raise HTTPException(status_code=400, detail=f"status must be one of {allowed}")
    order.status = payload.status
    db.commit()
    return {"order_id": order.id, "status": order.status}


@router.get("/api/forecast")
def api_forecast_all(days: int = 7, db: Session = Depends(get_db)):
    """AI demand forecast for every crop with listings or order history.
    Trains a scikit-learn model per crop on the fly — see
    services/forecasting.py for the tiering logic."""
    return forecast_all_crops(db, days=days)


@router.get("/api/forecast/{crop_key}")
def api_forecast_crop(crop_key: str, days: int = 7, db: Session = Depends(get_db)):
    """Demand forecast for one crop_key over the next `days` days."""
    return forecast_for_crop(db, crop_key.strip().lower(), days=days)


@router.get("/api/order/{order_id}")
def api_get_order(order_id: int, db: Session = Depends(get_db)):
    order = get_or_404(db, Order, order_id)
    return {
        "id": order.id, "status": order.status,
        "crop_key": order.crop_key, "quantity_requested_kg": order.quantity_requested_kg,
        "total_distance_km": order.total_distance_km,
        "total_cost_estimate": order.total_cost_estimate,
        "route": json.loads(order.route_json) if order.route_json else None,
    }
