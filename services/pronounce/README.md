# Pronounce API

Phoneme-level pronunciation scoring on port `8603`. Powers the `pronounce`
capability in EmptyOS via `plugins/pronounce/`.

## What it does

`POST /score` takes an audio clip plus the reference text the learner was
trying to say, and returns:

- The phones the learner actually produced (IPA + ARPABET).
- The phones they should have produced.
- A per-phone alignment marking each ref phone as `match`, `sub`, or `del`,
  with start/end timestamps and confidence.
- Per-word scores and a roll-up summary with the top weak phones.

## Engines (V1)

| Stage | Tool | Notes |
|---|---|---|
| Phoneme recognition | `wav2vec2-xlsr-53-espeak-cv-ft` via HuggingFace | ~1.2 GB; CPU works, CUDA is autodetected |
| Text → reference phones | `cmudict` | English-only V1; OOV words use letter-by-letter fallback |
| Alignment | CTC argmax + Needleman-Wunsch | No charsiu/Kaldi dependency |

A drop-in charsiu provider can be added behind the same return shape later.

**No espeak-ng required.** The model's HuggingFace tokenizer normally requires
the `phonemizer` Python package + the `espeak-ng` system binary for text→phone
encoding. We deliberately bypass the tokenizer entirely — we load only the
feature extractor + read `vocab.json` directly so we can decode the model's
softmax output back into IPA. cmudict handles the text-to-phone direction. The
result: no system-level binary install, fully pip-managed.

## Run standalone

```bash
cd services/pronounce
pip install -r requirements.txt
python server.py
```

The model downloads on first request to `~/.cache/emptyos/pronounce/` (override
with `PRONOUNCE_MODEL_DIR`). `GET /health` reports `model_state` so a caller
can show "warming up" while the first download is in flight.

## Run via Docker

```bash
docker build -t emptyos-pronounce .
docker run -p 8603:8603 -v pronounce-models:/var/lib/pronounce-models emptyos-pronounce
```

## Environment

| Var | Default | Purpose |
|---|---|---|
| `PRONOUNCE_API_HOST` | `127.0.0.1` | Bind address |
| `PRONOUNCE_API_PORT` | `8603` | Port |
| `PRONOUNCE_API_TOKEN` | empty | Bearer token; required when host is non-loopback |
| `PRONOUNCE_MODEL_ID` | `facebook/wav2vec2-xlsr-53-espeak-cv-ft` | Override to swap models |
| `PRONOUNCE_MODEL_DIR` | `~/.cache/emptyos/pronounce` | Where the model weights live |

## API

### `GET /health`

```json
{
  "status": "ok",
  "model_id": "facebook/wav2vec2-xlsr-53-espeak-cv-ft",
  "model_state": "ready",
  "device": "cuda",
  "ready": true
}
```

### `POST /score`

Request:
```json
{"audio_b64": "...", "reference_text": "the quick brown fox", "language": "en-us"}
```

Response: see the plan file or `server.py` for the full shape. The key
fields per row in `alignment` are `ref`, `hyp`, `op` (`match`/`sub`/`del`/`ins`),
`start`, `end`, `confidence`.

### `POST /align`

Same shape minus g2p, when the caller already has reference phones:
```json
{"audio_b64": "...", "reference_phones": ["DH","AH","K","W","IH","K"]}
```

## Air-gapped install

The model can be placed manually:

```bash
# On a connected machine
huggingface-cli download facebook/wav2vec2-xlsr-53-espeak-cv-ft \
  --local-dir ~/.cache/emptyos/pronounce/models--facebook--wav2vec2-xlsr-53-espeak-cv-ft

# rsync that directory to the offline machine
```

## Smoke test

```bash
python server.py &
curl http://localhost:8603/health
python -c "import base64, json, urllib.request; \
  audio = open('test.wav','rb').read(); \
  body = json.dumps({'audio_b64': base64.b64encode(audio).decode(), 'reference_text': 'the quick brown fox'}).encode(); \
  r = urllib.request.urlopen('http://localhost:8603/score', body); \
  print(r.read().decode())"
```
