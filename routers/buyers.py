

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from database import Base, engine, get_db, SessionLocal
from models import Buyer
from schemas import BuyerIn
from services import voice
from utils import templates


router = APIRouter(tags=["Buyers"])

#PAGES#

@router.get("/buyer", response_class=HTMLResponse)
def buyer_page(request: Request, db: Session = Depends(get_db)):
    buyers = db.query(Buyer).order_by(Buyer.id.desc()).all()
    crops = sorted(set(voice.CROP_DICTIONARY.values()))
    return templates.TemplateResponse(
        request, "buyer.html", {"buyers": buyers, "crops": crops}
    )

#ENDPOINTS#

@router.post("/api/buyer/register")
def api_register_buyer(payload: BuyerIn, db: Session = Depends(get_db)):
    b = Buyer(name=payload.name, phone=payload.phone, lat=payload.lat, lon=payload.lon)
    db.add(b)
    db.commit()
    db.refresh(b)
    return {"id": b.id, "name": b.name}