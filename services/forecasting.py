"""
AI demand forecasting.

This trains a small scikit-learn regression model per crop, on the fly,
from that crop's order history — no offline pipeline, no saved model
file, no GPU. Training happens inside the request handler and takes
milliseconds because there are only ever a few dozen rows per crop; that's
a deliberate choice for this stage of the product, not a limitation of
"real" ML — the same `forecast_for_crop` function is the seam to swap in
a persisted/scheduled-retrain model later if order volume grows into the
thousands.

Model tiers, chosen automatically by how much history a crop has:
  - < 2 orders:  not enough to fit anything - return the one data point
                 (or nothing) and say so honestly.
  - 2-5 orders:  scikit-learn LinearRegression on (order index -> qty).
                 Enough data to fit a line, not enough to fit anything
                 fancier without overfitting.
  - 6+ orders:   scikit-learn RandomForestRegressor (small ensemble) on
                 (order index -> qty). Captures non-linear demand shifts
                 that a straight line would miss, once there's enough
                 history for an ensemble to make sense.

If scikit-learn isn't installed, forecasting falls back automatically to
a plain exponential-smoothing + trend estimate (see `_fallback_level_slope`)
so the app still runs - but `pip install -r requirements.txt` (which now
includes scikit-learn) gives you the real ML path.

Either way, the output converts a "typical next order size" into an
expected-demand-over-N-days figure using how often orders actually arrive,
and reports confidence honestly based on how much data backed the model.
"""
from statistics import mean

from sqlalchemy.orm import Session

from models import Order, Produce

try:
    import numpy as np
    from sklearn.linear_model import LinearRegression
    from sklearn.ensemble import RandomForestRegressor
    SKLEARN_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only if sklearn isn't installed
    SKLEARN_AVAILABLE = False

TREND_EPSILON = 0.05  # slope must exceed 5% of mean level to call it a trend
SES_ALPHA = 0.5  # only used in the no-sklearn fallback path


def _fallback_level_slope(quantities):
    """Exponential smoothing + OLS slope - used only if scikit-learn isn't
    installed, so the app degrades gracefully instead of crashing."""
    level = quantities[0]
    for q in quantities[1:]:
        level = SES_ALPHA * q + (1 - SES_ALPHA) * level
    n = len(quantities)
    xs = list(range(n))
    x_mean, y_mean = mean(xs), mean(quantities)
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, quantities))
    den = sum((x - x_mean) ** 2 for x in xs)
    slope = num / den if den else 0.0
    return level, slope


def _fit_and_predict(quantities):
    """Returns (predicted_next_order_kg, slope, model_name) using the
    tiered scikit-learn approach, or the statistical fallback if sklearn
    isn't installed."""
    n = len(quantities)

    if not SKLEARN_AVAILABLE:
        level, slope = _fallback_level_slope(quantities)
        return level, slope, "exponential_smoothing_fallback"

    X = np.arange(n).reshape(-1, 1)
    y = np.array(quantities)

    if n < 6:
        model = LinearRegression()
        model.fit(X, y)
        next_level = float(model.predict([[n]])[0])
        slope = float(model.coef_[0])
        model_name = "linear_regression"
    else:
        model = RandomForestRegressor(n_estimators=50, max_depth=4, random_state=42)
        model.fit(X, y)
        next_level = float(model.predict([[n]])[0])
        prev_level = float(model.predict([[n - 1]])[0])
        slope = next_level - prev_level
        model_name = "random_forest"

    return max(next_level, 0.0), slope, model_name


def _trend_label(slope, level):
    if level <= 0:
        return "stable"
    ratio = slope / level
    if ratio > TREND_EPSILON:
        return "rising"
    if ratio < -TREND_EPSILON:
        return "falling"
    return "stable"


def _order_history(db: Session, crop_key: str):
    return (
        db.query(Order)
        .filter(Order.crop_key == crop_key)
        .filter(Order.status != "failed")
        .order_by(Order.created_at.asc())
        .all()
    )


def forecast_for_crop(db: Session, crop_key: str, days: int = 7) -> dict:
    """AI-based forecast of expected demand (kg) for `crop_key` over the
    next `days` days, using a scikit-learn model trained on that crop's
    order history (see module docstring for the tiering logic)."""
    orders = _order_history(db, crop_key)
    n = len(orders)
    quantities = [o.quantity_requested_kg for o in orders]

    listed_kg = (
        db.query(Produce)
        .filter(Produce.crop_key == crop_key)
        .with_entities(Produce.quantity_remaining_kg)
        .all()
    )
    current_stock_kg = sum(q[0] for q in listed_kg) if listed_kg else 0.0

    base = {
        "crop_key": crop_key,
        "horizon_days": days,
        "orders_seen": n,
        "current_stock_kg": round(current_stock_kg, 1),
        "model": "none",
        "confidence": "low",
        "trend": "unknown",
        "forecast_kg": None,
        "recommended_listing_kg": None,
        "note": "",
    }

    if n == 0:
        base["note"] = "No order history for this crop yet - nothing to base a forecast on."
        return base

    if n == 1:
        guess = quantities[0]
        base.update({
            "model": "single_data_point",
            "confidence": "low",
            "trend": "unknown",
            "forecast_kg": round(guess, 1),
            "recommended_listing_kg": round(max(guess - current_stock_kg, 0), 1),
            "note": "Only one past order - not enough data to train a model on yet.",
        })
        return base

    level, slope, model_name = _fit_and_predict(quantities)
    trend = _trend_label(slope, level)

    # Convert "predicted size of the next order" into "expected demand over
    # the horizon" using how often orders actually arrive.
    t_first, t_last = orders[0].created_at, orders[-1].created_at
    span_days = max((t_last - t_first).total_seconds() / 86400.0, 0.5)
    avg_gap_days = span_days / (n - 1)
    orders_per_horizon = max(days / avg_gap_days, 1 / n)
    orders_per_horizon = min(orders_per_horizon, n)  # don't extrapolate wildly past observed order count

    forecast_kg = level * orders_per_horizon
    confidence = "low" if n < 3 else ("medium" if n < 6 else "high")

    base.update({
        "model": model_name,
        "confidence": confidence,
        "trend": trend,
        "avg_days_between_orders": round(avg_gap_days, 1),
        "typical_order_kg": round(level, 1),
        "forecast_kg": round(forecast_kg, 1),
        "recommended_listing_kg": round(max(forecast_kg - current_stock_kg, 0), 1),
        "note": "",
    })
    return base


def forecast_all_crops(db: Session, days: int = 7) -> list:
    """Forecast every crop that has at least one produce listing OR one
    order on record, so new crops with only supply (no demand yet) still show up."""
    crop_keys = set(
        k for (k,) in db.query(Produce.crop_key).distinct().all() if k
    ) | set(
        k for (k,) in db.query(Order.crop_key).distinct().all() if k
    )
    results = [forecast_for_crop(db, ck, days=days) for ck in sorted(crop_keys)]
    results.sort(key=lambda r: (
        r["trend"] != "rising",
        r["confidence"] == "low",
        -(r["forecast_kg"] or 0),
    ))
    return results
