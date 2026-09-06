from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Transporter, Order
from auth import get_current_user
from utils import render


router = APIRouter(tags=["Transporters"])

#PAGES#

@router.get("/transporter", response_class=HTMLResponse)
def transporter_page(request: Request, db: Session = Depends(get_db)):
    transporters = db.query(Transporter).order_by(Transporter.id.desc()).all()

    user = get_current_user(request, db)
    my_transporter = None
    my_orders = []
    if user and user.role == "transporter":
        my_transporter = db.query(Transporter).filter_by(user_id=user.id).first()
        if my_transporter:
            my_orders = (
                db.query(Order)
                .filter_by(transporter_id=my_transporter.id)
                .order_by(Order.id.desc())
                .all()
            )

    return render(
        request, db, "transporter.html",
        {"transporters": transporters, "my_transporter": my_transporter, "my_orders": my_orders},
    )
