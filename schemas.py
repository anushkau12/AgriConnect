"""
Pydantic schemas for request validation.

Registration (Farmer/Buyer/Transporter sign-up) uses plain Form(...) fields
in routers/auth_pages.py instead of these, since it's a normal HTML form
post. These schemas cover the JSON API endpoints used once you're logged
in — farmer_id / buyer_id are no longer accepted from the client; they're
derived from the logged-in user's own profile.

This is the biggest day-to-day difference from Flask: instead of
`data = request.get_json(force=True); data["name"]` (which throws an opaque
KeyError/500 if a field is missing or the wrong type), FastAPI validates the
body against these models before your route function even runs, and returns
a clean 422 with a field-by-field error list on bad input.
"""
from typing import Optional
from pydantic import BaseModel


class ProduceIn(BaseModel):
    crop_key: str
    crop_name_hindi: Optional[str] = None
    quantity_kg: float
    price_per_kg: Optional[float] = 0.0


class VoiceConfirmIn(BaseModel):
    crop_key: str
    crop_name_raw: Optional[str] = None
    quantity_kg: float
    price_per_kg: Optional[float] = 0.0
    transcript: Optional[str] = None


class OrderIn(BaseModel):
    crop_key: str
    quantity_kg: float


class OrderStatusIn(BaseModel):
    status: str
