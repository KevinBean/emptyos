"""Pronounce API — phoneme-level pronunciation analysis on :8603.

Wraps a wav2vec2 phoneme-CTC model (recognition) + a forced aligner
(per-phone start/end timestamps) + g2p (text → reference phones) into a
single HTTP service. Apps call this via the `pronounce` capability and the
`plugins/pronounce/` provider — never directly.

Engine selection:
  - phoneme recognition: facebook/wav2vec2-xlsr-53-espeak-cv-ft (HF)
  - g2p (text → reference phones): cmudict (CMU pronouncing dictionary,
    pure-Python, English-only V1). OOV words fall back to a coarse
    letter→phone rule so the alignment still produces a row per word.
  - alignment: CTC argmax + frame-time decoding from the same wav2vec2 model
    (no charsiu dependency at V1 — keeps the dep footprint small; a
    charsiu provider can be slotted in behind the same return shape later).

Tokenizer note: this model's HuggingFace processor normally requires the
`phonemizer` package + `espeak-ng` system binary for text-to-phone encoding.
We skip the processor entirely — we load only the feature extractor +
read `vocab.json` directly so we can decode the model's softmax output back
into IPA. cmudict handles text-to-phone. The result: no system-level
binary install, fully pip-managed deps.

Endpoints:
  GET  /health           — status + model state (downloading/loading/ready)
  POST /score            — full pipeline: audio + ref text → alignment
  POST /align            — alignment only when caller already has phones

Start:  python services/pronounce/server.py
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request

logger = logging.getLogger("pronounce-api")

app = FastAPI(title="EmptyOS Pronounce API")


# --- Auth middleware (mirrors voice-api shape) -------------------------------
_AUTH_EXEMPT_PATHS = {"/health", "/"}


def _pronounce_token() -> str:
    return os.environ.get("PRONOUNCE_API_TOKEN", "").strip()


@app.middleware("http")
async def _auth_mw(request: Request, call_next):
    token = _pronounce_token()
    if not token:
        return await call_next(request)
    if request.url.path in _AUTH_EXEMPT_PATHS:
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        import hmac as _hmac

        if _hmac.compare_digest(auth[7:].strip(), token):
            return await call_next(request)
    return JSONResponse({"error": "unauthorized"}, status_code=401)


AUDIO_DIR = Path(tempfile.gettempdir()) / "emptyos-pronounce"
AUDIO_DIR.mkdir(exist_ok=True)

MODEL_ID = os.environ.get("PRONOUNCE_MODEL_ID", "facebook/wav2vec2-xlsr-53-espeak-cv-ft")
MODEL_DIR = Path(
    os.environ.get("PRONOUNCE_MODEL_DIR", str(Path.home() / ".cache" / "emptyos" / "pronounce"))
)
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# Model lifecycle state — exposed via /health so the plugin's available()
# can tell "warming up" apart from "broken".
_MODEL_STATE: dict[str, Any] = {
    "status": "idle",  # idle | downloading | loading | ready | error
    "detail": "",
    "device": "cpu",
}
_model = None
_processor = None
_g2p = None
_model_lock = asyncio.Lock()


# --- Model lifecycle ---------------------------------------------------------


async def ensure_model() -> tuple[Any, Any]:
    """Lazy-load wav2vec2 phoneme model. Downloads on first call.

    We deliberately skip `AutoProcessor` and load only the feature extractor +
    vocab.json directly. The full processor would pull in the phoneme tokenizer,
    which calls into the `phonemizer` Python package and the `espeak-ng` system
    binary. We don't need text→phone encoding (cmudict does that); we only need
    audio normalization + index→phone decoding, both of which are trivial to
    do ourselves from the raw artefacts. This is the same pattern the model
    card uses for inference.

    Returns (model, decoder) where `decoder` is a dict with keys:
        feature_extractor — preprocesses waveform → input_values
        id_to_phone        — {int: phone_str}
        pad_id             — int (decoder drops these)
        unk_id, bos_id, eos_id — int (also dropped)
    """
    global _model, _processor
    if _model is not None and _processor is not None:
        return _model, _processor

    async with _model_lock:
        if _model is not None:
            return _model, _processor

        _MODEL_STATE["status"] = "loading"
        _MODEL_STATE["detail"] = f"Loading {MODEL_ID} from {MODEL_DIR}"
        logger.info(_MODEL_STATE["detail"])

        try:
            import json as _json

            import torch
            from huggingface_hub import snapshot_download
            from transformers import AutoModelForCTC, Wav2Vec2FeatureExtractor

            device = "cuda" if torch.cuda.is_available() else "cpu"
            _MODEL_STATE["device"] = device

            # First call: ensures the snapshot is on disk. Subsequent calls are
            # a no-op once cached. Doing this explicitly (vs. relying on
            # from_pretrained's implicit download) lets us locate vocab.json
            # below regardless of how transformers cached it.
            _MODEL_STATE["status"] = "downloading"
            snapshot_path = await asyncio.to_thread(
                snapshot_download,
                repo_id=MODEL_ID,
                cache_dir=str(MODEL_DIR),
            )

            feature_extractor = await asyncio.to_thread(
                Wav2Vec2FeatureExtractor.from_pretrained,
                MODEL_ID,
                cache_dir=str(MODEL_DIR),
            )
            model = await asyncio.to_thread(
                AutoModelForCTC.from_pretrained,
                MODEL_ID,
                cache_dir=str(MODEL_DIR),
            )
            model = model.to(device)
            model.eval()

            vocab_path = Path(snapshot_path) / "vocab.json"
            with open(vocab_path, "r", encoding="utf-8") as f:
                phone_to_id: dict[str, int] = _json.load(f)
            id_to_phone = {v: k for k, v in phone_to_id.items()}

            decoder = {
                "feature_extractor": feature_extractor,
                "id_to_phone": id_to_phone,
                "pad_id": phone_to_id.get("<pad>", 0),
                "unk_id": phone_to_id.get("<unk>", -1),
                "bos_id": phone_to_id.get("<s>", -1),
                "eos_id": phone_to_id.get("</s>", -1),
            }
            _processor = decoder
            _model = model
            _MODEL_STATE["status"] = "ready"
            _MODEL_STATE["detail"] = f"Loaded on {device} (vocab={len(id_to_phone)})"
            logger.info(_MODEL_STATE["detail"])
        except Exception as e:
            _MODEL_STATE["status"] = "error"
            _MODEL_STATE["detail"] = f"{type(e).__name__}: {e}"
            logger.exception("[pronounce] model load failed")
            raise

    return _model, _processor


def ensure_g2p():
    """Lazy-load cmudict. Returns a dict-like with .get(word) → list of variants.

    cmudict ships with the CMU Pronouncing Dictionary baked in (~125k entries),
    so first-call latency is the JSON parse — no network, no model. Each
    variant is a list of ARPABET phones with trailing stress digits
    (AH0, AH1, AH2), stripped at the call site.
    """
    global _g2p
    if _g2p is not None:
        return _g2p
    import cmudict

    _g2p = cmudict.dict()
    return _g2p


# Coarse English letter→phone fallback for OOV words. Single-letter mapping
# only; intentionally crude — it's better to surface a guess than to silently
# drop a word from the alignment. Real g2p for OOV is a Phase-2 enhancement.
_OOV_LETTER_PHONE = {
    "a": "AE", "b": "B", "c": "K", "d": "D", "e": "EH", "f": "F", "g": "G",
    "h": "HH", "i": "IH", "j": "JH", "k": "K", "l": "L", "m": "M", "n": "N",
    "o": "AA", "p": "P", "q": "K", "r": "R", "s": "S", "t": "T", "u": "AH",
    "v": "V", "w": "W", "x": "K", "y": "IY", "z": "Z",
}


def _oov_phones(word: str) -> list[str]:
    """Best-effort letter-by-letter phones for an OOV word."""
    return [_OOV_LETTER_PHONE[c] for c in word.lower() if c in _OOV_LETTER_PHONE]


# --- Audio helpers -----------------------------------------------------------


def _decode_audio_b64(audio_b64: str) -> Path:
    """Write a b64-decoded audio blob to a temp file. Returns the path."""
    raw = base64.b64decode(audio_b64)
    suffix = ".wav"
    path = AUDIO_DIR / f"in_{uuid.uuid4().hex[:10]}{suffix}"
    path.write_bytes(raw)
    return path


def _load_waveform(path: Path, target_sr: int = 16000):
    """Load audio → mono float32 numpy array at target_sr.

    Uses librosa to handle any input format soundfile/ffmpeg-compatible
    (wav/flac/ogg/webm/mp3 depending on the user's codec install).
    """
    import librosa

    wav, sr = librosa.load(str(path), sr=target_sr, mono=True)
    return wav, sr


# --- G2P / phone normalisation ----------------------------------------------


# wav2vec2-xlsr-53-espeak emits eSpeak-style IPA. g2p_en emits ARPABET.
# For V1 we keep two parallel phone sequences (one IPA from recognition,
# one ARPABET from g2p_en) and align via a coarse ARPABET↔IPA bridge so
# substitutions like "TH"→"D" surface correctly. This is intentionally
# light — a richer phonetic distance matrix is a Phase-3 enhancement.

# Primary IPA glyph the UI displays alongside each ARPABET phone. Kept short —
# matches the most common monophonic form found in modern English dictionaries.
ARPABET_TO_IPA: dict[str, str] = {
    "AA": "ɑ", "AE": "æ", "AH": "ʌ", "AO": "ɔ", "AW": "aʊ",
    "AY": "aɪ", "B": "b", "CH": "tʃ", "D": "d", "DH": "ð",
    "EH": "ɛ", "ER": "ɝ", "EY": "eɪ", "F": "f", "G": "ɡ",
    "HH": "h", "IH": "ɪ", "IY": "i", "JH": "dʒ", "K": "k",
    "L": "l", "M": "m", "N": "n", "NG": "ŋ", "OW": "oʊ",
    "OY": "ɔɪ", "P": "p", "R": "ɹ", "S": "s", "SH": "ʃ",
    "T": "t", "TH": "θ", "UH": "ʊ", "UW": "u", "V": "v",
    "W": "w", "Y": "j", "Z": "z", "ZH": "ʒ",
}

# All IPA glyphs the eSpeak-trained model may emit that are considered the
# same phone as a given ARPABET label. The model commonly emits length-marked
# vowels (ɑː, iː, uː), rhotic-coloured schwas (ɚ vs ɝ), and the unstressed
# schwa "ə" where ARPABET writes AH0. Without these equivalences the
# alignment scores false substitutions on every long vowel. Order matters
# only for display, not matching.
ARPABET_IPA_ALIASES: dict[str, set[str]] = {
    "AA": {"ɑ", "ɑː", "a"},
    "AE": {"æ", "a"},
    "AH": {"ʌ", "ə", "ɐ"},  # ARPABET AH covers stressed ʌ, unstressed ə, and the near-open central vowel ɐ that eSpeak commonly emits for the same sound
    "AO": {"ɔ", "ɔː", "oː"},
    "AW": {"aʊ", "aw"},
    "AY": {"aɪ", "ai"},
    "EH": {"ɛ", "e"},
    "ER": {"ɝ", "ɚ", "ɹ̩"},  # rhotic-coloured schwa, both stressed + unstressed
    "EY": {"eɪ", "e", "ej"},
    "IH": {"ɪ", "i"},
    "IY": {"i", "iː"},
    "OW": {"oʊ", "o", "oː", "əʊ"},
    "OY": {"ɔɪ"},
    "UH": {"ʊ", "u"},
    "UW": {"u", "uː"},
    "B": {"b"},
    "CH": {"tʃ", "ʧ"},
    "D": {"d", "ɾ"},  # flap allophone
    "DH": {"ð"},
    "F": {"f"},
    "G": {"ɡ", "g"},
    "HH": {"h"},
    "JH": {"dʒ", "ʤ"},
    "K": {"k"},
    "L": {"l", "ɫ"},
    "M": {"m"},
    "N": {"n"},
    "NG": {"ŋ"},
    "P": {"p"},
    "R": {"ɹ", "r", "ɻ"},
    "S": {"s"},
    "SH": {"ʃ"},
    "T": {"t", "ɾ"},  # flap allophone shared with D
    "TH": {"θ"},
    "V": {"v"},
    "W": {"w"},
    "Y": {"j"},
    "Z": {"z"},
    "ZH": {"ʒ"},
}


def _ipa_to_arpa(ipa: str) -> str:
    """Reverse lookup: IPA glyph → ARPABET label.

    Strips eSpeak primary/secondary stress marks (ˈ, ˌ) and matches the first
    ARPABET label whose alias set contains the cleaned glyph. Falls back to
    the upper-cased glyph so unknown phones still appear in the alignment
    (rather than being silently dropped) — the UI surfaces them as `?`.
    """
    # eSpeak emits stress + length on the same token sometimes; normalize.
    cleaned = ipa.replace("ˈ", "").replace("ˌ", "")
    for arpa, aliases in ARPABET_IPA_ALIASES.items():
        if cleaned in aliases:
            return arpa
    return cleaned.upper()


def _strip_arpabet_stress(phone: str) -> str:
    """ARPABET phones carry trailing stress digits (AH0, AH1, AH2)."""
    return "".join(c for c in phone if not c.isdigit())


def text_to_reference_phones(text: str) -> list[dict]:
    """Convert English text to reference phones via cmudict.

    Returns one entry per word: {word, phones_arpabet, phones_ipa, oov}.
    Punctuation is stripped from words before lookup. OOV words use a
    coarse letter-by-letter fallback (oov=True) so the alignment still
    has a row per word.
    """
    d = ensure_g2p()
    words: list[dict] = []
    for raw in text.split():
        # Strip surrounding punctuation; keep internal apostrophes intact
        # (cmudict has entries for "don't", "you're", etc.).
        token = raw.strip(".,!?;:\"()[]{}").lower()
        if not token:
            continue
        variants = d.get(token)
        oov = False
        if variants:
            phones_raw = variants[0]  # first/most-common variant
        else:
            phones_raw = _oov_phones(token)
            oov = True
        arpa = [_strip_arpabet_stress(p) for p in phones_raw if p]
        if not arpa:
            # Even the OOV fallback produced nothing (e.g. digits-only token).
            # Skip rather than emit an empty word row that would confuse alignment.
            continue
        ipa = [ARPABET_TO_IPA.get(p, p.lower()) for p in arpa]
        words.append({
            "word": raw,
            "phones_arpabet": arpa,
            "phones_ipa": ipa,
            "oov": oov,
        })
    return words


# --- Phoneme recognition + alignment ----------------------------------------


async def recognize_phones(wav) -> list[dict]:
    """Run wav2vec2 phoneme CTC; return per-frame (phone, start, end, confidence).

    Uses CTC argmax over the model's vocabulary. Adjacent identical predictions
    are merged into a single phone span. Special tokens (`<pad>`, `<unk>`,
    `<s>`, `</s>`) are dropped.
    """
    model, decoder = await ensure_model()

    import torch

    feature_extractor = decoder["feature_extractor"]
    id_to_phone: dict[int, str] = decoder["id_to_phone"]
    drop_ids = {decoder["pad_id"], decoder["unk_id"], decoder["bos_id"], decoder["eos_id"]}

    def _infer():
        inputs = feature_extractor(wav, sampling_rate=16000, return_tensors="pt")
        device = next(model.parameters()).device
        input_values = inputs["input_values"].to(device)
        with torch.no_grad():
            logits = model(input_values).logits[0]  # (T, V)
        probs = torch.softmax(logits, dim=-1)
        confs, preds = probs.max(dim=-1)
        return preds.cpu().tolist(), confs.cpu().tolist()

    preds, confs = await asyncio.to_thread(_infer)

    # Wav2vec2 strides 320 samples/frame at 16kHz = 20ms/frame.
    FRAME_SECONDS = 320 / 16000.0

    spans: list[dict] = []
    cur_id = None
    cur_start_frame = 0
    cur_confs: list[float] = []

    def _flush(end_frame: int):
        if cur_id is None or cur_id in drop_ids:
            return
        phone = id_to_phone.get(cur_id, "")
        if not phone or phone.startswith("<"):
            return
        spans.append({
            "phone": phone,
            "start": round(cur_start_frame * FRAME_SECONDS, 3),
            "end": round(end_frame * FRAME_SECONDS, 3),
            "confidence": round(sum(cur_confs) / max(len(cur_confs), 1), 3),
        })

    for i, pid in enumerate(preds):
        if pid == cur_id:
            cur_confs.append(confs[i])
            continue
        _flush(i)
        cur_id = pid
        cur_start_frame = i
        cur_confs = [confs[i]]
    _flush(len(preds))

    return spans


def _align_phones(ref_phones: list[str], hyp_phones: list[dict]) -> list[dict]:
    """Needleman-Wunsch-ish alignment of reference phones to hypothesised phone spans.

    Returns one row per reference phone slot:
      {ref, hyp, op: "match"|"sub"|"del", start, end, confidence}
    Plus trailing rows for any unconsumed hypothesised phones:
      {ref: None, hyp, op: "ins", start, end, confidence}
    """
    if not ref_phones:
        return [{"ref": None, "hyp": h["phone"], "op": "ins",
                 "start": h["start"], "end": h["end"], "confidence": h["confidence"]}
                for h in hyp_phones]

    R, H = len(ref_phones), len(hyp_phones)
    # dp[i][j] = (cost, backptr) — backptr in {"M","S","I","D"}
    dp: list[list[tuple[float, str]]] = [[(0.0, "M") for _ in range(H + 1)] for _ in range(R + 1)]
    for i in range(1, R + 1):
        dp[i][0] = (i * 1.0, "D")
    for j in range(1, H + 1):
        dp[0][j] = (j * 1.0, "I")
    for i in range(1, R + 1):
        for j in range(1, H + 1):
            same = ref_phones[i - 1].lower() == hyp_phones[j - 1]["phone"].lower()
            mcost = dp[i - 1][j - 1][0] + (0.0 if same else 1.0)
            dcost = dp[i - 1][j][0] + 1.0
            icost = dp[i][j - 1][0] + 1.0
            best = min((mcost, "M" if same else "S"), (dcost, "D"), (icost, "I"),
                       key=lambda x: x[0])
            dp[i][j] = best

    # Backtrace
    rows: list[dict] = []
    i, j = R, H
    while i > 0 or j > 0:
        op = dp[i][j][1] if i > 0 and j > 0 else ("D" if j == 0 else "I")
        if op in ("M", "S"):
            h = hyp_phones[j - 1]
            rows.append({
                "ref": ref_phones[i - 1],
                "hyp": h["phone"],
                "op": "match" if op == "M" else "sub",
                "start": h["start"],
                "end": h["end"],
                "confidence": h["confidence"],
            })
            i -= 1
            j -= 1
        elif op == "D":
            rows.append({
                "ref": ref_phones[i - 1],
                "hyp": None,
                "op": "del",
                "start": None,
                "end": None,
                "confidence": 0.0,
            })
            i -= 1
        else:  # "I"
            h = hyp_phones[j - 1]
            rows.append({
                "ref": None,
                "hyp": h["phone"],
                "op": "ins",
                "start": h["start"],
                "end": h["end"],
                "confidence": h["confidence"],
            })
            j -= 1
    rows.reverse()
    return rows


def _summarize(alignment: list[dict], reference_words: list[dict]) -> dict:
    """Roll up alignment rows into phone-accuracy + weak-phones."""
    total = sum(1 for r in alignment if r["op"] in ("match", "sub", "del"))
    matched = sum(1 for r in alignment if r["op"] == "match")
    phone_accuracy = round(matched / total, 3) if total else 0.0

    # Weak phones = reference phones that were substituted or deleted, top N
    from collections import Counter

    miss_counter: Counter = Counter()
    for r in alignment:
        if r["op"] in ("sub", "del") and r.get("ref"):
            miss_counter[r["ref"]] += 1
    weak = [p for p, _c in miss_counter.most_common(5)]

    duration_s = 0.0
    ends = [r["end"] for r in alignment if r.get("end") is not None]
    if ends:
        duration_s = round(max(ends), 2)

    word_count = len(reference_words)
    return {
        "phone_accuracy": phone_accuracy,
        "weak_phones": weak,
        "duration_s": duration_s,
        "word_count": word_count,
        "phones_total": total,
        "phones_matched": matched,
    }


# --- Endpoints ---------------------------------------------------------------


@app.post("/warmup")
async def warmup():
    """Kick off model load in the background. Returns immediately with the
    current state so the plugin can probe progress via /health.

    Calling /warmup repeatedly is harmless — `ensure_model` is idempotent
    once the model is loaded, and the inner asyncio.Lock serialises concurrent
    callers so a second /warmup during download just waits for the first.
    """
    async def _run():
        try:
            await ensure_model()
        except Exception:
            # ensure_model already mutated _MODEL_STATE to "error"; nothing
            # more to do here.
            pass
    asyncio.create_task(_run())
    return {"started": True, "state": _MODEL_STATE["status"], "detail": _MODEL_STATE["detail"]}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "model_id": MODEL_ID,
        "model_dir": str(MODEL_DIR),
        "model_state": _MODEL_STATE["status"],
        "model_detail": _MODEL_STATE["detail"],
        "device": _MODEL_STATE["device"],
        "ready": _MODEL_STATE["status"] == "ready",
    }


@app.post("/score")
async def score(request: Request):
    """Full pipeline: audio + reference text → per-phone alignment + summary.

    Request body:
        {audio_b64, reference_text, language="en-us"}

    Returns the JSON shape documented in the plan file.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    audio_b64 = body.get("audio_b64", "")
    reference_text = (body.get("reference_text") or "").strip()
    language = body.get("language", "en-us")
    if not audio_b64 or not reference_text:
        return JSONResponse({"error": "audio_b64 and reference_text required"}, status_code=400)

    tmp_path = _decode_audio_b64(audio_b64)
    try:
        wav, _sr = _load_waveform(tmp_path)
        ref_words = text_to_reference_phones(reference_text)
        # Flatten ARPABET reference for alignment (model emits IPA; we still
        # report ARPABET in the output so UIs and downstream apps see a stable
        # phone set across the pipeline).
        ref_arpa: list[str] = []
        for w in ref_words:
            ref_arpa.extend(w["phones_arpabet"])

        hyp_spans = await recognize_phones(wav)

        # Translate hyp IPA spans into ARPABET via the equivalence-class map
        # so length-marked vowels (ɑː) and unstressed schwas (ə) don't score
        # as false substitutions against their ARPABET base form.
        for span in hyp_spans:
            span["phone_arpa"] = _ipa_to_arpa(span["phone"])

        hyp_arpa_spans = [{**s, "phone": s["phone_arpa"]} for s in hyp_spans]
        alignment = _align_phones(ref_arpa, hyp_arpa_spans)

        # Build word_alignment by stepping through ref_words and consuming
        # alignment rows that target each word's phone slice.
        word_alignment: list[dict] = []
        alignment_iter = iter(alignment)
        leftover: list[dict] = []
        for w in ref_words:
            wphones = w["phones_arpabet"]
            consumed: list[dict] = []
            need = len(wphones)
            # First grab any pending insertions (ref=None) that landed before
            # this word's first ref phone.
            for row in alignment_iter:
                if row["ref"] is None:
                    consumed.append(row)
                    continue
                consumed.append(row)
                need -= 1
                if need <= 0:
                    break
            if not consumed and leftover:
                consumed.extend(leftover)
                leftover = []
            # Compute word-level score: fraction of matches over reference len.
            matches = sum(1 for r in consumed if r["op"] == "match")
            starts = [r["start"] for r in consumed if r.get("start") is not None]
            ends = [r["end"] for r in consumed if r.get("end") is not None]
            word_alignment.append({
                "word": w["word"],
                "phones": wphones,
                "start": min(starts) if starts else None,
                "end": max(ends) if ends else None,
                "score": round(matches / max(len(wphones), 1), 3),
                "phones_alignment": consumed,
            })

        return {
            "transcript_phones": [s["phone_arpa"] for s in hyp_spans],
            "transcript_phones_ipa": [s["phone"] for s in hyp_spans],
            "reference_phones": ref_arpa,
            "reference_words": ref_words,
            "alignment": alignment,
            "word_alignment": word_alignment,
            "summary": _summarize(alignment, ref_words),
            "model_version": f"{MODEL_ID}@1.0",
            "device": _MODEL_STATE["device"],
            "language": language,
        }
    except Exception as e:
        logger.exception("[pronounce] /score failed")
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


@app.post("/align")
async def align(request: Request):
    """Alignment-only when caller already has reference phones.

    Request body:
        {audio_b64, reference_phones: ["DH","AH","K", ...]}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    audio_b64 = body.get("audio_b64", "")
    ref_phones = body.get("reference_phones") or []
    if not audio_b64 or not ref_phones:
        return JSONResponse(
            {"error": "audio_b64 and reference_phones required"}, status_code=400
        )

    tmp_path = _decode_audio_b64(audio_b64)
    try:
        wav, _sr = _load_waveform(tmp_path)
        hyp_spans = await recognize_phones(wav)
        for span in hyp_spans:
            span["phone"] = _ipa_to_arpa(span["phone"])
        alignment = _align_phones([_strip_arpabet_stress(p) for p in ref_phones], hyp_spans)
        return {
            "alignment": alignment,
            "summary": _summarize(alignment, []),
            "model_version": f"{MODEL_ID}@1.0",
        }
    except Exception as e:
        logger.exception("[pronounce] /align failed")
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    port = int(os.environ.get("PRONOUNCE_API_PORT", "8603"))
    host = os.environ.get("PRONOUNCE_API_HOST", "127.0.0.1")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    auth_state = "auth=on" if _pronounce_token() else "auth=off"
    print(f"[Pronounce API] {host}:{port} ({auth_state}) | Model={MODEL_ID}")
    print(f"[Pronounce API] Cache: {MODEL_DIR}")
    if host != "127.0.0.1" and not _pronounce_token():
        print(
            "[Pronounce API] WARNING: bound non-loopback without PRONOUNCE_API_TOKEN — "
            "anyone on the network can submit audio for scoring."
        )
    uvicorn.run(app, host=host, port=port, log_level="warning")
