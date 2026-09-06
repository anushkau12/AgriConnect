// Small helpers shared across pages: turn a <form> into a JSON POST,
// and render simple success/error notices.

function formToJSON(form) {
  const data = {};
  new FormData(form).forEach((v, k) => (data[k] = v));
  return data;
}

function showNotice(el, message, isError = false) {
  el.style.display = "block";
  el.className = "notice" + (isError ? " error" : "");
  el.textContent = message;
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

// FastAPI puts every error under "detail" (a string for most errors, a list
// of {loc, msg} for 422 validation errors, or — for our custom 409 in
// /api/order — a dict with a "reason" field). This normalizes all three
// into one readable string for the UI.
function errorMessage(data, fallback) {
  const d = data && data.detail;
  if (!d) return fallback;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((e) => `${e.loc?.slice(-1)[0]}: ${e.msg}`).join("; ");
  if (typeof d === "object") return d.reason || fallback;
  return fallback;
}

// ---- Farmer registration ----
document.getElementById("farmer-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const { ok, data } = await postJSON("/api/farmer/register", formToJSON(e.target));
  const box = document.getElementById("farmer-result");
  if (ok) showNotice(box, `Registered farmer #${data.id} — use this ID to add produce.`);
  else showNotice(box, errorMessage(data, "Could not register farmer."), true);
  if (ok) e.target.reset();
});

// ---- Typed produce listing ----
document.getElementById("produce-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const { ok, data } = await postJSON("/api/produce", formToJSON(e.target));
  const box = document.getElementById("produce-result");
  if (ok) showNotice(box, `Listing saved: ${data.quantity_kg} kg of ${data.crop_key}.`);
  else showNotice(box, errorMessage(data, "Could not save listing."), true);
  if (ok) e.target.reset();
});

// ---- Buyer registration ----
document.getElementById("buyer-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const { ok, data } = await postJSON("/api/buyer/register", formToJSON(e.target));
  const box = document.getElementById("buyer-result");
  if (ok) showNotice(box, `Registered buyer #${data.id} — use this ID to place an order.`);
  else showNotice(box, errorMessage(data, "Could not register buyer."), true);
  if (ok) e.target.reset();
});

// ---- Transporter registration ----
document.getElementById("transporter-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const { ok, data } = await postJSON("/api/transporter/register", formToJSON(e.target));
  const box = document.getElementById("transporter-result");
  if (ok) showNotice(box, `Registered vehicle #${data.id}.`);
  else showNotice(box, errorMessage(data, "Could not register vehicle."), true);
  if (ok) e.target.reset();
});

// ---- Place order (renders result inline, incl. route preview) ----
document.getElementById("order-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const resultBox = document.getElementById("order-result");
  resultBox.innerHTML = `<div class="notice">Matching farmers and planning route…</div>`;
  const { ok, data } = await postJSON("/api/order", formToJSON(e.target));

  if (!ok) {
    resultBox.innerHTML = `<div class="notice error">${errorMessage(data, "Order could not be placed.")}</div>`;
    return;
  }

  let html = `<div class="panel"><h2>Order #${data.order_id}</h2>`;
  html += `<p><span class="tag ${data.status}">${data.status.replace(/_/g, " ")}</span></p>`;
  if (data.transporter) html += `<p><strong>Transporter:</strong> ${data.transporter}</p>`;
  if (data.total_distance_km != null) html += `<p><strong>Total distance:</strong> ${data.total_distance_km} km &nbsp; <strong>Est. cost:</strong> ₹${data.total_cost_estimate}</p>`;
  if (data.route) {
    html += `<ul class="route-timeline">`;
    data.route.ordered_stops.forEach((s, i) => {
      html += `<li><span class="dot"></span><span class="stop-label">${s.label}</span>`;
      if (i > 0) html += `<div class="leg">+${s.leg_km} km leg</div>`;
      html += `</li>`;
    });
    html += `</ul>`;
  }
  html += `<p><a href="/order/${data.order_id}">View full order →</a></p></div>`;
  resultBox.innerHTML = html;
});
