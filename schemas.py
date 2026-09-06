"""
Pydantic schemas for request validation.

This is the biggest day-to-day difference from Flask: instead of
`data = request.get_json(force=True); data["name"]` (which throws an opaque
KeyError/500 if a field is missing or the wrong type), FastAPI validates the
body against these models before your route function even runs, and returns
a clean 422 with a field-by-field error list on bad input.
"""
from typing import Optional
from pydantic import BaseModel


class FarmerIn(BaseModel):
    name: str
    phone: Optional[str] = None
    village: Optional[str] = None
    lat: float
    lon: float


class ProduceIn(BaseModel):
    farmer_id: int
    crop_key: str
    crop_name_hindi: Optional[str] = None
    quantity_kg: float
    price_per_kg: Optional[float] = 0.0


class VoiceConfirmIn(BaseModel):
    farmer_id: int
    crop_key: str
    crop_name_raw: Optional[str] = None
    quantity_kg: float
    price_per_kg: Optional[float] = 0.0
    transcript: Optional[str] = None


class BuyerIn(BaseModel):
    name: str
    phone: Optional[str] = None
    lat: float
    lon: float


class TransporterIn(BaseModel):
    name: str
    phone: Optional[str] = None
    vehicle_number: Optional[str] = None
    capacity_kg: float
    lat: float
    lon: float
    cost_per_km: Optional[float] = 15.0


class OrderIn(BaseModel):
    buyer_id: int
    crop_key: str
    quantity_kg: float


class OrderStatusIn(BaseModel):
    status: str
