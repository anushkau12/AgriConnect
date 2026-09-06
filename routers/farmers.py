from fastapi import APIRouter, Depends, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from sqlalchemy.orm import Session

from database import get_db
from models import Farmer, Produce, User, Order, OrderAllocation
from schemas import ProduceIn, VoiceConfirmIn

from services import voice
from services.fulfillment import retry_awaiting_orders
from services.forecasting import forecast_all_crops
from auth import require_role, get_current_user, dashboard_url_for
from utils import render


router = APIRouter(tags=["Farmers"])

#PAGES#

@router.get("/farmer", response_class=HTMLResponse)
def farmer_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)

    # This page's only actionable content for a non-admin is "manage your
    # own farmer profile / add listings". A buyer or transporter account
    # can never do that here, so instead of showing them a panel that just
    # explains they can't use it, send them to the dashboard they can use.
    if user and user.role in ("buyer", "transporter"):
        return RedirectResponse(url=dashboard_url_for(user), status_code=303)

    # The full "who else is registered" list is business data — only admin
    # gets it. Everyone else only sees their own profile / add-listing form.
    farmers = db.query(Farmer).order_by(Farmer.id.desc()).all() if user and user.role == "admin" else []
    crops = sorted(set(voice.CROP_DICTIONARY.values()))

    my_farmer = db.query(Farmer).filter_by(user_id=user.id).first() if user and user.role == "farmer" else None

    # Aggregate market-wide demand (not any individual buyer's business
    # data) — useful to any logged-in farmer deciding what to list more of,
    # so it's shown to farmers and admin alike (not to logged-out visitors).
    forecasts = forecast_all_crops(db, days=7) if user and user.role in ("farmer", "admin") else []

    # Orders that are actually sourcing from this farmer — who ordered,
    # how much of it is coming from them, who's picking it up, and current
    # status. A farmer has a real stake in this even though the buyer
    # placed the order, so it shouldn't be admin/buyer-only information.
    my_orders = []
    if my_farmer:
        my_orders = (
            db.query(Order)
            .join(OrderAllocation, OrderAllocation.order_id == Order.id)
            .filter(OrderAllocation.farmer_id == my_farmer.id)
            .distinct()
            .order_by(Order.id.desc())
            .all()
        )

    return render(
        request, db, "farmer.html",
        {
            "farmers": farmers, "crops": crops, "voice_enabled": voice.VOICE_ENABLED,
            "my_farmer": my_farmer, "forecasts": forecasts, "my_orders": my_orders,
        },
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
    crop_key = voice.normalize_crop_key(payload.crop_key)
    if crop_key is None:
        raise HTTPException(
            status_code=400,
            detail=f"'{payload.crop_key}' isn't a crop we recognize yet. "
                   f"Please pick one from the suggestions (e.g. {', '.join(voice.CANONICAL_CROPS[:5])}...).",
        )
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

    # A buyer may have already placed an order for this crop before this
    # listing existed — that order is sitting parked as "awaiting_supply"
    # rather than having failed outright. Now that stock exists, try to
    # fulfill any such orders right away instead of making the buyer
    # re-submit.
    retry_awaiting_orders(db, crop_key)

    return {"id": p.id, "crop_key": p.crop_key, "quantity_kg": p.quantity_kg}


@router.post("/api/produce/voice/transcribe")
def api_voice_transcribe(raw_transcript: str = Form(...), user: User = Depends(require_role("farmer"))):
    """
    Step 1 of voice listing (Web Speech API version):
    Accept transcribed text directly from the browser, parse crop/qty/price,
    and return the parsed fields WITHOUT saving.
    """
    return voice.parse_listing_llm(raw_transcript)


@router.post("/api/produce/voice/confirm")
def api_voice_confirm(payload: VoiceConfirmIn, user: User = Depends(require_role("farmer")),
                       db: Session = Depends(get_db)):
    """Step 2: farmer confirms/edits the parsed listing, then it's saved
    against their own farmer profile."""
    farmer = db.query(Farmer).filter_by(user_id=user.id).first()
    if not farmer:
        raise HTTPException(status_code=404, detail="No farmer profile linked to this account.")
    crop_key = voice.normalize_crop_key(payload.crop_key)
    if crop_key is None:
        raise HTTPException(
            status_code=400,
            detail=f"'{payload.crop_key}' isn't a crop we recognize yet. "
                   f"Please pick one from the suggestions (e.g. {', '.join(voice.CANONICAL_CROPS[:5])}...).",
        )
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

    # Same as the typed-listing endpoint: pick up any orders that were
    # waiting on this crop to become available.
    retry_awaiting_orders(db, crop_key)

    return {"id": p.id, "crop_key": p.crop_key, "quantity_kg": p.quantity_kg}
