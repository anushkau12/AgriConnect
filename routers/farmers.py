from fastapi import APIRouter, Depends, Request, HTTPException, Form
from fastapi.responses import HTMLResponse

from sqlalchemy.orm import Session

from database import get_db
from models import Farmer, Produce, User
from schemas import ProduceIn, VoiceConfirmIn

from services import voice
from auth import require_role, get_current_user
from utils import render


router = APIRouter(tags=["Farmers"])

#PAGES#

@router.get("/farmer", response_class=HTMLResponse)
def farmer_page(request: Request, db: Session = Depends(get_db)):
    farmers = db.query(Farmer).order_by(Farmer.id.desc()).all()
    crops = sorted(set(voice.CROP_DICTIONARY.values()))

    user = get_current_user(request, db)
    my_farmer = db.query(Farmer).filter_by(user_id=user.id).first() if user and user.role == "farmer" else None

    return render(
        request, db, "farmer.html",
        {"farmers": farmers, "crops": crops, "voice_enabled": voice.VOICE_ENABLED, "my_farmer": my_farmer},
    )

#ENDPOINTS#

@router.post("/api/produce")
def api_add_produce(payload: ProduceIn, user: User = Depends(require_role("farmer")),
                     db: Session = Depends(get_db)):
    """Add a produce listing from a typed form (crop, qty, price). The
    listing is always attached to the logged-in farmer's own profile —
    there's no farmer_id field to trust from the client anymore."""
    farmer = db.query(Farmer).filter_by(user_id=user.id).first()
    if not farmer:
        raise HTTPException(status_code=404, detail="No farmer profile linked to this account.")
    crop_key = payload.crop_key.strip().lower()
    p = Produce(
        farmer_id=farmer.id,
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
def api_voice_transcribe(raw_transcript: str = Form(...), user: User = Depends(require_role("farmer"))):
    """
    Step 1 of voice listing (Web Speech API version):
    Accept transcribed text directly from the browser, parse crop/qty/price,
    and return the parsed fields WITHOUT saving.
    """
    return voice.parse_listing(raw_transcript)


@router.post("/api/produce/voice/confirm")
def api_voice_confirm(payload: VoiceConfirmIn, user: User = Depends(require_role("farmer")),
                       db: Session = Depends(get_db)):
    """Step 2: farmer confirms/edits the parsed listing, then it's saved
    against their own farmer profile."""
    farmer = db.query(Farmer).filter_by(user_id=user.id).first()
    if not farmer:
        raise HTTPException(status_code=404, detail="No farmer profile linked to this account.")
    crop_key = payload.crop_key.strip().lower()
    p = Produce(
        farmer_id=farmer.id,
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
