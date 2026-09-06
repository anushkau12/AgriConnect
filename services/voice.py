"""
Voice layer for farmers: speak a listing in Hindi ("मेरे पास 100 किलो टमाटर हैं,
20 रुपये किलो") -> transcribed text -> parsed into (crop, quantity_kg, price_per_kg).

Uses Google Cloud Speech-to-Text (STT) and Text-to-Speech (TTS).

SETUP REQUIRED (you provide this — nothing here has real credentials):
  1. Create/select a project in Google Cloud Console.
  2. Enable "Cloud Speech-to-Text API" and "Cloud Text-to-Speech API".
  3. Create a Service Account, grant it "Cloud Speech Client" role, download
     its JSON key.
  4. Set the environment variable before running the app:
         export GOOGLE_APPLICATION_CREDENTIALS="/path/to/key.json"
     (On Windows: set GOOGLE_APPLICATION_CREDENTIALS=C:\\path\\to\\key.json)
  5. pip install google-cloud-speech google-cloud-texttospeech

Until credentials are configured, `transcribe_audio` falls back to a clear
error message rather than crashing the whole app, so the rest of the site
(text-based listing entry) keeps working.
"""
import os
import re

VOICE_ENABLED = bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))

# Minimal Hindi <-> English crop dictionary. Extend this as needed — in
# production, back this with a proper crops table so buyers/farmers can
# add new produce names without a code change.
CROP_DICTIONARY = {
    "टमाटर": "tomato", "tamatar": "tomato", "tomato": "tomato",
    "आलू": "potato", "aloo": "potato", "potato": "potato",
    "प्याज": "onion", "pyaz": "onion", "onion": "onion",
    "गेहूं": "wheat", "gehun": "wheat", "wheat": "wheat",
    "चावल": "rice", "chawal": "rice", "rice": "rice",
    "गोभी": "cauliflower", "gobhi": "cauliflower", "cauliflower": "cauliflower",
    "मिर्च": "chilli", "mirch": "chilli", "chilli": "chilli",
    "भिंडी": "okra", "bhindi": "okra", "okra": "okra",
}

# Matches "100 किलो" / "100 kg" / "100किग्रा" etc.
_QTY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:किलो|किग्रा|kg|kilo)", re.IGNORECASE)
_PRICE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:रुपये?|रुपए|रुपया|rs\.?|rupees?)\s*(किलो|per\s*kilo|प्रति\s*किलो|kg)?",
    re.IGNORECASE
)

def transcribe_audio(audio_bytes: bytes, sample_rate_hz: int = 48000, encoding: str = "WEBM_OPUS") -> str:
    """
    Sends recorded audio to Google Cloud Speech-to-Text with language "hi-IN"
    and returns the transcript. Raises RuntimeError with a friendly message
    if credentials aren't configured.

    Default encoding is WEBM_OPUS at 48kHz because that's what the browser's
    MediaRecorder API produces by default (see static/js/voice.js) — no
    client-side transcoding needed.
    """
    if not VOICE_ENABLED:
        raise RuntimeError(
            "Voice input isn't configured yet. Set GOOGLE_APPLICATION_CREDENTIALS "
            "to a Google Cloud service-account key with Speech-to-Text access, "
            "then restart the server. Text-based listing entry still works."
        )

    from google.cloud import speech

    client = speech.SpeechClient()
    audio = speech.RecognitionAudio(content=audio_bytes)
    config = speech.RecognitionConfig(
        encoding=getattr(speech.RecognitionConfig.AudioEncoding, encoding),
        sample_rate_hertz=sample_rate_hz,
        language_code="hi-IN",
        alternative_language_codes=["en-IN"],  # farmers may mix Hindi/English
    )
    response = client.recognize(config=config, audio=audio)
    transcript = " ".join(r.alternatives[0].transcript for r in response.results)
    return transcript.strip()


def synthesize_speech(text: str) -> bytes:
    """
    Converts a Hindi confirmation message to speech (MP3 bytes) using Google
    Cloud Text-to-Speech, e.g. to read back "आपकी 100 किलो टमाटर की लिस्टिंग
    दर्ज हो गई है" to a farmer who can't read.
    """
    if not VOICE_ENABLED:
        raise RuntimeError(
            "Voice output isn't configured yet. Set GOOGLE_APPLICATION_CREDENTIALS "
            "to enable Text-to-Speech."
        )

    from google.cloud import texttospeech

    client = texttospeech.TextToSpeechClient()
    synthesis_input = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(
        language_code="hi-IN",
        ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3
    )
    response = client.synthesize_speech(
        input=synthesis_input, voice=voice, audio_config=audio_config
    )
    return response.audio_content


def parse_listing(transcript: str):
    """
    Very lightweight NLU: pulls a known crop name, a quantity in kg, and an
    optional price per kg out of a Hindi/English transcript.

    Returns dict: {"crop_key": str|None, "crop_name_raw": str|None,
                    "quantity_kg": float|None, "price_per_kg": float|None}
    Anything not confidently found is returned as None so the caller (Flask
    route) can ask the farmer to confirm/correct via a simple form instead
    of silently guessing.
    """
    text = transcript.strip()
    lower = text.lower()

    crop_key = None
    crop_name_raw = None
    for word, key in CROP_DICTIONARY.items():
        if word in text or word in lower:
            crop_key = key
            crop_name_raw = word
            break

    qty_match = _QTY_RE.search(text)
    quantity_kg = float(qty_match.group(1)) if qty_match else None

    price_match = _PRICE_RE.search(text)
    price_per_kg = float(price_match.group(1)) if price_match else None

    return {
        "crop_key": crop_key,
        "crop_name_raw": crop_name_raw,
        "quantity_kg": quantity_kg,
        "price_per_kg": price_per_kg,
        "transcript": transcript,
    }
