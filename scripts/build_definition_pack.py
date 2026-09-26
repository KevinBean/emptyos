"""Build a dictionary definition pack: the shipped definitions `lookup()` reads
before asking a model (apps/extension/english-learning/dictionary/definition_pack.py).

Pipeline, resumable at every step:

  1. headwords — the N most frequent English words (wordfreq), kept only when a
     pack could serve them (see `select_headwords`) and when a real word list
     has them (``--dictionary``, see `load_word_list`);
  2. generate  — each word through the same static request a live lookup sends
     (system prompt, user prompt, temperature, read from the dictionary's
     shared.py). A live answer can still differ: the daemon routes it through
     its provider chain and may add language localization;
  3. record    — every result appended to ``definitions.jsonl`` as it arrives
     (accepted entry, or the refusal with the raw reply for review). A rerun
     skips recorded words, so an interrupted run does not pay for them again;
     a call that failed is not recorded and is retried;
  4. finalize  — ``definitions.sqlite`` built from the accepted lines through
     ``write_pack``. The JSONL stays beside it as the human-readable copy to
     review before a pack ships.

Entries are refused when they cannot be parsed, have no definition, or are
tagged a proper noun: wordfreq lists names in lower case ("vader", "hubbell"),
and a learner typing those gets the model instead. ``--retry-refused`` sends
refused words again (useful after a model change or a one-off bad reply).

Spend: the script calls the provider directly, so the daemon's cloud-consent
gate and billing do not see these calls. In their place: a cloud provider makes
no call without ``--yes``; ``--dry-run`` calls nothing; ``--limit N`` builds a
sample first; the run prints the cost the provider reported; and it stops
after ``MAX_CONSECUTIVE_FAILURES`` failures in a row (a missing key or empty
credit would otherwise fail every word).

    python scripts/build_definition_pack.py --out build/definition-pack --dictionary <scowl>/final --dry-run
    python scripts/build_definition_pack.py --out build/definition-pack --dictionary <scowl>/final --limit 200 --yes
    python scripts/build_definition_pack.py --out build/definition-pack --finalize-only

The word list: wordfreq ranks words by how often they appear on the web, so its
tail is full of surnames, brands, abbreviations and noise, and the model invents
a meaning for a string it does not know ("pham" became slang for friends). A
sample of 200 from ranks 5k-50k held about 10 such entries. ``--dictionary``
keeps only words in SCOWL (http://wordlist.aspell.net/, permissive licence, see
its Copyright file) at size <= ``WORD_LIST_MAX_LEVEL``: its ``words`` and
``contractions`` lists, never ``proper-names``, ``upper`` or ``abbreviations``.
The list only chooses headwords; nothing from it is stored in the pack. Get
scowl-2020.12.07.zip from SourceForge and pass its ``final`` directory; the
archive used for the 2026-09-26 measurements had sha256
dc3435e1cb56f3394aea91b5d2ab5d10d80c98bc7dd88c3fccb7348f6ab913a0 (computed
on download). ``--no-dictionary`` builds unfiltered, on purpose.

Exit codes: 0 done, 1 some words failed (rerun to retry), 2 a cloud call
needs --yes, 3 the provider is unavailable, 4 a configuration error. The provider is built from
``emptyos.toml`` ``[capabilities.think.<name>]`` by the daemon's own factory;
this imports kernel modules but creates no kernel and opens no database.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Awaitable, Callable

ROOT = Path(__file__).resolve().parent.parent
DICT_DIR = ROOT / "apps" / "extension" / "english-learning" / "dictionary"
JSONL = "definitions.jsonl"
PACK = "definitions.sqlite"
HEADWORD_SOURCE = "wordfreq top_n_list('en')"
# Not from a source: enough to ride out a burst of rate limits, few enough that
# a missing key or empty credit stops before it has fired many requests.
MAX_CONSECUTIVE_FAILURES = 20
# Not from a source: the raw reply kept on a refused line, for review.
REPLY_EXCERPT = 300

# SCOWL's size levels run 10 (commonest) to 95. 70 is "large" — still real
# words. Measured 2026-09-26, possessives excluded: at <= 70 the 50,000th
# eligible wordfreq word is "antiphon", so a 50k pack fills; 80 adds obscure
# forms, 60 drops some ordinary ones.
WORD_LIST_MAX_LEVEL = 70
WORD_LIST_KINDS = ("words", "contractions")
# With the list on, eligible words run deeper into wordfreq's ranking: the
# 50,000th sat at rank 90,143 (measured with the constants above). 6x fetches
# 300,000, room for a later list or level that filters harder.
FILTERED_OVERFETCH = 6
# The "'s" contractions (is/has) a learner looks up. Any other word ending in
# "'s" is a possessive ("window's"): 10 of 200 sampled headwords, each a slot a
# learner would not search. SCOWL cannot tell the two apart — it files it's
# and window's together in its words lists, not in contractions — so the
# contractions are named here.
S_CONTRACTIONS = frozenset({
    "it's", "that's", "let's", "he's", "she's", "there's", "here's", "what's",
    "who's", "where's", "when's", "why's", "how's",
})
# A backstop for a name or brand the model tagged "noun" despite the prompt: a
# definition that IS a name label — "A French surname.", "A surname of English
# origin.", "A brand of footwear." Up to two words may qualify the label. Not
# matched: a definition that goes on to explain the label ("A surname is the
# name shared by a family"), and the name words themselves (`_NAME_TERMS`).
# Still refused, knowingly: a genericised trademark defined as "A brand of…"
# (velcro) — it falls through to the live model, like any other refusal.
_NAME_DEFINITION = re.compile(
    r"^\s*(?:an?|the)\s+(?:[\w'-]+\s+){0,2}?"
    r"(?:surname|given name|family name|first name|last name|forename|brand|trademark)"
    r"\s*(?:$|[.,;]|of\b|from\b|used\b|common\b|found\b)", re.IGNORECASE)
_NAME_TERMS = frozenset({
    "surname", "surnames", "forename", "forenames", "name", "names", "nickname",
    "brand", "brands", "trademark", "trademarks",
})

Generate = Callable[[str], Awaitable[str]]
EXIT_CONFIG = 4


def load_word_list(final_dir: Path, max_level: int = WORD_LIST_MAX_LEVEL) -> set[str]:
    """The SCOWL words a pack may serve: every ``<variant>-<kind>.<level>`` file
    in ``final_dir`` whose kind is a word or contraction list and whose level is
    at most ``max_level``. Proper names, upper-case words and abbreviations live
    in their own files and are never read."""
    words: set[str] = set()
    for path in sorted(Path(final_dir).glob("*-*.*")):
        stem, _, level = path.name.rpartition(".")
        kind = stem.rpartition("-")[2]
        if kind not in WORD_LIST_KINDS or not level.isdigit() or int(level) > max_level:
            continue
        # SCOWL ships ISO-8859-1.
        words.update(w.strip() for w in path.read_text(encoding="latin-1").splitlines() if w.strip())
    return words


def _config_error(message: str):
    """A setup problem, distinct from exit 1 ("some words failed, rerun")."""
    print(message, file=sys.stderr)
    raise SystemExit(EXIT_CONFIG)


def _dictionary_modules():
    """definition_pack (loaded by path) + the lookup request constants (read,
    not imported — the dictionary package's relative imports need the app
    loader)."""
    if not DICT_DIR.is_dir():
        _config_error(f"dictionary app not found at {DICT_DIR}")
    # By path: putting the app folder on sys.path would let its generic module
    # names (shared, prompts, app) shadow other imports in the process.
    spec = importlib.util.spec_from_file_location(
        "eos_definition_pack", DICT_DIR / "definition_pack.py")
    definition_pack = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(definition_pack)  # pure module, no relative imports

    wanted = {"DICTIONARY_LOOKUP_SYSTEM", "DICTIONARY_LOOKUP_USER", "DICTIONARY_LOOKUP_TEMPERATURE"}
    request: dict = {}
    for node in ast.parse((DICT_DIR / "shared.py").read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in wanted:
                request[node.targets[0].id] = ast.literal_eval(node.value)
    missing = wanted - set(request)
    if missing:
        _config_error(f"shared.py no longer defines {sorted(missing)}")
    return definition_pack, request


def build_request(word: str, request: dict) -> dict:
    """The provider kwargs for `word` — the same static request lookup() sends."""
    return {
        "prompt": request["DICTIONARY_LOOKUP_USER"].format(word=word),
        "system": request["DICTIONARY_LOOKUP_SYSTEM"],
        "temperature": request["DICTIONARY_LOOKUP_TEMPERATURE"],
    }


def select_headwords(candidates: list[str], n: int, normalize,
                     allowed: set[str] | None = None) -> list[str]:
    """The first `n` candidates a pack could serve, deduplicated.

    Dropped: anything that is not already a valid pack key; single letters
    other than "a"; the pronoun "i" with its contractions ("i'm", "i've"),
    which are always written capitalised and so could never be served;
    possessives ("window's", see `S_CONTRACTIONS`); and, when `allowed` is
    given, anything not in that word list.
    """
    out: list[str] = []
    seen: set[str] = set()
    for word in candidates:
        if normalize(word) != word or word in seen:
            continue
        if allowed is not None and word not in allowed:
            continue
        if (len(word) == 1 and word != "a") or word.startswith("i'"):
            continue
        if word.endswith("'s") and word not in S_CONTRACTIONS:
            continue
        seen.add(word)
        out.append(word)
        if len(out) >= n:
            break
    return out


def read_progress(path: Path) -> dict[str, dict]:
    """``{word: line}`` for every word already recorded; later lines win."""
    done: dict[str, dict] = {}
    if not path.exists():
        return done
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            line = json.loads(raw)
        except ValueError:
            continue  # a line torn by a crash mid-write; the word is retried
        if isinstance(line, dict) and isinstance(line.get("word"), str):
            done[line["word"]] = line
    return done


def pending(words: list[str], done: dict[str, dict], retry_refused: bool) -> list[str]:
    """The words still to generate: not recorded, or recorded as refused when
    ``retry_refused``. The one place this rule lives."""
    if retry_refused:
        done = {w: line for w, line in done.items() if line.get("entry")}
    return [w for w in words if w not in done]


def judge(word: str, reply: str, clean_entry, parse, normalize=None) -> tuple[dict | None, str]:
    """(entry, "") when usable, else (None, reason)."""
    try:
        raw = parse(reply)
    except Exception as e:  # parse_llm_json raises on non-JSON
        return None, f"unparseable: {type(e).__name__}"
    # An answer about another word ("will" for "ll") would be stored under
    # this headword; refuse it. A reply that names no word is let through.
    said = raw.get("word") if isinstance(raw, dict) else None
    if normalize and isinstance(said, str) and said.strip() and normalize(said) != word:
        return None, f"answered another word: {said[:40]!r}"
    entry = clean_entry(raw, word)
    if entry is None:
        return None, "no definition"
    reason = entry_refusal(word, entry)
    return (None, reason) if reason else (entry, "")


def entry_refusal(word: str, entry: dict) -> str:
    """Why a cleaned entry may not ship, or "" when it may. Shared by `judge`
    and `finalize`, so a line accepted under older rules is checked again."""
    pos = entry.get("part_of_speech", "")
    if "proper" in pos.lower():
        return "proper noun"
    # The prompt asks for an empty part of speech when the model does not know
    # the word; its definition then says so ("Unknown word; not found…").
    if not pos.strip():
        return "not recognised"
    if word not in _NAME_TERMS and _NAME_DEFINITION.match(entry.get("definition", "")):
        return "name or brand"
    return ""


def _open_for_append(path: Path):
    """Append handle that starts on a fresh line.

    A crash mid-write leaves a last line with no newline; appending straight
    onto it would fuse the next record into the torn one and lose both.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_newline = False
    if path.exists() and path.stat().st_size:
        with path.open("rb") as fh:
            fh.seek(-1, 2)
            needs_newline = fh.read(1) != b"\n"
    handle = path.open("a", encoding="utf-8")
    if needs_newline:
        handle.write("\n")
    return handle


async def generate_all(
    words: list[str], out_path: Path, generate: Generate, *,
    clean_entry, parse, normalize=None, model: str = "", concurrency: int = 8,
    attempts: int = 2, backoff_s: float = 1.0, retry_refused: bool = False,
    cost_of_last_call: Callable[[], float] = lambda: 0.0,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Generate every word still pending in `out_path`, appending one line each."""
    todo = pending(words, read_progress(out_path), retry_refused)
    counts = {"skipped": len(words) - len(todo), "accepted": 0, "refused": 0,
              "failed": 0, "stopped": False, "cost_usd": 0.0}
    state = {"consecutive_failures": 0}
    sem = asyncio.Semaphore(max(1, concurrency))

    with _open_for_append(out_path) as fh:
        async def one(word: str):
            async with sem:
                if counts["stopped"]:
                    return
                reply, error = None, ""
                for attempt in range(attempts):
                    try:
                        reply = await generate(word)
                        counts["cost_usd"] += cost_of_last_call()
                        break
                    except Exception as e:
                        error = f"{type(e).__name__}: {e}"[:200]
                        if attempt + 1 < attempts:
                            await asyncio.sleep(backoff_s * 2 ** attempt)
                if reply is None:
                    # Not written: a failed call is retried on the next run.
                    counts["failed"] += 1
                    state["consecutive_failures"] += 1
                    print(f"  failed {word!r}: {error}", file=sys.stderr, flush=True)
                    if state["consecutive_failures"] >= MAX_CONSECUTIVE_FAILURES:
                        counts["stopped"] = True
                    return
                state["consecutive_failures"] = 0
            entry, reason = judge(word, reply, clean_entry, parse, normalize)
            if entry:
                line = {"word": word, "entry": entry}
            else:
                line = {"word": word, "refused": reason, "reply": str(reply)[:REPLY_EXCERPT]}
            line["model"] = model
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            fh.flush()
            counts["accepted" if entry else "refused"] += 1
            if on_progress:
                on_progress(counts["accepted"] + counts["refused"] + counts["failed"], len(todo))

        await asyncio.gather(*(one(w) for w in todo))
    return counts


def finalize(out_dir: Path, write_pack, meta: dict, keep: set[str] | None = None) -> int:
    """Build the SQLite pack from the accepted JSONL lines; returns rows stored.

    Only headwords in `keep` (this run's selection) are stored, and each entry
    passes `entry_refusal` again — so a directory holding lines from an
    unfiltered or older run cannot ship them under this run's filter.
    `model` is the one most entries came from; when a pack mixes models,
    `models` records the count per model.
    """
    lines = read_progress(out_dir / JSONL)
    accepted = [line for word, line in lines.items()
                if line.get("entry") and (keep is None or word in keep)
                and not entry_refusal(word, line["entry"])]
    models = Counter(line.get("model") for line in accepted if line.get("model"))
    if models:
        meta = {**meta, "model": models.most_common(1)[0][0]}
        if len(models) > 1:
            meta["models"] = dict(models)
    return write_pack(out_dir / PACK, {line["word"]: line["entry"] for line in accepted}, meta)


def _provider(name: str):
    from emptyos.capabilities.setup import _build_think_provider_raw  # noqa: PLC0415
    from emptyos.kernel.config import Config  # noqa: PLC0415 — parses toml; no kernel

    provider = _build_think_provider_raw(name, Config(str(ROOT / "emptyos.toml")))
    if provider is None:
        _config_error(f"no [capabilities.think.{name}] section in emptyos.toml")
    return provider


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, type=Path, help="output directory")
    ap.add_argument("--words", type=int, default=50000, help="headwords in the pack")
    ap.add_argument("--provider", default="openrouter", help="[capabilities.think.<name>]")
    ap.add_argument("--limit", type=int, default=0, help="generate at most N new words this run")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true", help="plan only; call nothing")
    ap.add_argument("--yes", action="store_true", help="allow calls to a cloud provider")
    ap.add_argument("--retry-refused", action="store_true", help="send refused words again")
    ap.add_argument("--finalize-only", action="store_true", help="rebuild the pack from the JSONL")
    ap.add_argument("--dictionary", type=Path,
                    help="SCOWL 'final' directory; only words in it become headwords")
    ap.add_argument("--no-dictionary", action="store_true",
                    help="build without a word list (the tail then holds names and noise)")
    args = ap.parse_args(argv)

    dp, request = _dictionary_modules()
    out_dir: Path = args.out
    meta = {"provider": args.provider, "headword_source": HEADWORD_SOURCE}

    allowed = None
    if args.dictionary:
        allowed = load_word_list(args.dictionary)
        if not allowed:
            _config_error(f"no SCOWL word lists in {args.dictionary} (pass its 'final' directory)")
        meta["headword_filter"] = f"SCOWL {'+'.join(WORD_LIST_KINDS)} <= {WORD_LIST_MAX_LEVEL}"
    elif not args.no_dictionary:
        _config_error("pass --dictionary <SCOWL final dir>, or --no-dictionary to build "
                      "unfiltered (see the module docstring)")

    from wordfreq import top_n_list  # noqa: PLC0415

    # Over-fetch: the headword filters drop a share of the raw list.
    overfetch = FILTERED_OVERFETCH if allowed is not None else 2
    words = select_headwords(top_n_list("en", args.words * overfetch), args.words,
                             dp.normalize_headword, allowed)
    if len(words) < args.words:
        print(f"warning: only {len(words)} usable headwords for --words {args.words}")
    keep = set(words)
    if args.finalize_only:
        n = finalize(out_dir, dp.write_pack, meta, keep)
        print(f"pack: {n} entries -> {out_dir / PACK}")
        return 0
    todo = pending(words, read_progress(out_dir / JSONL), args.retry_refused)
    recorded = len(words) - len(todo)
    if args.limit:
        todo = todo[: args.limit]
    print(f"headwords: {len(words)} selected, {recorded} already recorded, "
          f"{len(todo)} to generate this run")
    # Built even for a dry run, so a misspelt --provider fails before the paid run.
    provider = _provider(args.provider)
    if args.dry_run:
        return 0
    if not todo:
        # Nothing left to generate, but an earlier run may have stopped before
        # building the pack — build it so "done" always leaves a pack behind.
        n = finalize(out_dir, dp.write_pack, meta, keep)
        print(f"pack: {n} entries -> {out_dir / PACK}")
        return 0

    if provider.is_cloud and not args.yes:
        print(f"{provider.name} is a cloud provider ({provider.host}); {len(todo)} paid "
              f"calls. Try --limit first to see the cost, then re-run with --yes.")
        return 2
    if not asyncio.run(provider.available()):
        print(f"{provider.name} is not available (missing API key or not running).")
        return 3

    from emptyos.sdk.utils import parse_llm_json  # noqa: PLC0415

    async def generate(word: str) -> str:
        # Cleared first: a reply with no usage block leaves last_usage alone,
        # which would otherwise count the previous call's cost again.
        provider.last_usage = None
        return await provider.execute(**build_request(word, request))

    def cost_of_last_call() -> float:
        # Approximate under concurrency: last_usage is per provider instance.
        usage = getattr(provider, "last_usage", None) or {}
        try:
            return float(usage.get("cost") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def progress(n: int, total: int) -> None:
        if n % 100 == 0 or n == total:
            print(f"  {n}/{total}", flush=True)

    counts = asyncio.run(generate_all(
        todo, out_dir / JSONL, generate,
        clean_entry=dp.clean_entry, parse=parse_llm_json, normalize=dp.normalize_headword,
        model=provider.model, concurrency=args.concurrency, retry_refused=args.retry_refused,
        cost_of_last_call=cost_of_last_call, on_progress=progress,
    ))
    print(f"accepted {counts['accepted']}, refused {counts['refused']}, "
          f"failed {counts['failed']} (retried next run); "
          f"reported cost ${counts['cost_usd']:.4f}")
    if counts["stopped"]:
        print(f"stopped after {MAX_CONSECUTIVE_FAILURES} failures in a row — "
              f"check the provider before re-running")
    n = finalize(out_dir, dp.write_pack, meta, keep)
    print(f"pack: {n} entries -> {out_dir / PACK}")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
