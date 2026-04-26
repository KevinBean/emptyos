---
name: journal-planner
description: Manage periodic notes (daily/weekly/monthly/yearly) - journaling, planning, reviews, time-based organization
---

# Journal & Planner

Comprehensive skill for all time-based notes: daily, weekly, monthly, yearly.

## File Locations

| Type | Path Pattern | Template | Naming |
|------|--------------|----------|--------|
| Daily | `50_Journal/YYYY/YYYY-MM-DD.md` | `template/daily.md` | `2026-01-05.md` |
| Weekly | `50_Journal/YYYY/YYYY-WNN.md` | (see below) | `2026-W02.md` |
| Monthly | `50_Journal/YYYY/YYYY-MM.md` | `template/monthly.md` | `2026-01.md` |
| Yearly | `50_Journal/YYYY/YYYY.md` | (manual) | `2026.md` |

---

## Hierarchy

```
YYYY.md (年度目标 + 季度重点)
  └── YYYY-MM.md (月度计划 + 生活领域)
        ├── YYYY-WNN.md (周计划 + 每日安排)
        │     └── YYYY-MM-DD.md (每日日志)
        └── Weekly Reviews 区块 (链接到周笔记)
```

---

## Daily Notes

### Structure

```markdown
---
weight:
---

 [[prev|prev <]]  |  YYYY-MM-DD  |   [[next|> next]]

### Milestone
-

### Journal
- Activity 1
- [[Person]] (platform)

#### Three successful things today
1.
2.
3.

### Day Planner & Log
[time-blocked task queries]

### Due this week
[task query]
```

### Quick Journal Entry

**User says**: "journal today" / "I did X, Y, Z"

**Workflow**:
1. Create/open `50_Journal/YYYY/YYYY-MM-DD.md`
2. Fill Journal section with activities
3. Link people: `[[@Person]]` + (platform)
4. Infer Three Successful Things
5. Add Reflection if appropriate

### Activity Formatting

| Type | Format |
|------|--------|
| General | `- Went to library` |
| Person | `- Talked with [[@Person]] (phone/WeChat/IG)` |
| Place | `- Went to [[Place Name]]` |
| Creative | `- Produced songs` |
| Blocker | `- Blocker: description` |

---

## Weekly Notes

### Structure

```markdown
[[YYYY-MM]]

# WNN: Mon DD - Sun DD (主题)

---

## Schedule

| Day | Date | 计划 |
|-----|------|------|
| Mon | [[YYYY-MM-DD]] | 事件 + **时间 活动 @ [[地点]]** |
| Tue | [[YYYY-MM-DD]] | ... |
| Wed | [[YYYY-MM-DD]] | ... |
| Thu | [[YYYY-MM-DD]] | ... |
| Fri | [[YYYY-MM-DD]] | ... |
| Sat | [[YYYY-MM-DD]] | ... |
| Sun | [[YYYY-MM-DD]] | 休息 |

---

## Focus

**主题**: 本周重点

**重点任务:**
- [ ] Task 1 📅 YYYY-MM-DD
- [ ] Task 2
- [ ] Task 3

---

## Tasks

``` tasks
not done
path does not include template
filename does not include kanban
due after [week start - 1]
due before [week end + 1]
```

---

## Review

**完成:**
-

**未完成:**
-

**下周改进:**
-
```

### Weekly Planning

**User says**: "plan next week" / "周计划"

**Workflow**:
1. Determine week number (W01, W02, etc.)
2. Create `50_Journal/YYYY/YYYY-WNN.md`
3. Fill Schedule table with:
   - Fixed events (运动、会议)
   - Key tasks by day
   - Links to daily notes and places
4. Set Focus section with week theme and key tasks
5. Link from monthly note's Weekly Reviews section

### Weekly Review

**User says**: "weekly review" / "这周怎么样"

**Workflow**:
1. Open current week's note
2. Check Schedule - what was done vs planned
3. Fill Review section:
   - 完成: List completed items
   - 未完成: List incomplete items
   - 下周改进: Lessons learned
4. Update monthly note's Hours Log if applicable

---

## Monthly Notes

### Structure

```markdown
[[prev-month]] [[YYYY]] [[next-month]]

---

## Theme: [月度主题]

---

## Immigration / Study / Work / Health / Wealth / Relationship
[各领域目标和任务]

---

## Weekly Reviews

### [[YYYY-W01]] (dates)
简要总结 + 链接

### [[YYYY-W02]] (dates)
...

---

## Due this month
[task query]

## Review
- [ ] Monthly review 📅 YYYY-MM-末
```

### Monthly Planning

**User says**: "plan this month" / "月度计划"

**Workflow**:
1. Open/create monthly note
2. Review yearly goals for context
3. Set month theme
4. Update life area sections with goals
5. Add key tasks with due dates

### Monthly Review

**User says**: "monthly review"

**Workflow**:
1. Review all weekly notes from that month
2. Summarize by life area
3. Score each area (完成度)
4. Update Hours Log totals
5. Note progress toward yearly goals

---

## Yearly Notes

### Structure

Key sections:
- **Theme**: Year's guiding philosophy
- **Critical Deadlines**: Key dates
- **Life Areas**: Immigration, Study, Work, Health, Wealth, Relationship
- **Quarterly Focus**: Q1/Q2/Q3/Q4 priorities
- **Key Projects**: Links to `10_Projects/`
- **Risks & Mitigations**: What could go wrong

See `50_Journal/2026/2026.md` for example.

---

## Quick Commands

| User Says | Claude Does |
|-----------|-------------|
| "journal today" | Create/update daily note |
| "plan next week" | Create weekly note with schedule |
| "weekly review" | Summarize and review the week |
| "plan this month" | Create/update monthly note |
| "monthly review" | Summarize month by life area |
| "year in review" | Comprehensive yearly analysis |
| "what did I do on [date]" | Read that daily note |

---

## Cross-Cutting Workflows

### Add Event to Calendar

**User says**: "周一 5:30pm Zumba at Chatswood"

**Workflow**:
1. Identify: day, time, activity, location
2. Add to weekly note's Schedule table
3. Create/link Place note if new location
4. Add recurring pattern if applicable

### Log Person Interaction

When person mentioned:
1. Link: `[[@Person]]` + (platform)
2. Consider updating `last_contact` in person note
3. Add to daily note's Journal section

### Navigate Time

| User Says | Action |
|-----------|--------|
| "yesterday" | Previous day's note |
| "last week" | Previous week's note |
| "this week" | Current week's note |
| "next week" | Create/open next week's note |

---

## Entity Detection

When processing entries, detect and offer to create notes:

| Entity | Trigger | Routes To |
|--------|---------|-----------|
| Person | "talked to X" | → note-factory → people-manager |
| Place | "went to X" | → note-factory |
| Book | "reading X" | → note-factory → media-library |
| Movie | "watched X" | → note-factory → media-library |

---

## Task Management System

### Time Blocks vs Tasks (重要区分)

这个 vault 中有两种不同的"待办"概念：

| 概念 | 格式 | 位置 | 用途 |
|------|------|------|------|
| **时间块** | 表格行 | 周计划 Schedule | 日程安排：什么时间做什么 |
| **任务** | `- [ ]` | Focus / 月度 / Tracker | 交付物：必须完成的事 |

**时间块** (Schedule)：
```markdown
| 17:30-19:00 | 🏃 Zumba @ [[Fitness First]] |
| 19:30-20:30 | 📚 English (1h) |
```
- 是**计划**，不是承诺
- 可以灵活调整
- 不需要打勾完成
- 每日日志记录实际发生了什么

**任务** (Focus / Tasks)：
```markdown
- [ ] 3x Zumba (Mon/Tue/Sat)
- [ ] Video #3 📅 2026-01-07
```
- 是**交付物**，需要追踪完成
- 有明确的完成/未完成状态
- 被 task-aggregator 处理
- 周 Review 对比完成情况

**追踪闭环**：
```
周计划 Schedule ──→ 每日执行 ──→ 日志记录
       ↓                            ↓
Focus 任务 ────────────────→ 周 Review (完成/未完成)
```

**为什么分开？**
1. 时间块是"计划视图"，任务是"结果视图"
2. 避免过度追踪（不用给每个时间块打勾）
3. Focus 任务已经捕获关键结果（如 "3x Zumba" 代表整周目标）

### Task Architecture

```
10_Projects/
├── 189-Visa-Tracker.md      ← 移民相关任务
├── Job-Search-Tracker.md    ← 求职相关任务
├── YouTube-Music-Channel/   ← 创作相关任务
└── [other projects]

50_Journal/YYYY/
├── YYYY.md                  ← 年度目标 + 季度重点
├── YYYY-MM.md               ← 月度任务 (按生活领域) + 链接到 Trackers
├── YYYY-WNN.md              ← 周 Focus 任务 + 时间块计划
└── YYYY-MM-DD.md            ← 每日执行日志
```

### Task Locations

| Task Type | Where to Put | Example |
|-----------|--------------|---------|
| 年度目标 | `YYYY.md` | `- [ ] Receive 189 visa grant 📅 2026-07-01` |
| 月度任务 | `YYYY-MM.md` 各领域区块 | `- [ ] Apply to 3-5 new roles 📅 2026-01-31` |
| 周重点 | `YYYY-WNN.md` Focus 区块 | `- [ ] Video #3 📅 2026-01-07` |
| 项目任务 | `10_Projects/[Tracker].md` | 详细追踪、checklist |
| 每日执行 | 时间块在周计划，日志在日记 | `17:30-19:00 Zumba` |

### Task Syntax

```markdown
- [ ] 普通任务
- [ ] 带日期任务 📅 2026-01-15
- [ ] 重复任务 🔁 every week
- [ ] 重复+日期 🔁 every week 📅 2026-01-10
- [x] 完成任务 ✅ 2026-01-04
```

### Task Flow (Cascade Down)

1. **年度目标** → 定义方向和里程碑
2. **月度任务** → 分解为可执行的月度 deliverables
3. **周 Focus** → 本周必须完成的关键任务
4. **时间块** → 具体到每天什么时间做什么
5. **每日日志** → 记录实际完成情况

### Project Tracker Integration

月度/年度笔记通过 `See: [[Tracker]]` 链接到项目追踪器：

```markdown
## Immigration

See: [[189-Visa-Tracker]] for detailed tracking.

- [ ] Check immiaccount weekly 🔁 every week
- [ ] 确认体检有效期 📅 2026-01-05
```

**原则**：
- 月度笔记放 **概览任务** + 链接
- Tracker 放 **详细追踪** (材料清单、申请表格、checklist)

### Recurring Task Patterns

| Pattern | Meaning | Example |
|---------|---------|---------|
| `🔁 every day` | 每日 | English practice |
| `🔁 every week` | 每周 | Check immiaccount |
| `🔁 every month` | 每月 | Weight trend review |

### Dataview Task Queries

**周笔记 - 本周到期任务：**
```tasks
not done
path does not include template
filename does not include kanban
due after 2026-01-04
due before 2026-01-12
```

**月笔记 - 本月到期任务：**
```tasks
not done
path does not include template
(due after 2025-12-31) AND (due before 2026-02-01)
```

### Quick Task Commands

| User Says | Claude Does |
|-----------|-------------|
| "add task [X] due [date]" | Add to appropriate level (week/month) |
| "what's due this week" | Check weekly note's Tasks query |
| "move task to next week" | Update 📅 date |
| "task done" | Mark `[x]` + add ✅ date |

---

## Events Integration

Events (重要多人事件) 与日记系统的联动：

| 方向 | 实现 |
|------|------|
| Event → 日记 | Event 模板自动链接到日期 `[[YYYY-MM-DD]]` |
| 日记 → Events | 日/周模板 Dataview 查询显示相关 Events |
| People → Events | People 模板自动显示涉及该人的 Events |

**相关文件:**
- Event 模板: `30_Resources/Technology/template/@ for event.md`
- Events 存放: `Timestamps/Events/`
- Events MOC: `20_Areas/_Events MOC.md`

---

## Obsidian CLI (Quick Journal Queries) — with Fallbacks

```bash
OBS="bash 99_Attachments/scripts/obs.sh"

# Tasks from today's daily note
$OBS tasks todo daily
# FALLBACK: Read today's daily note, extract '- [ ]' lines

# Tasks due this week (from weekly note)
$OBS tasks todo "file=2026-W14"
# FALLBACK: Grep for '- \[ \]' in the weekly note file

# Read today's daily note content
$OBS "daily:read"
# FALLBACK: Read 50_Journal/YYYY/YYYY-MM-DD.md directly

# Search journal for a topic
$OBS "search:context" "query=career pivot" path=50_Journal limit=5
# FALLBACK: Grep across 50_Journal/**/*.md

# What's due today (frontmatter property check)
$OBS tasks todo total
# FALLBACK: python "99_Attachments/scripts/task-manager.py" --today
```

## Integration

- **note-factory**: Routes entity creation with pre-checks
- **people-manager**: Update `last_contact` for interactions
- **media-library**: Book/movie note templates
- **Project Trackers**: `189-Visa-Tracker`, `Job-Search-Tracker` for detailed tracking
- **Events**: 重要多人事件自动显示在日/周记和相关人物笔记
