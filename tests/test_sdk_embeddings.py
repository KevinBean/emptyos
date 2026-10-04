"""Provider-aware embedder — config resolution, cache namespacing, graceful
degradation. Pure SDK, no daemon. The one live-ollama test skips when ollama
(bge-m3) isn't reachable, so CI without a local model still passes."""

import asyncio
import os
import tempfile
import urllib.request
from pathlib import Path

import pytest

from emptyos.sdk import embeddings as E


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Isolate resolution from BOTH config sources.

    `_resolve_embed_config` reads env > emptyos.toml > defaults. Clearing only
    the env left the *machine's* `[embeddings]` table leaking in, so a developer
    who set `provider = "ollama"` locally failed the "absent config" tests while
    CI (no emptyos.toml) passed. Stub the toml read so these tests assert the
    code's defaults, not the developer's config.
    """
    monkeypatch.setattr(E, "_load_toml_embeddings", lambda: {})
    keep = {k: os.environ.pop(k, None) for k in
            ("EOS_EMBED_PROVIDER", "EOS_EMBED_MODEL", "EOS_EMBED_BASE_URL", "EOS_EMBED_DIM")}
    yield
    for k, v in keep.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


def test_default_is_openai_dark():
    """Absent config, behaviour is byte-identical to before (OpenAI/1536)."""
    c = E._resolve_embed_config()
    assert c["provider"] == "openai"
    assert c["model"] == "text-embedding-3-small"
    assert c["dim"] == 1536


def test_env_switches_to_ollama_bge_m3():
    os.environ["EOS_EMBED_PROVIDER"] = "ollama"
    c = E._resolve_embed_config()
    assert c["provider"] == "ollama"
    assert c["model"] == "bge-m3"
    assert c["dim"] == 1024
    assert c["base_url"] == E._OLLAMA_DEFAULT_URL


def test_explicit_dim_override_wins():
    os.environ["EOS_EMBED_PROVIDER"] = "ollama"
    os.environ["EOS_EMBED_MODEL"] = "some-unlisted-model"
    os.environ["EOS_EMBED_DIM"] = "512"
    c = E._resolve_embed_config()
    assert c["model"] == "some-unlisted-model"
    assert c["dim"] == 512


def test_model_tag_is_filesystem_safe():
    assert E._model_tag("bge-m3") == "bge-m3"
    assert E._model_tag("text-embedding-3-small") == "text-embedding-3-small"
    assert "/" not in E._model_tag("org/model:v1")
    assert ":" not in E._model_tag("org/model:v1")


def test_cache_is_namespaced_per_model():
    """Switching models must never reuse the other model's cache (dim clash)."""
    tmp = Path(tempfile.mkdtemp())
    os.environ["EOS_EMBED_PROVIDER"] = "ollama"
    e1 = E.Embedder(cache_path=tmp / "shared.json")
    assert e1.cache_path.name == "shared.bge-m3.json"
    assert e1.dim == 1024

    os.environ["EOS_EMBED_PROVIDER"] = "openai"
    e2 = E.Embedder(cache_path=tmp / "shared.json")
    assert e2.cache_path.name == "shared.text-embedding-3-small.json"
    assert e2.dim == 1536
    assert e1.cache_path != e2.cache_path


def test_unreachable_local_degrades_gracefully():
    """Local down → available False (callers grep-degrade), zero-vectors at the
    right dim, never a crash and never cross-model garbage."""
    os.environ["EOS_EMBED_PROVIDER"] = "ollama"
    os.environ["EOS_EMBED_BASE_URL"] = "http://127.0.0.1:1/v1"  # dead port
    tmp = Path(tempfile.mkdtemp())
    e = E.Embedder(cache_path=tmp / "shared.json")
    assert e.available is False
    vecs = asyncio.run(e.embed_many(["hello", "world"]))
    assert len(vecs) == 2
    assert all(len(v) == 1024 for v in vecs)  # dim matches bge-m3, not 1536


def _ollama_bge_m3_up() -> bool:
    try:
        with urllib.request.urlopen(E._OLLAMA_DEFAULT_URL.rstrip("/") + "/models", timeout=2.5) as r:
            return b"bge-m3" in r.read()
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_bge_m3_up(), reason="ollama bge-m3 not available")
def test_live_bge_m3_semantic_ranking():
    os.environ["EOS_EMBED_PROVIDER"] = "ollama"
    tmp = Path(tempfile.mkdtemp())
    e = E.Embedder(cache_path=tmp / "shared.json")

    async def go():
        docs = await e.embed_many(["how to publish a blog post", "接地系统设计与 touch voltage"])
        q = await e.embed_one("blogging and publishing articles")
        return E.cosine(q, docs[0]), E.cosine(q, docs[1])

    s_blog, s_earth = asyncio.run(go())
    assert s_blog > s_earth
