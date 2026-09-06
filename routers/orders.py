import json
import os

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse

from sqlalchemy.orm import Session

from database import Base, engine, get_db, SessionLocal
from models import Produce, Buyer, Order, OrderAllocation
from schemas import OrderIn, OrderStatusIn
from services.matching import find_farmers_for_order, find_available_transporter, InsufficientSupplyError
from services.routing import optimize_route
from utils import templates


router = APIRouter(tags=["Orders"])


#PAGES#
@router.get("/orders", response_class=HTMLResponse)
def orders_page(request: Request, db: Session = Depends(get_db)):
    orders = db.query(Order).order_by(Order.id.desc()).all()
    return templates.TemplateResponse(request, "orders.html", {"orders": orders})


@router.get("/order/{order_id}", response_class=HTMLResponse)
def order_detail_page(order_id: int, request: Request, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
        
    route = json.loads(order.route_json) if order.route_json else None
    return templates.TemplateResponse(
        request, "order_detail.html", {"order": order, "route": route}
    )

#ENDPOINTS#
@router.post("/api/order")
def api_place_order(payload: OrderIn, db: Session = Depends(get_db)):
    buyer = db.get(Buyer, payload.buyer_id)
    if not buyer:
        raise HTTPException(status_code=404, detail=f"Buyer {payload.buyer_id} not found")
        
    crop_key = payload.crop_key.strip().lower()
    qty = payload.quantity_kg

    order = Order(buyer_id=buyer.id, crop_key=crop_key, crop_name_display=crop_key,
                   quantity_requested_kg=qty, status="pending")
    db.add(order)
    db.commit()
    db.refresh(order)

    # Step 1: matching
    try:
        allocations = find_farmers_for_order(db, crop_key, qty, buyer.lat, buyer.lon)
    except InsufficientSupplyError as e:
        order.status = "failed"
        db.commit()
        raise HTTPException(status_code=409, detail={
            "order_id": order.id, "status": "failed", "reason": str(e),
        })

    for a in allocations:
        db.add(OrderAllocation(
            order_id=order.id, farmer_id=a.farmer_id, produce_id=a.produce_id,
            allocated_qty_kg=a.allocated_qty_kg,
        ))
        produce = db.get(Produce, a.produce_id)
        produce.quantity_remaining_kg -= a.allocated_qty_kg
    order.status = "matched"
    db.commit()

    # Step 2: transporter
    pickup_points = [(a.farmer_lat, a.farmer_lon) for a in allocations]
    transporter = find_available_transporter(db, qty, pickup_points, (buyer.lat, buyer.lon))
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

    # Step 3: OR-Tools route optimization
    pickup_labels = [a.farmer_name for a in allocations]
    route = optimize_route(
        depot_point=(transporter.lat, transporter.lon),
        pickup_points=pickup_points,
        pickup_labels=pickup_labels,
        dropoff_point=(buyer.lat, buyer.lon),
    )

    stop_index_by_label = {s["label"]: i for i, s in enumerate(route["ordered_stops"])}
    for a in allocations:
        oa = (
            db.query(OrderAllocation)
            .filter_by(order_id=order.id, produce_id=a.produce_id)
            .first()
        )
        oa.pickup_sequence = stop_index_by_label.get(a.farmer_name)

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


@router.post("/api/order/{order_id}/status")
def api_update_order_status(order_id: int, payload: OrderStatusIn, db: Session = Depends(get_db)):
    """Advance delivery status: route_planned -> picked_up -> delivered."""
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
        
    allowed = {"route_planned", "picked_up", "delivered", "failed"}
    if payload.status not in allowed:
        raise HTTPException(status_code=400, detail=f"status must be one of {allowed}")
    order.status = payload.status
    db.commit()
    return {"order_id": order.id, "status": order.status}


@router.get("/api/order/{order_id}")
def api_get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
        
    return {
        "id": order.id, "status": order.status,
        "crop_key": order.crop_key, "quantity_requested_kg": order.quantity_requested_kg,
        "total_distance_km": order.total_distance_km,
        "total_cost_estimate": order.total_cost_estimate,
        "route": json.loads(order.route_json) if order.route_json else None,
    }