"""Automation engine for Boards — trigger/condition/action model.

Rules are stored in the board config frontmatter under `rules:`.
When an item is updated, rules are evaluated and matching actions dispatched.
"""

from __future__ import annotations

import re
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from emptyos.sdk.base_app import BaseApp


class GuardBlocked(Exception):
    """Raised when a `kind=guard` rule blocks a field change pre-commit."""

    def __init__(self, rule: dict, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


async def evaluate_guards(app: BaseApp, board_config: dict,
                          old_item: dict | None, updates: dict) -> dict | None:
    """Evaluate `kind=guard` rules BEFORE committing a field change.

    Returns None if all guards pass. Returns {error, guard, message} if any
    guard blocks. Guards are trigger→condition→block, not trigger→action —
    they stop writes rather than react to them.

    Called by the boards app's PATCH path before `lib.set_field()`.
    """
    rules = board_config.get("rules", [])
    if not rules or not updates:
        return None

    # Simulate the proposed new_item by merging updates into old_item.
    projected = {**(old_item or {}), **updates}

    for rule in rules:
        if rule.get("kind") != "guard":
            continue
        trigger = rule.get("trigger", "field_change")
        field = rule.get("field", "")
        from_val = rule.get("from")
        to_val = rule.get("to")

        # A state-transition guard only fires when the field is being changed
        # AND the transition matches the declared from/to.
        if field and field not in updates:
            continue
        if field:
            old_v = (old_item or {}).get(field)
            new_v = updates[field]
            if from_val is not None and str(old_v) != str(from_val):
                continue
            if to_val is not None and str(new_v) != str(to_val):
                continue

        guard_expr = rule.get("guard", "")
        if not _eval_guard(guard_expr, projected):
            on_block = rule.get("on_block", {}) or {}
            message = on_block.get("toast") or f"Blocked: {guard_expr}"
            # Emit a side-channel event so UIs beyond the initiating PATCH can react.
            emit_type = on_block.get("emit")
            if emit_type:
                await app.emit(emit_type, {"rule": rule, "item": old_item})
            return {"error": "guard_blocked", "guard": guard_expr, "message": message}

    return None


def _eval_guard(expr: str, item: dict) -> bool:
    """Minimal expression evaluator — no arbitrary code. Supports:
        <field> == <literal>
        <field> != <literal>
        <field> is_empty
        <field> is_not_empty
        <field> > <number>  (also >=, <, <=)
        <expr1> and <expr2>
        <expr1> or <expr2>
    Literals: bare words, quoted strings, numbers, true/false.
    """
    if not expr:
        return True
    expr = expr.strip()

    # Boolean combinators (left-associative, no parens for now).
    for joiner, fn in (("and", all), ("or", any)):
        # split on standalone ' and '/' or '; crude but good enough for the
        # rule grammar we advertise.
        if f" {joiner} " in expr:
            parts = [p.strip() for p in expr.split(f" {joiner} ")]
            return fn(_eval_guard(p, item) for p in parts)

    # Single clause.
    tokens = expr.split()
    if len(tokens) == 2 and tokens[1] in ("is_empty", "is_not_empty"):
        v = item.get(tokens[0])
        empty = v is None or v == "" or v == [] or v is False
        return empty if tokens[1] == "is_empty" else (not empty)
    if len(tokens) >= 3:
        lhs, op = tokens[0], tokens[1]
        rhs = " ".join(tokens[2:]).strip().strip('"').strip("'")
        lv = item.get(lhs)
        if op in ("==", "equals"):
            return str(lv).lower() == _coerce_bool_str(rhs).lower()
        if op in ("!=", "not_equals"):
            return str(lv).lower() != _coerce_bool_str(rhs).lower()
        if op in (">", ">=", "<", "<="):
            try:
                lnum = float(lv); rnum = float(rhs)
            except (TypeError, ValueError):
                return False
            return (lnum > rnum if op == ">" else
                    lnum >= rnum if op == ">=" else
                    lnum < rnum if op == "<" else
                    lnum <= rnum)
    return False


def _coerce_bool_str(s: str) -> str:
    """Normalize 'true'/'false'/'yes'/'no' to canonical form for comparisons."""
    low = s.lower()
    if low in ("true", "yes", "1"): return "true"
    if low in ("false", "no", "0"): return "false"
    return s


async def evaluate_rules(app: BaseApp, board_config: dict,
                         old_item: dict | None, new_item: dict,
                         event_type: str = "field_changed"):
    """Evaluate all automation rules for a board event.

    Args:
        app: The BoardsApp instance (for emit, call_app, etc.)
        board_config: The full board config dict (contains `rules`)
        old_item: The item state before the change (None for new items)
        new_item: The item state after the change
        event_type: One of: field_changed, item_created, item_archived
    """
    rules = board_config.get("rules", [])
    if not rules:
        return

    board_id = board_config.get("id", "unknown")

    for rule in rules:
        # Guards are evaluated pre-commit elsewhere — skip in the action loop.
        if rule.get("kind") == "guard":
            continue
        trigger = rule.get("trigger", {})
        condition = rule.get("condition")
        actions = rule.get("actions", [])

        # Check trigger
        if not _trigger_matches(trigger, event_type, old_item, new_item):
            continue

        # Check condition
        if condition and not _condition_met(condition, new_item):
            continue

        # Execute actions
        for action in actions:
            await _execute_action(app, board_id, action, new_item)


def _trigger_matches(trigger: dict, event_type: str,
                     old_item: dict | None, new_item: dict) -> bool:
    """Check if a trigger definition matches the current event."""
    trigger_event = trigger.get("event", "")

    if trigger_event == "item_created" and event_type == "item_created":
        return True

    if trigger_event == "item_archived" and event_type == "item_archived":
        return True

    if trigger_event == "field_changed" and event_type == "field_changed":
        field = trigger.get("field", "")
        target_value = trigger.get("value")

        if not field:
            return True  # Any field change

        old_val = (old_item or {}).get(field)
        new_val = new_item.get(field)

        if old_val == new_val:
            return False  # Field didn't actually change

        if target_value is not None:
            return str(new_val) == str(target_value)

        return True  # Field changed, no specific value required

    if trigger_event == "date_arrived":
        field = trigger.get("field", "")
        if field:
            val = str(new_item.get(field, ""))
            return val == date.today().isoformat()

    if trigger_event == "date_overdue":
        field = trigger.get("field", "")
        if field:
            val = str(new_item.get(field, ""))
            if val and len(val) >= 10:
                try:
                    return date.fromisoformat(val[:10]) < date.today()
                except ValueError:
                    pass

    return False


def _condition_met(condition: dict, item: dict) -> bool:
    """Evaluate a condition against an item."""
    field = condition.get("field", "")
    operator = condition.get("operator", "==")
    threshold = condition.get("threshold")
    value = condition.get("value")

    item_val = item.get(field)

    if operator == "==" or operator == "equals":
        return str(item_val) == str(value or threshold)
    if operator == "!=" or operator == "not_equals":
        return str(item_val) != str(value or threshold)
    if operator == ">":
        return _to_num(item_val) > _to_num(threshold)
    if operator == "<":
        return _to_num(item_val) < _to_num(threshold)
    if operator == ">=":
        return _to_num(item_val) >= _to_num(threshold)
    if operator == "<=":
        return _to_num(item_val) <= _to_num(threshold)
    if operator == "contains":
        return str(value or "").lower() in str(item_val or "").lower()
    if operator == "is_empty":
        return not item_val
    if operator == "is_not_empty":
        return bool(item_val)

    return True  # Unknown operator → pass


async def _execute_action(app: BaseApp, board_id: str,
                          action: dict, item: dict):
    """Execute a single automation action."""
    action_type = action.get("type", "")

    if action_type == "notify":
        channel = action.get("channel", "")
        message = _interpolate(action.get("message", ""), item)
        await app.emit("notify:send", {
            "text": message,
            "source": f"Board: {board_id}",
            "channel": channel,
        })

    elif action_type == "set_field":
        field = action.get("field", "")
        value = action.get("value", "")
        # Interpolate special values
        if value == "{today}":
            value = date.today().isoformat()
        elif value == "{now}":
            from datetime import datetime
            value = datetime.now().isoformat()
        # Update the item in-memory (caller should persist)
        item[field] = value

    elif action_type == "move_to_group":
        # Same as set_field on the kanban group column
        field = action.get("field", "status")
        value = action.get("value", "")
        item[field] = value

    elif action_type == "call_app":
        target_app = action.get("app", "")
        method = action.get("method", "")
        kwargs = action.get("kwargs", {})
        # Interpolate kwargs
        kwargs = {k: _interpolate(str(v), item) for k, v in kwargs.items()}
        try:
            await app.call_app(target_app, method, **kwargs)
        except Exception:
            pass

    elif action_type == "journal_entry":
        text = _interpolate(action.get("text", ""), item)
        emoji = action.get("emoji", "📋")
        try:
            await app.call_app("journal", "_add_entry",
                               d=date.today(), text=f"{emoji} {text}", mood="okay")
        except Exception:
            pass

    elif action_type == "propagate_slip":
        # When a date field moves forward and auto_slip=true on the rule,
        # push every item in `blocks` by the same delta (capped).
        field = action.get("field", "due")
        limit_days = int(action.get("auto_slip_limit_days", 14))
        # Compute delta from old_item vs new_item — caller provides both via `item`;
        # we expect `item['_slip_days']` to be pre-computed by the boards app when
        # firing this action (boards layer has old+new, we only see new here).
        delta = int(item.get("_slip_days", 0) or 0)
        if delta <= 0 or delta > limit_days:
            return
        downstream = item.get("blocks") or []
        if isinstance(downstream, str):
            downstream = [s.strip() for s in downstream.split(",") if s.strip()]
        board_id = item.get("_board_id", board_id)
        for target_id in downstream:
            try:
                # Load the downstream item, compute its new date, write back.
                r = await app.call_app("boards", "shift_item_date",
                                       board_id=board_id, item_id=target_id,
                                       field=field, delta_days=delta)
                await app.emit("board:item_auto_slipped", {
                    "board": board_id, "from_item": item.get("id") or item.get("file"),
                    "to_item": target_id, "delta_days": delta, "result": r,
                })
            except Exception:
                pass

    elif action_type == "create_item":
        # Create a new vault note
        tag = action.get("tag", "")
        fields = action.get("fields", {})
        fields = {k: _interpolate(str(v), item) for k, v in fields.items()}
        if tag:
            fields.setdefault("tags", [tag])
        # Would need vault_create_note — delegated to the app


def _interpolate(template: str, item: dict) -> str:
    """Replace {field_name} placeholders with item values."""
    def _replacer(m):
        field = m.group(1)
        return str(item.get(field, ""))
    return re.sub(r"\{(\w+)\}", _replacer, template)


def _to_num(val: Any) -> float:
    """Safely convert to number for comparisons."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0
