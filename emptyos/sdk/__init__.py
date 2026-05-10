"""EmptyOS App SDK."""

from emptyos.sdk import dimensions, formulas

# Practice app shared modules
from emptyos.sdk.audio import assess_fillers, assess_pacing, compute_speech_metrics
from emptyos.sdk.base_app import BaseApp
from emptyos.sdk.base_engine import BaseEngine
from emptyos.sdk.base_plugin import BasePlugin
from emptyos.sdk.chat_session import ChatSessionStore
from emptyos.sdk.column_types import ColumnType, ColumnTypeRegistry
from emptyos.sdk.decorators import cli_command, on_event, scheduled, web_route, ws_route
from emptyos.sdk.embeddings import (
    Embedder,
    EmbeddingIndex,
    build_index,
    build_retrieval_query,
    cosine,
)
from emptyos.sdk.external_service import ExternalServiceBase
from emptyos.sdk.intents import (
    DEFAULT_INTENT_PROMPT_HEADER,
    INTENT_RE,
    MAX_INTENTS_IN_PROMPT,
    build_plan_dict,
    find_intents,
    intent_embedding_text,
    render_intent_block,
    scope_intents,
    scope_intents_by_relevance,
    validate_args,
)
from emptyos.sdk.markdown_render import (
    convert_callouts,
    extract_images,
    render_markdown,
    resolve_wikilinks,
)
from emptyos.sdk.conformance import ConformanceCase, ConformanceRegistry
from emptyos.sdk.method_registry import MethodRegistry, MethodSpec
from emptyos.sdk.schema import (
    field_help,
    field_range,
    field_unit,
    inputs_hash,
    schema_field,
    to_jsonschema,
    validate,
)
from emptyos.sdk.scoring import lcs_score, word_accuracy
from emptyos.sdk.session import HistoryStore, SessionStore
from emptyos.sdk.srs import (
    due_items as srs_due_items,
)
from emptyos.sdk.srs import (
    review_stats as srs_review_stats,
)
from emptyos.sdk.srs import (
    sm2_schedule,
)
from emptyos.sdk.stats import daily_counts, practice_stats, progress_percent, rolling_average
from emptyos.sdk.streaming import NDJSON_MEDIA, ndjson_response
from emptyos.sdk.time_series import TimeSeriesCounter, days_ago_utc, today_utc
from emptyos.sdk.utils import (
    CAPTURE_LINE_RE,
    DONE_PATTERN,
    DUE_PATTERN,
    ROOM_PATTERN,
    TASK_RE,
    WIKILINK_RE,
    compute_task_decay,
    extract_wikilinks,
    fm_list,
    fm_str,
    load_json,
    new_id,
    now_iso,
    parse_captures,
    parse_frontmatter,
    parse_llm_json,
    save_json,
    set_frontmatter_field,
    slugify,
    strip_frontmatter,
    strip_markdown,
    task_tier,
    today_iso,
)
from emptyos.sdk.music_library import (
    AUDIO_EXTS,
    SIDECAR_MD,
    AlbumLibrary,
    LibraryMixin,
    SongLibrary,
)
from emptyos.sdk.vault_library import VaultLibrary

__all__ = [
    "DONE_PATTERN",
    "DUE_PATTERN",
    "ROOM_PATTERN",
    "TASK_RE",
    "compute_task_decay",
    "task_tier",
    "BaseApp",
    "BaseEngine",
    "BasePlugin",
    "ExternalServiceBase",
    "DEFAULT_INTENT_PROMPT_HEADER",
    "INTENT_RE",
    "MAX_INTENTS_IN_PROMPT",
    "build_plan_dict",
    "find_intents",
    "intent_embedding_text",
    "render_intent_block",
    "scope_intents",
    "scope_intents_by_relevance",
    "validate_args",
    "cli_command",
    "on_event",
    "scheduled",
    "web_route",
    "ws_route",
    "CAPTURE_LINE_RE",
    "fm_str",
    "fm_list",
    "load_json",
    "new_id",
    "save_json",
    "parse_captures",
    "parse_frontmatter",
    "parse_llm_json",
    "set_frontmatter_field",
    "slugify",
    "strip_frontmatter",
    "WIKILINK_RE",
    "extract_wikilinks",
    "strip_markdown",
    "today_iso",
    "now_iso",
    "SessionStore",
    "HistoryStore",
    "ChatSessionStore",
    "compute_speech_metrics",
    "assess_pacing",
    "assess_fillers",
    "lcs_score",
    "word_accuracy",
    "practice_stats",
    "rolling_average",
    "progress_percent",
    "daily_counts",
    "VaultLibrary",
    "SongLibrary",
    "AlbumLibrary",
    "LibraryMixin",
    "SIDECAR_MD",
    "AUDIO_EXTS",
    "ColumnType",
    "ColumnTypeRegistry",
    "formulas",
    "sm2_schedule",
    "srs_due_items",
    "srs_review_stats",
    "TimeSeriesCounter",
    "today_utc",
    "days_ago_utc",
    "NDJSON_MEDIA",
    "ndjson_response",
    "dimensions",
    "render_markdown",
    "resolve_wikilinks",
    "convert_callouts",
    "extract_images",
    "MethodRegistry",
    "MethodSpec",
    "ConformanceCase",
    "ConformanceRegistry",
    "schema_field",
    "validate",
    "to_jsonschema",
    "inputs_hash",
    "field_unit",
    "field_range",
    "field_help",
]
