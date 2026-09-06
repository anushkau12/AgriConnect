"""
Fix a wrong latitude/longitude for a farmer, buyer, or transporter — for
when someone mistypes their location during registration (there's no
"edit profile" page yet, so this is the quick way to correct it without
touching anything else in the database).

Usage:
    python fix_location.py <username> <new_lat> <new_lon>

Example:
    python fix_location.py metro_fresh_username 28.58 77.32

Only changes that one account's lat/lon. Everything else (produce
listings, past orders, routes already planned) is left untouched — if an
order was already routed with the WRONG location, you'll need to re-place
that order for the corrected location to actually be used, since routes
are computed once and stored, not recalculated live.
"""
import sys

from database import SessionLocal
from models import User, Farmer, Buyer, Transporter

ROLE_MODEL = {"farmer": Farmer, "buyer": Buyer, "transporter": Transporter}


def fix_location(username: str, new_lat: float, new_lon: float):
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username=username).first()
        if not user:
            print(f"No account found with username {username!r}.")
            return

        model = ROLE_MODEL.get(user.role)
        if model is None:
            print(f"Account {username!r} has role {user.role!r} — nothing to fix here (only farmer/buyer/transporter have a location).")
            return

        profile = db.query(model).filter_by(user_id=user.id).first()
        if not profile:
            print(f"Account {username!r} has no linked {user.role} profile.")
            return

        print(f"{username!r} ({user.role}, {profile.name!r}) currently: lat={profile.lat}, lon={profile.lon}")
        profile.lat = new_lat
        profile.lon = new_lon
        db.commit()
        print(f"Updated to: lat={new_lat}, lon={new_lon}")
        print("Note: any order already routed using the old location keeps its old route/distance/cost. "
              "Re-place the order if you need it recalculated with the corrected location.")
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python fix_location.py <username> <new_lat> <new_lon>")
        sys.exit(1)
    fix_location(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]))
