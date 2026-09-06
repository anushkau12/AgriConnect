from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Farmer, Buyer, Transporter, Order
from utils import templates

router=APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    stats = {
        "farmers": db.query(Farmer).count(),
        "buyers": db.query(Buyer).count(),
        "transporters": db.query(Transporter).count(),
        "orders": db.query(Order).count(),
    }
    recent_orders = db.query(Order).order_by(Order.id.desc()).limit(6).all()
    return templates.TemplateResponse(
        request, "index.html", {"stats": stats, "recent_orders": recent_orders}
    )