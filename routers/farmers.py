from fastapi import APIRouter, Depends, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse

from sqlalchemy.orm import Session

from database import Base, engine, get_db, SessionLocal
from models import Farmer, Produce
from schemas import FarmerIn, ProduceIn, VoiceConfirmIn

from services import voice
from utils import templates


router = APIRouter(tags=["Farmers"])

#PAGES#

@router.get("/farmer", response_class=HTMLResponse)
def farmer_page(request: Request, db: Session = Depends(get_db)):
    farmers = db.query(Farmer).order_by(Farmer.id.desc()).all()
    crops = sorted(set(voice.CROP_DICTIONARY.values()))
    return templates.TemplateResponse(
        request, "farmer.html",
        {"farmers": farmers, "crops": crops, "voice_enabled": voice.VOICE_ENABLED},
    )

#ENDPOINTS#

@router.post("/api/farmer/register")
def api_register_farmer(payload: FarmerIn, db: Session = Depends(get_db)):
    f = Farmer(name=payload.name, phone=payload.phone, village=payload.village,
               lat=payload.lat, lon=payload.lon)
    db.add(f)
    db.commit()
    db.refresh(f)
    return {"id": f.id, "name": f.name}


@router.post("/api/produce")
def api_add_produce(payload: ProduceIn, db: Session = Depends(get_db)):
    """Add a produce listing from a typed form (crop, qty, price)."""
    crop_key = payload.crop_key.strip().lower()
    p = Produce(
        farmer_id=payload.farmer_id,
        crop_name_hindi=payload.crop_name_hindi,
        crop_name_en=crop_key,
        crop_key=crop_key,
        quantity_kg=payload.quantity_kg,
        quantity_remaining_kg=payload.quantity_kg,
        price_per_kg=payload.price_per_kg or 0,
        source="text",
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"id": p.id, "crop_key": p.crop_key, "quantity_kg": p.quantity_kg}


@router.post("/api/produce/voice/transcribe")
def api_voice_transcribe(raw_transcript: str = Form(...)):
    """
    Step 1 of voice listing (Web Speech API version): 
    Accept transcribed text directly from the browser, parse crop/qty/price, 
    and return the parsed fields WITHOUT saving.
    """
    return voice.parse_listing(raw_transcript)


@router.post("/api/produce/voice/confirm")
def api_voice_confirm(payload: VoiceConfirmIn, db: Session = Depends(get_db)):
    """Step 2: farmer confirms/edits the parsed listing, then it's saved."""
    crop_key = payload.crop_key.strip().lower()
    p = Produce(
        farmer_id=payload.farmer_id,
        crop_name_hindi=payload.crop_name_raw,
        crop_name_en=crop_key,
        crop_key=crop_key,
        quantity_kg=payload.quantity_kg,
        quantity_remaining_kg=payload.quantity_kg,
        price_per_kg=payload.price_per_kg or 0,
        source="voice",
        raw_transcript=payload.transcript,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"id": p.id, "crop_key": p.crop_key, "quantity_kg": p.quantity_kg}