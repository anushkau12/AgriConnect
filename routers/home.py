from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Farmer, Buyer, Transporter, Order
from services.forecasting import forecast_all_crops
from auth import get_current_user
from utils import render

router=APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    stats = {
        "farmers": db.query(Farmer).count(),
        "buyers": db.query(Buyer).count(),
        "transporters": db.query(Transporter).count(),
        "orders": db.query(Order).count(),
    }
    # Order details (crop, quantity, status) are business data belonging to
    # whichever buyer placed them — the public home page must not leak them.
    # Buyers/transporters/farmers see their own orders on their own
    # dashboards (/buyer, /transporter); the full list across everyone is
    # admin-only, on /orders. This panel is admin-only too, for the same
    # reason.
    user = get_current_user(request, db)
    recent_orders = (
        db.query(Order).order_by(Order.id.desc()).limit(6).all()
        if user and user.role == "admin" else []
    )
    # Same reasoning: aggregate demand-per-crop is planning data for the
    # whole marketplace, so it's admin-only on this page (farmers get a
    # scoped view of it on their own /farmer page instead).
    forecasts = forecast_all_crops(db, days=7) if user and user.role == "admin" else []
    return render(
        request, db, "index.html",
        {"stats": stats, "recent_orders": recent_orders, "forecasts": forecasts},
    )