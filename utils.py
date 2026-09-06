import os
from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from auth import get_current_user

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def get_or_404(db: Session, model, obj_id: int):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} {obj_id} not found")
    return obj


def render(request: Request, db: Session, name: str, context: dict = None):
    """
    Wraps TemplateResponse and injects `current_user` into every page, since
    FastAPI/Jinja2Templates has no equivalent of Flask's context_processor —
    every template needs the logged-in user for nav (login/logout links,
    role-specific links), so we do it in one place instead of every route
    remembering to pass it.
    """
    context = dict(context or {})
    context["current_user"] = get_current_user(request, db)
    return templates.TemplateResponse(request, name, context)
