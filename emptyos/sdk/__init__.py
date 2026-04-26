"""EmptyOS App SDK."""

from emptyos.sdk.base_app import BaseApp
from emptyos.sdk.base_engine import BaseEngine
from emptyos.sdk.base_plugin import BasePlugin
from emptyos.sdk.external_service import ExternalServiceBase
from emptyos.sdk.decorators import cli_command, on_event, scheduled, web_route, ws_route
from emptyos.sdk.utils import (
    CAPTURE_LINE_RE, DONE_PATTERN, DUE_PATTERN, TASK_RE, compute_task_decay,
    fm_list, fm_str, load_json, parse_captures, parse_frontmatter, parse_llm_json, save_json,
    set_frontmatter_field, slugify, strip_frontmatter, strip_markdown, task_tier, today_iso,
)
# Practice app shared modules
from emptyos.sdk.audio import assess_fillers, assess_pacing, compute_speech_metrics
from emptyos.sdk.scoring import lcs_score, word_accuracy
from emptyos.sdk.session import HistoryStore, SessionStore
from emptyos.sdk.chat_session import ChatSessionStore
from emptyos.sdk.stats import daily_counts, practice_stats, progress_percent, rolling_average
from emptyos.sdk.vault_library import VaultLibrary
from emptyos.sdk.column_types import ColumnType, ColumnTypeRegistry
from emptyos.sdk import formulas
from emptyos.sdk.srs import sm2_schedule, due_items as srs_due_items, review_stats as srs_review_stats
from emptyos.sdk.time_series import TimeSeriesCounter, today_utc, days_ago_utc
from emptyos.sdk.streaming import NDJSON_MEDIA, ndjson_response
from emptyos.sdk import dimensions
from emptyos.sdk.markdown_render import (
    render_markdown, resolve_wikilinks, convert_callouts, extract_images,
)

__all__ = ["BaseApp", "BaseEngine", "BasePlugin", "ExternalServiceBase", "cli_command", "on_event", "scheduled", "web_route", "ws_route",
           "CAPTURE_LINE_RE", "fm_str", "fm_list", "load_json", "save_json", "parse_captures", "parse_frontmatter", "parse_llm_json", "set_frontmatter_field", "slugify", "strip_frontmatter", "strip_markdown", "today_iso",
           "SessionStore", "HistoryStore", "ChatSessionStore",
           "compute_speech_metrics", "assess_pacing", "assess_fillers",
           "lcs_score", "word_accuracy", "practice_stats", "rolling_average", "progress_percent", "daily_counts",
           "VaultLibrary", "ColumnType", "ColumnTypeRegistry", "formulas",
           "sm2_schedule", "srs_due_items", "srs_review_stats",
           "TimeSeriesCounter", "today_utc", "days_ago_utc",
           "NDJSON_MEDIA", "ndjson_response",
           "dimensions",
           "render_markdown", "resolve_wikilinks", "convert_callouts", "extract_images"]
