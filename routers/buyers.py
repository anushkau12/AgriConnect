from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Buyer, Order
from services import voice
from auth import get_current_user, dashboard_url_for
from utils import render


router = APIRouter(tags=["Buyers"])

#PAGES#

@router.get("/buyer", response_class=HTMLResponse)
def buyer_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)

    # Same reasoning as /farmer: a farmer or transporter account can never
    # place an order here, so send them to the dashboard they can actually
    # use instead of a panel explaining they can't.
    if user and user.role in ("farmer", "transporter"):
        return RedirectResponse(url=dashboard_url_for(user), status_code=303)

    # Same rule as /farmer: the full buyer registry is admin-only.
    buyers = db.query(Buyer).order_by(Buyer.id.desc()).all() if user and user.role == "admin" else []
    crops = sorted(set(voice.CROP_DICTIONARY.values()))

    my_buyer = db.query(Buyer).filter_by(user_id=user.id).first() if user and user.role == "buyer" else None

    # Always show this buyer's own order history here, independent of
    # whatever happened in this browser session — so logging back in later
    # (or from a different device) still shows whether an order got
    # fulfilled, is still awaiting supply, etc.
    my_orders = []
    if my_buyer:
        my_orders = (
            db.query(Order)
            .filter_by(buyer_id=my_buyer.id)
            .order_by(Order.id.desc())
            .all()
        )

    return render(
        request, db, "buyer.html",
        {"buyers": buyers, "crops": crops, "my_buyer": my_buyer, "my_orders": my_orders},
    )
