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

PARSING the transcript into (crop, quantity, price) has two paths:
  - parse_listing_llm(): sends the transcript to Groq along with the list
    of crops this app actually knows about (CROP_DICTIONARY), and asks it
    to extract quantity/price and pick the closest matching crop from that
    list — or say it doesn't recognize one. Groq handles the messy
    Hindi/English/typo/dialect part; it does NOT get to invent a new
    crop_key on its own.
  - parse_listing(): the original hand-built dictionary + regex approach.
    Zero setup/cost, only recognizes the crops in CROP_DICTIONARY below.

Either way, whatever crop name comes back is validated against
CROP_DICTIONARY before it's trusted as a crop_key:
  - Groq's guess is looked up in CROP_DICTIONARY same as any dictionary
    hit would be. If it matches (exactly, or via a known spelling), we use
    the normalized key.
  - If it doesn't match anything we know, crop_key comes back None (with
    crop_name_raw set to whatever was said) instead of silently saving an
    unmatched string — the route can then ask the farmer to confirm/retry
    rather than creating a listing no buyer search will ever find.

This is the point: Groq is the flexible language layer, CROP_DICTIONARY
is the validation layer. Groq's raw output never touches the database
directly — same idea whether the transcript came from parse_listing() or
parse_listing_llm(). parse_listing_llm() is the one to call from routes;
it automatically falls back to parse_listing() if GROQ_API_KEY isn't set
or the API call fails, so the feature never hard-breaks — same "degrade
gracefully" pattern as transcribe_audio() above.
"""
import os
import re
import json

VOICE_ENABLED = bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))
GROQ_ENABLED = bool(os.environ.get("GROQ_API_KEY"))
GROQ_MODEL = "openai/gpt-oss-120b"

print(f"[voice] GROQ_ENABLED = {GROQ_ENABLED}  (set GROQ_API_KEY and restart if this is False)")

# Hindi <-> English crop dictionary. This is the single source of truth
# for which crops the app supports — the regex parser below, the Groq
# validation step, AND the buyer/farmer dropdowns (see routers/farmers.py,
# routers/buyers.py) all read from CROP_DICTIONARY, so a crop typed,
# spoken, or Groq-extracted always normalizes to the same crop_key a buyer
# can search for. Extend this as new produce comes up.
CROP_DICTIONARY = {
    "टमाटर": "tomato", "tamatar": "tomato", "tomato": "tomato",
    "आलू": "potato", "aloo": "potato", "potato": "potato",
    "प्याज": "onion", "pyaz": "onion", "onion": "onion",
    "गेहूं": "wheat", "gehun": "wheat", "wheat": "wheat",
    "चावल": "rice", "chawal": "rice", "rice": "rice",
    "गोभी": "cauliflower", "gobhi": "cauliflower", "cauliflower": "cauliflower",
    "मिर्च": "chilli", "mirch": "chilli", "chilli": "chilli",
    "भिंडी": "okra", "bhindi": "okra", "okra": "okra",
    "परवल": "parwal", "parwal": "parwal", "parval": "parwal", "pointed gourd": "parwal",
    "करेला": "karela", "karela": "karela", "bitter gourd": "karela",
    "टिंडा": "tinda", "tinda": "tinda", "round gourd": "tinda",
    "लौकी": "lauki", "lauki": "lauki", "bottle gourd": "lauki", "doodhi": "lauki",
    "बैंगन": "brinjal", "baingan": "brinjal", "brinjal": "brinjal", "eggplant": "brinjal",
    "गाजर": "carrot", "gajar": "carrot", "carrot": "carrot",
    "मटर": "peas", "matar": "peas", "peas": "peas",
    "पालक": "spinach", "palak": "spinach", "spinach": "spinach",
    "अरबी": "arbi", "arbi": "arbi", "colocasia": "arbi",
    "आम": "mango", "aam": "mango", "mango": "mango",
    "अमरूद": "guava", "amrud": "guava", "guava": "guava",
}

# Canonical crop_key -> the value CROP_DICTIONARY maps everything to,
# de-duplicated. This is what we show Groq as "the list to pick from",
# and also what a Groq answer is checked against directly (in case Groq
# already replies with the canonical key itself, e.g. "tomato").
_CANONICAL_CROPS = sorted(set(CROP_DICTIONARY.values()))


def _normalize_crop_guess(raw_guess: str):
    """
    Looks up a free-text crop guess (from Groq, a typed form, or anywhere
    else) against CROP_DICTIONARY. Returns the canonical crop_key if it
    matches (case-insensitively, exact key or exact canonical value), else
    None. This is the ONLY thing allowed to turn a guess into a crop_key
    that gets saved to the database — nothing is trusted un-validated.
    """
    if not raw_guess:
        return None
    guess = raw_guess.strip().lower()
    if guess in _CANONICAL_CROPS:
        return guess
    for word, key in CROP_DICTIONARY.items():
        if word.lower() == guess:
            return key
    return None


# Public names for use outside this module (e.g. routers validating
# farmer-typed crop_key on the manual add-produce / voice-confirm forms —
# same validation Groq's guesses go through, so a typo or unrecognized
# crop can never silently become an unmatched crop_key in the database).
CANONICAL_CROPS = _CANONICAL_CROPS
normalize_crop_key = _normalize_crop_guess

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


_LLM_EXTRACTION_PROMPT = """Extract the crop name, quantity in kg, and price per kg (in rupees) from this farmer's message. The message may be in Hindi, English, or a mix (Hinglish), and may contain typos or informal spelling.

Pick the crop from EXACTLY this list (these are the only crops this system supports right now): {crop_list}

Reply with ONLY a JSON object, no other text, in exactly this shape:
{{"crop_guess": "<one value from the list above, in lowercase, EXACTLY as spelled there — or null if the message doesn't clearly match any of them>", "quantity_kg": <number or null>, "price_per_kg": <number or null>}}

Never invent a crop name that isn't in the list. If you're not confident it's one of these, use null for crop_guess rather than guessing. If a field isn't mentioned, use null for it too.

Message: {transcript}"""


def parse_listing_llm(transcript: str):
    """
    Same return shape as parse_listing(), but extracts the fields by asking
    an LLM (via Groq's OpenAI-compatible API) instead of only matching
    against a fixed dictionary — so it understands Hindi/Hinglish/typos/
    dialect without us having to enumerate every spelling ahead of time.

    IMPORTANT: Groq's answer is a *guess*, never a direct write. Whatever
    crop name it returns gets passed through _normalize_crop_guess(), the
    same validation CROP_DICTIONARY provides everywhere else. If Groq's
    guess doesn't match a crop we actually support, crop_key comes back
    None (with crop_name_raw set to what was said) so the caller can ask
    the farmer to confirm/retry instead of saving an unmatched crop_key
    that no buyer search would ever find.

    Falls back to the dictionary-based parse_listing() if GROQ_API_KEY
    isn't set, the `groq` package isn't installed, or the API call fails
    for any reason (network issue, bad response, etc.) — a farmer's listing
    should never be blocked by this feature being unavailable.
    """
    if not GROQ_ENABLED:
        return parse_listing(transcript)

    try:
        from groq import Groq

        client = Groq()  # reads GROQ_API_KEY from the environment
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{
                "role": "user",
                "content": _LLM_EXTRACTION_PROMPT.format(
                    transcript=transcript,
                    crop_list=", ".join(_CANONICAL_CROPS),
                ),
            }],
            temperature=0,
            max_tokens=400,
            reasoning_effort="low",  # gpt-oss models "think" before answering by
            # default, and that thinking eats into max_tokens — with a short
            # budget that sometimes leaves nothing for the actual JSON reply
            # (empty content -> JSONDecodeError). This is a simple extraction
            # task, not a reasoning task, so keep effort low and give enough
            # headroom for the reply either way.
        )
        raw = response.choices[0].message.content.strip()
        if not raw:
            raise ValueError(
                "Groq returned empty content (likely spent its whole token "
                "budget on internal reasoning) — see reasoning_effort/max_tokens above."
            )
        # Models sometimes wrap JSON in ```json fences despite instructions —
        # strip those before parsing rather than failing on them.
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(raw)

        # Validate Groq's guess against CROP_DICTIONARY — never trust it
        # blindly, even though we asked it to pick from our list. This is
        # what stops an unrecognized/mismatched crop from ever becoming
        # a saved crop_key.
        crop_guess = parsed.get("crop_guess")
        crop_key = _normalize_crop_guess(crop_guess)

        return {
            "crop_key": crop_key,
            "crop_name_raw": crop_guess if crop_guess else None,
            "quantity_kg": parsed.get("quantity_kg"),
            "price_per_kg": parsed.get("price_per_kg"),
            "transcript": transcript,
        }
    except Exception as e:
        # Any failure (missing package, bad API key, malformed JSON back,
        # network error) — fall back to the dictionary parser rather than
        # returning an error to the farmer. Logged so it's visible in the
        # server console during development instead of failing silently.
        print(f"[voice] Groq extraction failed, falling back to dictionary: {e!r}")
        return parse_listing(transcript)



