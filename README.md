# AgriLogix — Smart Logistics & Route Optimization

Connects farmers, buyers, and transporters. When a buyer places an order,
the system combines produce from multiple nearby farmers, assigns a
transporter with enough capacity, and uses **Google OR-Tools** to plan the
shortest pickup-and-delivery route.

## Run it

```bash
cd agrilogix
pip install -r requirements.txt
uvicorn app:app --reload
```

Open http://127.0.0.1:8000 — it starts pre-seeded with demo farmers, a buyer,
and two transporters so you can place an order immediately (try 500 kg
tomatoes from the Buyers page, buyer ID 1). Delete `agrilogix.db` any time to
reset to a clean seeded state.

## Project layout

```
app.py               FastAPI app + all routes (pages and JSON API)
database.py           SQLAlchemy engine/session setup + get_db dependency
models.py            SQLAlchemy models (Farmer, Produce, Buyer, Transporter, Order, OrderAllocation)
schemas.py            Pydantic request schemas used for validation
services/matching.py Finds the nearest farmers that together cover an order's quantity
services/routing.py  OR-Tools route optimizer (depot -> farmer pickups -> buyer)
services/voice.py    Google Speech-to-Text / Text-to-Speech for Hindi voice listings
templates/           Server-rendered pages (Jinja2)
static/               CSS + JS (incl. push-to-talk voice recording)
```

Interactive API docs (auto-generated from the Pydantic schemas) are at
`/docs` once the server is running — handy for testing `/api/order` and the
matching/routing pipeline without going through the UI.

## How the core pipeline works

1. **Buyer places an order** (crop + quantity) → `POST /api/order`.
2. **Matching** (`services/matching.py`): pulls every farmer's listing for
   that crop with stock remaining, sorts by distance to the buyer, and
   allocates from the nearest farmers first until the quantity is covered.
3. **Transporter assignment**: picks an available vehicle with enough
   capacity, preferring the one closest to the first pickup.
4. **Route optimization** (`services/routing.py`): builds a distance matrix
   (haversine, via `geopy`) over [transporter depot, every selected farmer,
   buyer] and asks OR-Tools to find the cheapest path that starts at the
   depot, visits every farmer exactly once, and ends at the buyer. This is
   an *open* path problem (no return to depot), handled by giving
   `RoutingIndexManager` distinct start/end node indices.
5. **Delivery tracking**: order status moves
   `pending → matched → transporter_assigned → route_planned → picked_up → delivered`
   (or `failed` if supply/capacity can't be found). Update status from the
   order detail page.

## Setting up Hindi voice listings (Google Cloud Speech APIs)

Farmers can press-and-hold a mic button and speak a listing in Hindi
(e.g. *"मेरे पास 100 किलो टमाटर हैं, 18 रुपये किलो"*), which gets transcribed,
parsed into crop/quantity/price, and shown back for confirmation before saving.

1. In the [Google Cloud Console](https://console.cloud.google.com/), create
   or select a project.
2. Enable **Cloud Speech-to-Text API** and **Cloud Text-to-Speech API**
   (APIs & Services → Library).
3. Create a **Service Account** (IAM & Admin → Service Accounts), grant it
   the "Cloud Speech Client" role (or broader, for testing), and create a
   **JSON key** for it — this downloads a `.json` file.
4. Point the app at that key before starting it:
   ```bash
   export GOOGLE_APPLICATION_CREDENTIALS="/absolute/path/to/your-key.json"
   python app.py
   ```
   (Windows PowerShell: `$env:GOOGLE_APPLICATION_CREDENTIALS="C:\path\to\key.json"`)
5. Reload the Farmers page — the mic button becomes active. Until this is
   set, the app tells the farmer voice input isn't configured yet, but
   typed listings keep working normally.

Note: Google Cloud Speech-to-Text is a paid API beyond a small free tier —
check current pricing on the Cloud Console before rolling this out to real
users.

### Extending the crop vocabulary

`services/voice.py` has a small `CROP_DICTIONARY` mapping Hindi/Hinglish/
English words to a normalized `crop_key` (e.g. `"टमाटर" → "tomato"`). Add more
entries there as you onboard farmers growing other crops — in production
this should move to a database table so it's editable without a deploy.

## What's deliberately simple right now (good next steps)

- **Matching** is greedy-nearest-first, not a true optimizer over which
  *combination* of farmers minimizes total distance — good enough at small
  scale, but could be upgraded to feed candidate farmers into the same
  OR-Tools model as an assignment problem.
- **One vehicle per order.** Splitting a very large order across multiple
  trucks would extend `services/routing.py` to a proper multi-vehicle VRP
  (OR-Tools supports this natively — just add more starts/ends and a
  capacity dimension).
- **Auth** isn't implemented — farmer/buyer/transporter IDs are typed in
  by hand for this prototype. Add login before going to real users.
- **Voice NLU** is regex-based (good for structured "N किलो X, Y रुपये किलो"
  phrasing). A model-based parser would handle more natural phrasing.
