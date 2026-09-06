(function () {
  // 1. Geolocation Logic
  const geoBtn = document.getElementById('getLocationBtn');
  if (geoBtn) {
    geoBtn.addEventListener('click', function() {
      const status = document.getElementById('locationStatus');
      const lat = document.getElementById('lat');
      const lon = document.getElementById('lon');

      if (!navigator.geolocation) {
        status.style.color = "red";
        status.textContent = "Browser not supported.";
        return;
      }

      status.style.color = "blue";
      status.textContent = "Locating...";
      
      navigator.geolocation.getCurrentPosition(
        (pos) => { 
          lat.value = pos.coords.latitude; 
          lon.value = pos.coords.longitude; 
          status.style.color = "green";
          status.textContent = "✅ Location captured!"; 
        },
        (err) => { 
          status.style.color = "red";
          status.textContent = "❌ Error: " + err.message; 
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
    statusEl.style.color = "red";
    statusEl.textContent = "Browser not supported. Please use Chrome.";
    micBtn.disabled = true;
  } else {
    const recognition = new SpeechRecognition();
    recognition.lang = 'hi-IN';
    recognition.interimResults = false;

    micBtn.addEventListener("click", () => {
      recognition.start();
      micBtn.classList.add("recording");
      statusEl.style.color = "blue";
      statusEl.textContent = "Listening… speak now.";
    });

    recognition.onresult = async (event) => {
      micBtn.classList.remove("recording");
      lastTranscript = event.results[0][0].transcript;
      statusEl.style.color = "green";
      statusEl.textContent = "Parsing speech...";

      const form = new FormData();
      form.append("raw_transcript", lastTranscript);

      const res = await fetch("/api/produce/voice/transcribe", { method: "POST", body: form });
      const data = await res.json();

      if (!res.ok) {
        statusEl.style.color = "red";
        statusEl.textContent = (typeof data.detail === "string" ? data.detail : data.error) || "Could not parse text.";
        return;
      }

      transcriptEl.textContent = lastTranscript || "(nothing understood — try again)";
      cropInput.value = data.crop_key || "";
      qtyInput.value = data.quantity_kg || "";
      priceInput.value = data.price_per_kg || "";
      confirmBox.style.display = "block";
      statusEl.style.color = "black";
      statusEl.textContent = "Check the details below and confirm.";
    };

    recognition.onerror = (event) => {
      micBtn.classList.remove("recording");
      statusEl.style.color = "red";
      statusEl.textContent = "Microphone error: " + event.error;
    };
  }

  // 3. Confirm Save
  confirmBtn?.addEventListener("click", async () => {
    const farmerId = document.getElementById("voice-farmer-id").value;
    if (!farmerId) { alert("Enter the Farmer ID first."); return; }
    
    const body = {
      farmer_id: parseInt(farmerId),
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
      statusEl.style.color = "green";
      statusEl.textContent = "Listing saved. Click the mic to add another.";
      cropInput.value = "";
      qtyInput.value = "";
      priceInput.value = "";
    } else {
      const data = await res.json().catch(() => ({}));
      alert((typeof data.detail === "string" ? data.detail : data.error) || "Could not save listing.");
    }
  });
})();