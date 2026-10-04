"""scripts/telegram_dispatch_eval — corpus shape + scorer semantics.

The live run needs a sandbox daemon and an LLM; these tests pin the offline
half: the corpus stays a real benchmark (size, both languages, every category),
and the scorer grades a turn by what the verb will actually do with its args.
"""

from datetime import date

import pytest

from scripts.telegram_dispatch_eval import (
    _refuse_user_daemon,
    actions_from_chat,
    expected_date,
    expected_dates,
    load_corpus,
    score_case,
    summarize,
    unparsed_do,
)

# A Wednesday that is NOT the real run date, so a scorer that reads the wall
# clock instead of rebasing onto the run date goes red.
TODAY = date(2026, 9, 16)
CATEGORIES = {"expense", "task", "reminder", "journal", "word", "query", "focus",
              "chat", "capture", "weather"}


def _case(*expect, category="task"):
    return {"id": "x", "category": category, "lang": "en", "text": "t", "expect": list(expect)}


def _act(verb, **args):
    return {"verb": verb, "args": args}


# ── corpus ──────────────────────────────────────────────────────────────────


def test_corpus_is_big_enough_and_covers_every_category_in_both_languages():
    cases = load_corpus()
    assert len(cases) >= 50
    assert len({c["id"] for c in cases}) == len(cases), "duplicate case ids"
    assert {c["category"] for c in cases} == CATEGORIES
    for lang in ("zh", "en"):
        assert sum(c["lang"] == lang for c in cases) >= 20


def test_every_expected_verb_is_app_dot_method_or_no_action():
    for c in load_corpus():
        assert c["expect"], c["id"]
        for o in c["expect"]:
            v = o.get("verb", "")
            assert v == "" or (v.count(".") == 1 and all(v.split("."))), (c["id"], v)


def test_every_date_spec_in_the_corpus_resolves():
    for c in load_corpus():
        for o in c["expect"]:
            for spec in (o.get("args") or {}).values():
                if isinstance(spec, dict) and "date" in spec:
                    assert expected_dates(spec["date"], TODAY)


# ── date specs ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("spec,want", [
    ({"offset": 0}, "2026-09-16"),
    ({"offset": 1}, "2026-09-17"),
    ({"weekday": "fri"}, "2026-09-18"),
    ({"weekday": "wed"}, "2026-09-16"),  # "by Wednesday", said on a Wednesday
    ({"after": "wed"}, "2026-09-23"),  # "on Wednesday", said on a Wednesday
    ({"after": "fri"}, "2026-09-18"),
    ({"next_week": "mon"}, "2026-09-21"),
    ({"next_week": "wed"}, "2026-09-23"),  # 下周三, said on a Wednesday
])
def test_expected_date(spec, want):
    assert expected_date(spec, TODAY) == want


# ── scoring ─────────────────────────────────────────────────────────────────


def test_right_verb_and_amount_passes():
    case = _case({"verb": "expense.voice_add_expense", "args": {"amount": 45}}, category="expense")
    s = score_case(case, [_act("expense.voice_add_expense", amount="45", description="午饭")], TODAY)
    assert s["verb_ok"] and s["args_ok"]


def test_wrong_amount_is_verb_ok_but_args_fail():
    case = _case({"verb": "expense.voice_add_expense", "args": {"amount": 45}}, category="expense")
    s = score_case(case, [_act("expense.voice_add_expense", amount=4.5, description="午饭")], TODAY)
    assert s["verb_ok"] and not s["args_ok"]
    assert s["bad_args"] == ["amount"]


def test_wrong_verb_fails():
    case = _case({"verb": "expense.voice_add_expense"})
    s = score_case(case, [_act("task.voice_add_task", text="lunch 45")], TODAY)
    assert not s["verb_ok"] and not s["args_ok"]


def test_relative_due_scores_as_what_the_verb_stores_rebased_on_the_run_date():
    # task.voice_add_task runs `due` through normalize_relative_date; since T2
    # that reads 明天 too. The scorer rebases the word onto the run's TODAY (a
    # Wednesday that is not the wall-clock date) rather than the wall clock.
    case = _case({"verb": "task.voice_add_task", "args": {"due": {"date": {"offset": 1}}}})
    for due in ("明天", "tomorrow", "2026-09-17"):
        assert score_case(case, [_act("task.voice_add_task", text="交报告", due=due)], TODAY)["args_ok"], due
    assert not score_case(case, [_act("task.voice_add_task", text="交报告", due="后天")], TODAY)["args_ok"]
    assert not score_case(case, [_act("task.voice_add_task", text="交报告", due="next week")], TODAY)["args_ok"]


def test_calendar_day_reads_relative_words_and_refuses_the_unreadable():
    case = _case({"verb": "calendar.voice_today_agenda", "args": {"day": {"date": {"offset": 1}}}})
    # A compact 20260917 now reads too: the verb normalises through
    # date.fromisoformat, which accepts it, and hands get_agenda the ISO form.
    for day in ("tomorrow", "明天", "2026-09-17", "20260917"):
        assert score_case(case, [_act("calendar.voice_today_agenda", day=day)], TODAY)["args_ok"], day
    # The verb refuses a day it cannot read, so the card does nothing:
    # bad_args names the arg the verb refused.
    for day in ("someday", "next week"):
        s = score_case(case, [_act("calendar.voice_today_agenda", day=day)], TODAY)
        assert not s["args_ok"] and "day" in s["bad_args"], day
    today_case = _case({"verb": "calendar.voice_today_agenda", "args": {"day": {"date": {"offset": 0}}}})
    assert score_case(today_case, [_act("calendar.voice_today_agenda")], TODAY)["args_ok"]
    assert score_case(today_case, [_act("calendar.voice_today_agenda", day="today")], TODAY)["args_ok"]
    # A corpus outcome that names no `day` at all still loses to an unreadable
    # one: the verb refuses it, so the card does nothing when applied.
    bare = _case({"verb": "calendar.voice_today_agenda"}, category="query")
    s = score_case(bare, [_act("calendar.voice_today_agenda", day="someday")], TODAY)
    assert s["verb_ok"] and not s["args_ok"] and s["bad_args"] == ["day"]
    assert score_case(bare, [_act("calendar.voice_today_agenda", day="明天")], TODAY)["args_ok"]


def test_reminder_time_is_normalised_like_the_verb_does():
    case = _case({"verb": "reminders.voice_add_reminder", "args": {"time": {"time": "20:00"}}})
    def ok(t):
        return score_case(case, [_act("reminders.voice_add_reminder", text="吃药", time=t)],
                          TODAY)["args_ok"]
    assert ok("8pm") and ok("20:00")
    assert not ok("晚上八点")
    # The verb calls normalize_clock_time(raw); an int raises there.
    assert not ok(20)


def test_any_of_word_arg_normalises_spacing_and_case():
    case = _case({"verb": "task.voice_list_due", "args": {"when": {"any": ["this_week"]}}})
    assert score_case(case, [_act("task.voice_list_due", when="This Week")], TODAY)["args_ok"]
    assert not score_case(case, [_act("task.voice_list_due", when="today")], TODAY)["args_ok"]


def test_no_action_expected_passes_only_when_nothing_emitted():
    case = _case({"verb": ""}, category="chat")
    assert score_case(case, [], TODAY)["args_ok"]
    s = score_case(case, [_act("quick-action.voice_capture", text="hi")], TODAY)
    assert not s["verb_ok"]


def test_alternative_outcome_matches():
    case = _case({"verb": "task.voice_add_task", "args": {"due": {"date": {"offset": 1}}}},
                 {"verb": "reminders.voice_add_reminder",
                  "args": {"due": {"date": {"offset": 1}}, "time": {"time": "15:00"}}})
    s = score_case(case, [_act("reminders.voice_add_reminder", text="交报告",
                               due="2026-09-17", time="3pm")], TODAY)
    assert s["args_ok"] and s["matched"] == "reminders.voice_add_reminder"


def test_first_outcome_with_bad_args_does_not_hide_a_later_full_match():
    case = _case({"verb": "task.voice_add_task", "args": {"due": {"date": {"offset": 1}}}},
                 {"verb": "reminders.voice_add_reminder", "args": {"due": {"date": {"offset": 1}}}})
    acts = [_act("task.voice_add_task", text="x", due="明天"),
            _act("reminders.voice_add_reminder", text="x", due="tomorrow")]
    s = score_case(case, acts, TODAY)
    assert s["args_ok"] and s["extra"] == 1


def test_summarize_counts_per_category_and_overall():
    rows = [
        {"category": "expense", "score": {"verb_ok": True, "args_ok": True}},
        {"category": "expense", "score": {"verb_ok": True, "args_ok": False}},
        {"category": "chat", "score": {"verb_ok": False, "args_ok": False},
         "unparsed_do": ["weather.voice_now"]},
    ]
    s = summarize(rows)
    assert s["expense"] == {"n": 2, "verb_ok": 2, "args_ok": 1, "unparsed": 0, "broken_extra": 0}
    assert s["_all"] == {"n": 3, "verb_ok": 2, "args_ok": 1, "unparsed": 1, "broken_extra": 0}


def test_unparsed_do_finds_tokens_the_rooms_parser_skipped():
    # Zero-arg verbs written `()` instead of `({})` never match DO_RE, so the
    # raw token reaches the phone and nothing runs (baseline 2026-09-30).
    assert unparsed_do("Checking. [DO:weather.voice_now()]") == ["weather.voice_now"]
    assert unparsed_do("[DO:quick-action.voice_capture(") == ["quick-action.voice_capture"]
    assert unparsed_do("done, nothing pending") == []


def test_actions_from_chat_reads_pending_and_applied_entries():
    res = {"server_results": [
        {"app": "expense", "method": "voice_add_expense", "args": {"amount": 45}, "status": "pending"},
        {"app": "task", "method": "voice_add_task", "args": {"text": "x"}, "status": "applied"},
        "garbage",
    ]}
    assert actions_from_chat(res) == [
        {"verb": "expense.voice_add_expense", "args": {"amount": 45}, "status": "pending"},
        {"verb": "task.voice_add_task", "args": {"text": "x"}, "status": "applied"},
    ]


def test_non_gate_result_shape_maps_ok_to_status_and_a_failure_scores_as_a_miss():
    # Non-gate agents answer {"ok": bool} with no status (rooms/pending.py);
    # a refused call must not read as a match.
    acts = actions_from_chat({"server_results": [
        {"app": "task", "method": "voice_add_task", "ok": False, "error": "not allowed"},
        {"app": "weather", "method": "voice_now", "ok": True, "result": "sunny"},
    ]})
    assert [a["status"] for a in acts] == ["error", "ok"]
    assert not score_case(_case({"verb": "task.voice_add_task"}), acts[:1], TODAY)["args_ok"]


def test_date_spec_list_accepts_any_reading():
    case = _case({"verb": "task.voice_add_task",
                  "args": {"due": {"date": [{"after": "wed"}, {"after": "wed", "weeks": 1}]}}})
    sunday = date(2026, 9, 20)
    for due in ("2026-09-23", "2026-09-30"):  # "next Wednesday" said on a Sunday
        assert score_case(case, [_act("task.voice_add_task", text="x", due=due)], sunday)["args_ok"]
    assert not score_case(case, [_act("task.voice_add_task", text="x", due="2026-09-24")],
                          sunday)["args_ok"]


def test_verb_that_would_refuse_its_args_is_a_miss():
    # reminders.voice_add_reminder answers "couldn't parse the date" and stores
    # nothing when `due` is non-empty and neither a day nor an offset from now.
    # Baseline r07 ("半小时后") was refused; since T2 an offset is accepted, so
    # the miss is only the genuinely unreadable word.
    case = _case({"verb": "reminders.voice_add_reminder"}, category="reminder")
    s = score_case(case, [_act("reminders.voice_add_reminder", text="关火", due="someday")], TODAY)
    assert s["verb_ok"] and not s["args_ok"] and s["bad_args"] == ["due"]
    for due in ("in 30 minutes", "半小时后", "tomorrow", "明天"):
        assert score_case(case, [_act("reminders.voice_add_reminder", text="关火", due=due)], TODAY)["args_ok"], due
    assert score_case(case, [_act("reminders.voice_add_reminder", text="关火")], TODAY)["args_ok"]
    # The verb also reads an offset out of `time`, and it fills an unreadable day.
    assert score_case(case, [_act("reminders.voice_add_reminder", text="关火", due="someday",
                                  time="in 30 minutes")], TODAY)["args_ok"]
    assert not score_case(case, [_act("reminders.voice_add_reminder", text="关火", due="someday",
                                      time="9am")], TODAY)["args_ok"]
    # calendar strips before reading, so a blank day is today, not a refusal.
    cal = _case({"verb": "calendar.voice_today_agenda"}, category="query")
    assert score_case(cal, [_act("calendar.voice_today_agenda", day=" ")], TODAY)["args_ok"]
    exp = _case({"verb": "expense.voice_add_expense", "args": {"amount": 45}}, category="expense")
    assert not score_case(exp, [_act("expense.voice_add_expense", amount=45)], TODAY)["args_ok"]


def test_failed_action_and_unoffered_verb_are_misses():
    case = _case({"verb": "task.voice_add_task"})
    failed = {"verb": "task.voice_add_task", "args": {"text": "x"}, "status": "failed"}
    assert not score_case(case, [failed], TODAY)["args_ok"]
    word = _case({"verb": ""}, {"verb": "dictionary.lookup", "args": {"word": "quay"}}, category="word")
    act = [_act("dictionary.lookup", word="quay")]
    assert not score_case(word, act, TODAY, offered={"task.voice_add_task"})["args_ok"]
    assert score_case(word, act, TODAY, offered={"dictionary.lookup"})["args_ok"]


def test_broken_second_card_is_counted():
    # Baseline q01 was a correct task list plus an agenda card for
    # day="today", which get_agenda could not read — a false "nothing on".
    # Since T2 the verb reads "today"; a day it still cannot read is the
    # broken extra card.
    case = _case({"verb": "calendar.voice_today_agenda", "args": {"day": {"date": {"offset": 0}}}},
                 {"verb": "task.voice_list_today"}, category="query")
    s = score_case(case, [_act("calendar.voice_today_agenda", day="someday"),
                          _act("task.voice_list_today")], TODAY)
    assert s["args_ok"] and s["broken_extra"] == 1 and s["extra"] == 1
    for day in ({}, {"day": "today"}):
        ok = score_case(case, [_act("calendar.voice_today_agenda", **day),
                               _act("task.voice_list_today")], TODAY)
        assert ok["broken_extra"] == 0 and ok["extra"] == 1


def test_run_records_its_provenance_flags_not_the_last_turns_actions(tmp_path, monkeypatch):
    # `run(..., actions="target")` writes `"actions": actions` into the report;
    # a loop variable of the same name once shadowed it, so the field carried
    # the last turn's emitted list (hostile review, 2026-09-30).
    import scripts.telegram_dispatch_eval as ev

    calls: list[tuple[str, str]] = []

    def fake_http(method, url, body=None, timeout=30):
        calls.append((method, url))
        if url.endswith("/rooms/api/agents/telegram-bridge") and ":9000" in url:
            return {"id": "telegram-bridge", "name": "TB", "system_prompt": "LIVE", "gate_mode": "gate",
                    "server_actions": {"task": ["voice_add_task"], "old": ["gone"]}}
        if url.endswith("/voice-assistant/debug/intents"):
            return {"registry": [{"app": "task", "method": "voice_add_task"},
                                 {"app": "task", "method": "voice_add_task"},
                                 {"app": "dictionary", "method": "voice_lookup"},
                                 {"app": "voice-assistant", "method": "voice_remember"}]}
        if url.endswith("/rooms/api/chat"):
            return {"server_results": [{"app": "task", "method": "voice_add_task",
                                        "args": {"text": "x"}, "status": "pending"}]}
        return {}

    monkeypatch.setattr(ev, "_http", fake_http)
    monkeypatch.setattr(ev, "load_corpus", lambda path=None: [
        {"id": "t", "category": "task", "lang": "en", "text": "todo x",
         "expect": [{"verb": "task.voice_add_task"}]}])
    report = ev.run("http://127.0.0.1:9000", "http://127.0.0.1:9002", tmp_path / "r.json",
                    persona="seed", actions="target")
    assert (report["persona"], report["actions"]) == ("seed", "target")
    assert report["offered_verbs"] == ["dictionary.voice_lookup", "task.voice_add_task"]
    assert report["agent_prompt_chars"] == len(ev.shipped_persona()) > 0
    assert report["cases"][0]["actions"] == [{"verb": "task.voice_add_task", "args": {"text": "x"},
                                              "status": "pending"}]
    registered = [m for m, u in calls if u.endswith("/rooms/api/agents") and ":9002" in u]
    assert "POST" in registered  # the target had no agent, so the spec was created there


def test_unparsed_do_variants():
    assert unparsed_do("[DO:weather.voice_now]") == ["weather.voice_now"]
    assert unparsed_do("[DO: weather.voice_now()]") == ["weather.voice_now"]


@pytest.mark.parametrize("url", ["http://127.0.0.1:9000", "http://127.0.0.1:9000/x",
                                 "http://127.0.0.1:09000", "http://127.0.0.1:9001/",
                                 "http://127.0.0.1"])
def test_runner_refuses_user_daemons(url):
    with pytest.raises(SystemExit):
        _refuse_user_daemon(url)
    _refuse_user_daemon("http://127.0.0.1:9002")
