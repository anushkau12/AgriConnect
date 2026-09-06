from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Buyer
from services import voice
from auth import get_current_user
from utils import render


router = APIRouter(tags=["Buyers"])

#PAGES#

@router.get("/buyer", response_class=HTMLResponse)
def buyer_page(request: Request, db: Session = Depends(get_db)):
    buyers = db.query(Buyer).order_by(Buyer.id.desc()).all()
    crops = sorted(set(voice.CROP_DICTIONARY.values()))

    user = get_current_user(request, db)
    my_buyer = db.query(Buyer).filter_by(user_id=user.id).first() if user and user.role == "buyer" else None

    return render(
        request, db, "buyer.html", {"buyers": buyers, "crops": crops, "my_buyer": my_buyer}
    )
