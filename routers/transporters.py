from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Transporter, Order
from auth import get_current_user, dashboard_url_for
from utils import render


router = APIRouter(tags=["Transporters"])

#PAGES#

@router.get("/transporter", response_class=HTMLResponse)
def transporter_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)

    # Same reasoning as /farmer and /buyer: a farmer or buyer account can
    # never have orders assigned to it here, so send them to the dashboard
    # they can actually use instead of a panel explaining they can't.
    if user and user.role in ("farmer", "buyer"):
        return RedirectResponse(url=dashboard_url_for(user), status_code=303)

    # Same rule as /farmer and /buyer: the full vehicle registry is admin-only.
    transporters = db.query(Transporter).order_by(Transporter.id.desc()).all() if user and user.role == "admin" else []

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
