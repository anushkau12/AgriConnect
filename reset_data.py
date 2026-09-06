"""
Wipe all app data — every farmer, produce listing, buyer, transporter,
order, and order allocation, plus every non-admin login account — while
keeping admin account(s) exactly as they are.

This does NOT touch the `users` rows where role == "admin", so if you've
already changed the admin password, it's preserved.

Usage:
    python reset_data.py           # asks for confirmation first
    python reset_data.py --yes     # skips the confirmation prompt

Safe to run against the live agrilogix.db — it commits everything as one
transaction, so it either fully succeeds or leaves the database untouched.
"""
import sys

from database import SessionLocal
from models import User, Farmer, Produce, Buyer, Transporter, Order, OrderAllocation


def reset_all_data():
    db = SessionLocal()
    try:
        counts = {
            "orders": db.query(Order).count(),
            "order_allocations": db.query(OrderAllocation).count(),
            "produce": db.query(Produce).count(),
            "farmers": db.query(Farmer).count(),
            "buyers": db.query(Buyer).count(),
            "transporters": db.query(Transporter).count(),
            "non_admin_users": db.query(User).filter(User.role != "admin").count(),
        }
        total = sum(counts.values())
        if total == 0:
            print("Nothing to delete — the database already has no farmers/buyers/"
                  "transporters/orders/non-admin accounts.")
            return

        print("This will permanently delete:")
        for label, n in counts.items():
            if n:
                print(f"  - {n} {label.replace('_', ' ')}")
        admin_count = db.query(User).filter_by(role="admin").count()
        print(f"Admin account(s) ({admin_count}) will be kept untouched.")

        if "--yes" not in sys.argv:
            answer = input("Type 'yes' to continue: ").strip().lower()
            if answer != "yes":
                print("Cancelled — nothing was deleted.")
                return

        # Children before parents, to respect foreign keys.
        db.query(OrderAllocation).delete()
        db.query(Order).delete()
        db.query(Produce).delete()
        db.query(Farmer).delete()
        db.query(Buyer).delete()
        db.query(Transporter).delete()
        db.query(User).filter(User.role != "admin").delete()
        db.commit()
        print("Done. All data cleared except admin account(s).")
    finally:
        db.close()


if __name__ == "__main__":
    reset_all_data()
