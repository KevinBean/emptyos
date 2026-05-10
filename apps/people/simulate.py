"""Contacts simulation — AI chat, profile enrichment, personality analysis.

Extracted from contacts/app.py to keep the main app atomic.
These are mixin methods added back to ContactsApp via import.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from emptyos.sdk import web_route


SIMULATE_LOG_SUMMARY_SYSTEM = (
    "You summarise a logged conversation between the user and an AI-simulated "
    "contact in a single concise line under 80 characters. Bilingual "
    "(English/Chinese) is fine. Output the line only — no quotes, no "
    "labels, no preamble.\n\n"
    "Do NOT:\n"
    "- Wrap the output in quotes or markdown.\n"
    "- Repeat the contact's name unless it carries new info.\n"
    "- Add 'Summary:' or any leading label.\n"
    "- Exceed 80 characters."
)


# ---------------------------------------------------------------------------
# Chat Simulation — AI simulates contacts, enriches profiles, advises
# ---------------------------------------------------------------------------


def _extract_body(self, content: str) -> str:
    """Extract markdown body after frontmatter."""
    if not content.startswith("---"):
        return content
    end = content.find("---", 3)
    if end < 0:
        return content
    body = content[end + 3 :].strip()
    lines = []
    in_block = False
    for line in body.split("\n"):
        s = line.strip()
        if s.startswith("```") and not in_block:
            if any(kw in s.lower() for kw in ("dataview", "tasks")):
                in_block = True
                continue
            lines.append(line)
            continue
        if in_block:
            if s == "```":
                in_block = False
            continue
        if s.startswith("[[_") and s.endswith("]]"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _me_file(self) -> Path:
    return self.vault_config_path("me_file", "20_Areas/Personal-Info/_me.md") or Path(".")


async def _load_user_profile(self) -> str:
    """Load concise user profile from _me.md."""
    try:
        text = await self.read(str(self._me_file()))
    except Exception:
        return ""
    body = self._extract_body(text)
    return body[:1500] + "\n..." if len(body) > 1500 else body


@web_route("POST", "/api/simulate")
async def api_simulate(self, request):
    """Chat simulation with multiple modes:
    - chat: AI plays the contact
    - enrich: AI interviews user to build profile
    - advisor: AI helps prepare for conversations
    - self: Talk to present self
    - self-era: Talk to past self at a specific era
    """
    data = await request.json()
    name = data.get("name", "")
    message = data.get("message", "")
    history = data.get("history", [])
    mode = data.get("mode", "chat")
    era = data.get("era", "")

    if not message:
        return {"error": "message required"}

    is_self = name.lower() in ("self", "_self", "__self__", "me")

    if is_self:
        target = self._me_file()
    else:
        target = self._find_file(name)

    if not target or not target.exists():
        return {"error": "Contact not found" if not is_self else "_me.md not found"}

    content = await self.read(str(target))
    fm = self._parse_frontmatter(content)
    body = self._extract_body(content)
    if len(body) > 3000:
        body = body[:3000] + "\n..."

    quick_log = self._parse_quick_log(content)
    log_text = (
        "\n".join(f"- {e['date']}: {e['text']}" for e in quick_log[:10])
        or "No recent interactions."
    )
    user_profile = await self._load_user_profile()
    user_name = self.require("settings").get("user.name", "User")

    # Build system prompt based on mode
    if mode == "enrich" and is_self and era:
        system_prompt = (
            f"You are helping {user_name} recall and document his life at age {era}.\n"
            "Interview him about that period — ask focused questions one at a time "
            f"to capture memories, feelings, key events around age {era}.\n\n"
            f"## Current Profile\n{body}\n\n"
            "Topics: where he lived, relationships, career/education, dreams, challenges, "
            "defining moments, what he didn't know yet.\n\n"
            "When the user says 'done' or 'save', output:\n"
            "```profile\n"
            f"era: {era}\nlocation: ...\nlife_situation: ...\nrelationships: ...\n"
            "dreams: ...\nchallenges: ...\ndefining_moments: ...\nself_perception: ...\n```"
        )
    elif mode == "enrich" and is_self:
        system_prompt = (
            f"You are helping {user_name} update his personal profile.\n"
            "Interview him — ask focused questions one at a time about recent changes.\n\n"
            f"## Current Profile\n{body}\n\n"
            "Topics: recent changes, goals, skills, values, career, relationships.\n\n"
            "When the user says 'done' or 'save', output:\n"
            "```profile\ncurrent_stage: ...\ncore_values: ...\nlong_term_goals: ...\n"
            "communication_preferences: ...\nwork_style: ...\nrecent_changes: ...\n```"
        )
    elif mode == "enrich":
        display_name = target.stem.lstrip("@").replace("-", " ")
        system_prompt = (
            f"You are helping the user build a personality profile for {display_name}.\n"
            "Interview them — ask specific questions one at a time.\n\n"
            f"## Current Profile\n{body or '(almost empty)'}\n\n"
            "Topics: communication style, values, personality, triggers, relationship dynamics.\n\n"
            "When the user says 'done' or 'save', output:\n"
            "```profile\ncommunication_style: ...\nenergy: gives/drains/neutral\n"
            "values: ...\ntriggers_avoid: ...\npersonality_profile: ...\n"
            "what_they_care_about: ...\nour_relationship: ...\nthings_to_remember: ...\n```"
        )
    elif mode == "advisor":
        display_name = target.stem.lstrip("@").replace("-", " ")
        system_prompt = (
            f"You are a relationship advisor helping {user_name} prepare for conversations with {display_name}.\n\n"
            f"## {display_name}'s Profile\n{body or '(sparse)'}\n\n"
            f"## Recent Interactions\n{log_text}\n\n"
        )
        if user_profile:
            system_prompt += f"## About {user_name}\n{user_profile}\n\n"
        system_prompt += (
            "Help plan what to say, anticipate reactions, navigate tricky topics. "
            "Be direct, practical, bilingual."
        )
    elif is_self and era:
        system_prompt = (
            f"You are simulating {user_name} during '{era}'.\n\n"
            f"## {user_name}'s Full Profile\n{body}\n\n"
            f"Stay in character as {user_name} during '{era}'. You don't know what happens after.\n"
            "Speak naturally in the language that version would use. "
            "Be authentic to that stage's emotional state."
        )
    elif is_self:
        system_prompt = (
            f"You are {user_name}'s inner self — a reflective, honest inner voice.\n\n"
            f"## {user_name}'s Profile\n{body}\n\n"
            "Help him think through problems, challenge assumptions gently. "
            "Bilingual, reference his values and experiences."
        )
    else:
        display_name = target.stem.lstrip("@").replace("-", " ")

        def _fmt(val):
            return ", ".join(val) if isinstance(val, list) else str(val) if val else "not specified"

        system_prompt = (
            f"You are simulating a conversation as {display_name}.\n\n"
            f"## {display_name}'s Profile\n"
            f"Role: {_fmt(fm.get('title'))} at {_fmt(fm.get('company'))}. "
            f"Location: {_fmt(fm.get('location'))}.\n"
            f"Communication style: {_fmt(fm.get('communication_style'))}\n"
            f"Energy: {_fmt(fm.get('energy'))}\n"
            f"Shared interests: {_fmt(fm.get('shared_interests'))}\n\n"
        )
        if body:
            system_prompt += f"## Background\n{body}\n\n"
        system_prompt += f"## Recent Interactions\n{log_text}\n\n"
        if user_profile:
            system_prompt += f"## About {user_name}\n{user_profile}\n\n"
        system_prompt += (
            "Stay in character. Match their communication style. "
            "Respond naturally as this person would."
        )
        if len(body) < 200:
            system_prompt += "\nNote: sparse profile — be plausible but don't fabricate."

    # Build prompt with history
    context = system_prompt + "\n\n"
    for msg in history[-10:]:
        role = "User" if msg.get("role") == "user" else "Assistant"
        context += f"{role}: {msg['content']}\n\n"
    context += f"User: {message}\n\nAssistant:"

    try:
        result = await self.think(context, domain="text")
        return {"response": result, "mode": mode}
    except RuntimeError as e:
        if "No available provider for capability" in str(e):
            raise
        return {"error": f"AI unavailable: {e}"}
    except Exception as e:
        return {"error": f"AI unavailable: {e}"}


@web_route("POST", "/api/simulate/enrich-save")
async def api_enrich_save(self, request):
    """Save enrichment data from a ```profile block back to the contact note."""
    data = await request.json()
    name = data.get("name", "")
    profile_block = data.get("profile", "")

    if not name or not profile_block:
        return {"error": "name and profile required"}

    is_self = name.lower() in ("self", "_self", "__self__", "me")
    if is_self:
        target = self._me_file()
    else:
        target = self._find_file(name)

    if not target or not target.exists():
        return {"error": "file not found"}

    content = await self.read(str(target))

    # Parse profile block into key-value pairs
    updates = {}
    for line in profile_block.strip().split("\n"):
        if ":" in line:
            key, val = line.split(":", 1)
            key, val = key.strip(), val.strip()
            if key and val:
                updates[key] = val

    if not updates:
        return {"error": "no fields parsed from profile block"}

    era_val = updates.pop("era", "")

    if is_self and era_val:
        # Era-specific: append to ## 人生时间线 section
        era_heading = f"### {era_val}岁"
        era_lines = [f"- **{k.replace('_', ' ').title()}**: {v}" for k, v in updates.items()]
        era_block = f"\n{era_heading}\n" + "\n".join(era_lines)

        if "## 人生时间线" in content:
            idx = content.index("## 人生时间线")
            nl = content.index("\n", idx)
            rest = content[nl + 1 :]
            insert_pos = nl + 1
            for rline in rest.split("\n"):
                if rline.strip().startswith("## ") and not rline.strip().startswith("### "):
                    break
                insert_pos += len(rline) + 1
            content = content[:insert_pos] + era_block + "\n" + content[insert_pos:]
        else:
            content += f"\n\n## 人生时间线\n{era_block}\n"
    else:
        # Frontmatter fields to update
        fm_keys = {"communication_style", "energy"}
        fm = self._parse_frontmatter(content)
        fm_updated = False

        for key in fm_keys:
            if key in updates:
                fm[key] = updates.pop(key)
                fm_updated = True

        # Body sections to append
        section_map = {
            "personality_profile": "## Personality Profile",
            "values": "## Personality Profile",
            "triggers_avoid": "## Personality Profile",
            "what_they_care_about": "## What They Care About",
            "our_relationship": "## Our Relationship",
            "things_to_remember": "## Things to Remember",
        }
        if is_self:
            section_map = {
                "current_stage": "## 当前生活阶段",
                "core_values": "## 核心价值观",
                "long_term_goals": "## 长期目标",
                "communication_preferences": "## 沟通偏好",
                "work_style": "## 工作风格",
                "recent_changes": "## 最近变化",
                "things_learned": "## 自我认知",
            }

        # Rebuild frontmatter if changed
        if fm_updated:
            body = content
            if body.startswith("---"):
                end = body.find("---", 3)
                if end > 0:
                    body = body[end + 3 :]
            content = self._serialize_frontmatter(fm) + body

        # Append to body sections
        section_data = {}
        for key, section in section_map.items():
            if key in updates:
                label = key.replace("_", " ").title()
                line = f"- **{label}**: {updates[key]}"
                section_data.setdefault(section, []).append(line)

        for section_heading, lines in section_data.items():
            insert_text = "\n".join(lines)
            if section_heading in content:
                idx = content.index(section_heading) + len(section_heading)
                nl = content.index("\n", idx)
                rest = content[nl + 1 :]
                insert_pos = nl + 1
                for rline in rest.split("\n"):
                    if rline.strip().startswith("## "):
                        break
                    insert_pos += len(rline) + 1
                content = content[:insert_pos] + insert_text + "\n" + content[insert_pos:]
            else:
                content += f"\n\n{section_heading}\n{insert_text}\n"

    await self.write(str(target), content)
    await self.emit("contacts:enriched", {"name": name, "fields": list(updates.keys())})
    return {"ok": True, "fields_updated": list(updates.keys()), "era": era_val}


@web_route("POST", "/api/simulate/archive")
async def api_chat_archive(self, request):
    """Summarize a simulation conversation and save to Quick Log."""
    data = await request.json()
    name = data.get("name", "")
    history = data.get("history", [])
    mode = data.get("mode", "chat")

    if not name or not history:
        return {"error": "name and history required"}

    target = self._find_file(name)
    if not target or not target.exists():
        return {"error": "Contact not found"}

    display_name = target.stem.lstrip("@").replace("-", " ")

    # Build conversation for summarization
    user_name = self.require("settings").get("user.name", "User")
    lines = []
    for msg in history:
        speaker = user_name if msg.get("role") == "user" else display_name
        lines.append(f"{speaker}: {msg.get('content', '')}")
    convo_text = "\n".join(lines)

    mode_label = {
        "chat": "simulated conversation",
        "advisor": "advisor session",
        "enrich": "profile enrichment",
    }.get(mode, "chat")

    try:
        summary = await self.think(
            f"Mode: {mode_label}\nTranscript:\n{convo_text[:2000]}",
            system=SIMULATE_LOG_SUMMARY_SYSTEM,
            domain="text",
            temperature=0.4,
        )
        summary = summary.strip().strip('"').strip("'")
    except Exception:
        summary = f"{mode_label} with {display_name}"

    if not summary:
        summary = f"{mode_label} with {display_name}"

    # Append to Quick Log
    today = date.today().isoformat()
    entry = f"- {today}: [AI {mode_label}] {summary}"

    content = await self.read(str(target))
    if "## Quick Log" in content:
        idx = content.index("## Quick Log")
        end_of_line = content.index("\n", idx)
        content = content[: end_of_line + 1] + entry + "\n" + content[end_of_line + 1 :]
    else:
        content = content.rstrip() + "\n\n## Quick Log\n" + entry + "\n"

    await self.write(str(target), content)
    await self.emit("contacts:chat_archived", {"name": display_name, "mode": mode})
    return {"ok": True, "summary": summary}


# ---------------------------------------------------------------------------
# Profile Personality APIs — from vault personality assessment files
# ---------------------------------------------------------------------------


def _extract_table_rows(self, text: str, section_header: str) -> list[dict]:
    """Extract markdown table rows under a section header."""
    in_section = False
    headers = None
    rows = []
    for line in text.split("\n"):
        s = line.strip()
        if section_header in s and s.startswith("#"):
            in_section = True
            continue
        if in_section and s.startswith("#") and section_header not in s:
            break
        if not in_section:
            continue
        if "|" in s:
            cells = [c.strip() for c in s.split("|")[1:-1]]
            if not cells:
                continue
            if headers is None:
                headers = cells
                continue
            if all(c.replace("-", "").replace(":", "") == "" for c in cells):
                continue
            row = {h: cells[i] if i < len(cells) else "" for i, h in enumerate(headers)}
            rows.append(row)
    return rows


def _extract_section_bullets(self, text: str, header: str) -> list[str]:
    """Extract bullet points from a markdown section."""
    in_section = False
    items = []
    for line in text.split("\n"):
        s = line.strip()
        if header in s and s.startswith("#"):
            in_section = True
            continue
        if in_section and s.startswith("#"):
            break
        if in_section and s.startswith("- "):
            items.append(s[2:].strip())
    return items


def _strip_bold(self, s: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", s)


@web_route("GET", "/api/profile/personality")
async def api_personality(self, request):
    """MBTI, Big Five, DISC, Holland, Belbin from vault files."""
    ws_file = self.vault_config_path(
        "work_style", "20_Areas/Personal-Dev/Work-Style-Assessment.md"
    ) or Path(".")
    mbti_file = self.vault_config_path("mbti", "20_Areas/Personal-Dev/MBTI - INFJ.md") or Path(".")

    ws_text = ""
    mbti_text = ""
    try:
        ws_text = await self.read(str(ws_file))
    except Exception:
        pass
    try:
        mbti_text = await self.read(str(mbti_file))
    except Exception:
        pass

    # MBTI
    mbti_type = "INFJ-T"
    m = re.search(r"#\s+([A-Z]{4}(?:-[A-Z])?)\b", mbti_text)
    if m:
        mbti_type = m.group(1)

    # Big Five
    big_five = []
    for row in self._extract_table_rows(ws_text, "Big Five"):
        dim = self._strip_bold(row.get("维度", row.get("Dimension", "")))
        score = row.get("评分", row.get("Score", ""))
        if dim and score:
            m = re.search(r"\((\w+)\)", dim)
            dim_en = m.group(1) if m else dim
            nums = re.findall(r"\d+", score)
            avg = int(sum(int(n) for n in nums) / len(nums)) if nums else 0
            big_five.append({"dimension": dim_en, "score": avg})

    # DISC
    disc = []
    for row in self._extract_table_rows(ws_text, "DISC"):
        typ = self._strip_bold(row.get("类型", row.get("Type", "")))
        score = row.get("评分", row.get("Score", ""))
        if typ and score:
            nums = re.findall(r"\d+", score)
            avg = int(sum(int(n) for n in nums) / len(nums)) if nums else 0
            disc.append({"type": typ, "score": avg})

    # Holland
    holland = []
    for row in self._extract_table_rows(ws_text, "Holland"):
        typ = self._strip_bold(row.get("类型", row.get("Type", "")))
        score = row.get("评分", row.get("Score", ""))
        if typ and score:
            nums = re.findall(r"\d+", score)
            avg = int(sum(int(n) for n in nums) / len(nums)) if nums else 0
            holland.append({"type": typ, "score": avg})

    # Belbin
    belbin = []
    for row in self._extract_table_rows(ws_text, "Belbin"):
        role = self._strip_bold(row.get("角色", row.get("Role", "")))
        match = row.get("匹配度", row.get("Match", ""))
        if role:
            belbin.append({"role": role, "match": match})

    return {
        "mbti": mbti_type,
        "big_five": big_five,
        "disc": disc,
        "holland": holland,
        "belbin": belbin[:3],
    }


@web_route("GET", "/api/profile/values")
async def api_values(self, request):
    """Core values, life goals, life stage from _me.md."""
    try:
        text = await self.read(str(self._me_file()))
    except Exception:
        return {"values": [], "goals": [], "life_stage": ""}

    values = self._extract_section_bullets(text, "核心价值观")
    goals = self._extract_section_bullets(text, "长期目标")

    # Life stage
    life_stage = ""
    in_section = False
    for line in text.split("\n"):
        s = line.strip()
        if "当前生活阶段" in s and s.startswith("#"):
            in_section = True
            continue
        if in_section and "主要焦点" in s:
            life_stage = s.split(":", 1)[-1].split("：", 1)[-1].strip().strip("*")
            break

    return {"values": values, "goals": goals, "life_stage": life_stage}
