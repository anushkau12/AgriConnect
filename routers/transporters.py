
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from database import Base, engine, get_db, SessionLocal
from models import Transporter
from schemas import TransporterIn
from utils import templates


router = APIRouter(tags=["Transporters"])
#PAGES#
@router.get("/transporter", response_class=HTMLResponse)
def transporter_page(request: Request, db: Session = Depends(get_db)):
    transporters = db.query(Transporter).order_by(Transporter.id.desc()).all()
    return templates.TemplateResponse(
        request, "transporter.html", {"transporters": transporters}
    )

#ENDPOINTS#
@router.post("/api/transporter/register")
def api_register_transporter(payload: TransporterIn, db: Session = Depends(get_db)):
    t = Transporter(
        name=payload.name, phone=payload.phone, vehicle_number=payload.vehicle_number,
        capacity_kg=payload.capacity_kg, lat=payload.lat, lon=payload.lon,
        cost_per_km=payload.cost_per_km or 15.0,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return {"id": t.id, "name": t.name}
