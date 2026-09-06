(function () {
  // Small helper: set a status element's text using the theme's status
  // classes (defined in style.css: .ok/.error/.info/.idle) instead of
  // hardcoding browser color names, so status text always matches the
  // site's palette.
  function setStatus(el, text, kind) {
    el.textContent = text;
    el.classList.remove("ok", "error", "info", "idle");
    el.classList.add(kind);
  }

  // 1. Geolocation Logic
  const geoBtn = document.getElementById('getLocationBtn');
  if (geoBtn) {
    geoBtn.addEventListener('click', function() {
      const status = document.getElementById('locationStatus');
      const lat = document.getElementById('lat');
      const lon = document.getElementById('lon');
      status.classList.add("voice-status");

      if (!navigator.geolocation) {
        setStatus(status, "Browser not supported.", "error");
        return;
      }

      setStatus(status, "Locating...", "info");

      navigator.geolocation.getCurrentPosition(
        (pos) => {
          lat.value = pos.coords.latitude;
          lon.value = pos.coords.longitude;
          setStatus(status, "Location captured.", "ok");
        },
        (err) => {
          setStatus(status, "Error: " + err.message, "error");
        }
      );
    });
  }

  // 2. Web Speech API Logic
  const micBtn = document.getElementById("mic-btn");
  if (!micBtn) return;

  const statusEl = document.getElementById("voice-status");
  const confirmBox = document.getElementById("voice-confirm");
  const transcriptEl = document.getElementById("voice-transcript");
  const cropInput = document.getElementById("vc-crop");
  const qtyInput = document.getElementById("vc-qty");
  const priceInput = document.getElementById("vc-price");
  const confirmBtn = document.getElementById("voice-confirm-btn");

  let lastTranscript = "";
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

  if (!SpeechRecognition) {
    setStatus(statusEl, "Browser not supported. Please use Chrome.", "error");
    micBtn.disabled = true;
  } else {
    const recognition = new SpeechRecognition();
    recognition.lang = 'hi-IN';
    recognition.interimResults = false;

    micBtn.addEventListener("click", () => {
      recognition.start();
      micBtn.classList.add("recording");
      setStatus(statusEl, "Listening… speak now.", "info");
    });

    recognition.onresult = async (event) => {
      micBtn.classList.remove("recording");
      lastTranscript = event.results[0][0].transcript;
      setStatus(statusEl, "Parsing speech...", "info");

      const form = new FormData();
      form.append("raw_transcript", lastTranscript);

      const res = await fetch("/api/produce/voice/transcribe", { method: "POST", body: form });
      const data = await res.json();

      if (!res.ok) {
        setStatus(statusEl, (typeof data.detail === "string" ? data.detail : data.error) || "Could not parse text.", "error");
        return;
      }

      transcriptEl.textContent = lastTranscript || "(nothing understood — try again)";
      cropInput.value = data.crop_key || "";
      qtyInput.value = data.quantity_kg || "";
      priceInput.value = data.price_per_kg || "";
      confirmBox.style.display = "block";
      setStatus(statusEl, "Check the details below and confirm.", "idle");
    };

    recognition.onerror = (event) => {
      micBtn.classList.remove("recording");
      setStatus(statusEl, "Microphone error: " + event.error, "error");
    };
  }

  // 3. Confirm Save
  confirmBtn?.addEventListener("click", async () => {
    const body = {
      crop_key: cropInput.value,
      crop_name_raw: lastTranscript,
      quantity_kg: parseFloat(qtyInput.value),
      price_per_kg: parseFloat(priceInput.value) || 0,
      transcript: lastTranscript,
    };

    const res = await fetch("/api/produce/voice/confirm", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (res.ok) {
      confirmBox.style.display = "none";
      setStatus(statusEl, "Listing saved. Click the mic to add another.", "ok");
      cropInput.value = "";
      qtyInput.value = "";
      priceInput.value = "";
    } else {
      const data = await res.json().catch(() => ({}));
      alert((typeof data.detail === "string" ? data.detail : data.error) || "Could not save listing.");
    }
  });
})();
