"""
Plain SQLAlchemy setup for FastAPI.

Flask-SQLAlchemy gave us a global `db.session` bound to the Flask app
context, plus `Model.query` on every model. FastAPI has no ORM opinion,
so we wire SQLAlchemy up ourselves: one engine, a session factory, and a
`get_db` dependency that hands each request its own session and closes it
when the request ends.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = f"sqlite:///{os.path.join(BASE_DIR, 'agrilogix.db')}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency: `db: Session = Depends(get_db)` in a route."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
