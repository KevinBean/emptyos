# EmptyOS App Specifications

> 12 core apps with deep backends, custom UIs, and rich data models.
> Source of truth: each app's `app.py` + `manifest.toml`.

---

## Expense

- **What it does**: Natural language expense tracking with vault-backed markdown tables, budget forecasting, recurring expenses, and AI spending insights.

- **Data**: Vault `20_Areas/Finances/expense-log-{YYYY}.md` (markdown table, source of truth). App state `data/apps/expense/state.json` (budget, presets, recurring rules).

- **API** (20 endpoints):

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/add | Add expense (amount, description, category) |
| POST | /api/smart-add | Parse natural language: "35 lunch coffee" or "50 dinner AA 2" |
| GET | /api/summary | Monthly totals + category breakdown |
| GET | /api/list | Expense list with optional month filter + limit |
| POST | /api/delete | Delete by matching date+amount+description |
| POST | /api/edit | Edit: delete original + add updated |
| GET | /api/budget | Get monthly budget target |
| POST | /api/budget | Set monthly budget |
| GET | /api/presets | Quick-log preset list |
| POST | /api/presets | Save presets |
| GET | /api/forecast | Month forecast: daily_avg x days_in_month, budget_pct, on_track |
| GET | /api/heatmap | Daily spending heatmap (last N months) |
| GET | /api/week-compare | This week vs last week: total, diff, pct_change |
| GET | /api/ai-insight | LLM analysis: 3 insights + 1 saving suggestion + health 1-10 |
| GET | /api/recurring | List recurring expense rules |
| POST | /api/recurring | Add recurring rule (text, frequency, enabled) |
| POST | /api/recurring/check | Auto-log due recurring expenses + advance next_due |
| GET | /api/export | CSV export for a month |
| POST | /api/import | CSV import with source tag |
| GET | /api/category-trend | Per-category this month vs last month |

- **Core algorithms**: Forecast = `total / days_elapsed * days_in_month`. AA split: "50 lunch AA 2" -> $25. Category auto-detect via keyword dictionary (7 categories, 80+ keywords). Recurring advance: weekly +7d, monthly +1m, yearly +1y.

- **Events**: Emits `expense:added`

- **Settings**: `expense.budget` (number, 3000), `expense.default_category` (select, Other), `expense.alert_threshold` (number, 80)

- **Status**: 20/21 HP endpoints. Missing: weekly-digest (folded into week-compare).

---

## Briefing

- **What it does**: Daily command center. Health score (5 dimensions x 20 = 100), weather, schedule sync from weekly plan, what-now decision engine, morning routine checklist, AI narrative.

- **Data**: Multi-source aggregation via `call_app()` to task, journal, english, expense, healing, nutrition, contacts, focus. Morning routine in app state. Weather cached from Open-Meteo API (free, no key).

- **API** (20 endpoints):

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/briefing | Full briefing: health, frogs, schedule, tasks, weather, what-now |
| GET | /api/frogs | Frog tasks (must-do items) status |
| GET | /api/health-score | 5-dimension score: journal, english, exercise, nutrition, tasks |
| GET | /api/what-now | Time-aware suggestion: morning routine -> nudge -> schedule -> default |
| GET | /api/weather | Open-Meteo 3-day forecast (30min cache) |
| GET | /api/schedule | Today's schedule from daily/weekly note |
| GET | /api/yesterday | Yesterday summary (entries, mood, tasks done) |
| GET | /api/upcoming | Upcoming deadlines from active projects |
| POST | /api/ai-summary | LLM narrative from all briefing data |
| GET | /api/daily-progress | Today's progress metrics |
| GET | /api/events | Today's calendar events |
| GET | /api/birthdays | Upcoming birthdays from contacts |
| GET | /api/nudge | Proactive nudge: what needs attention most |
| GET | /api/week-review | Week summary across apps |
| GET | /api/quote | Random motivational quote |
| GET | /api/morning | Morning routine checklist items |
| POST | /api/morning/toggle | Toggle a morning routine item |
| POST | /api/schedule-sync | Copy weekly plan schedule to daily note if empty |
| GET | /api/quarterly | 90-day review across all dimensions |
| GET | /api/tasks-due | Tasks due today/this week |

- **Core algorithms**: Health score: journal(20 if exists), english(min(20, min*20/30)), exercise(20 if no nudge), nutrition(20 if food today), tasks(min(20, done*5)). What-now: hour<10 -> morning routine, nudge.urgency>=10 -> nudge, next_event_in_30min -> prep, hour>=21 -> journal, else -> top task.

- **Events**: Emits `briefing:generated`

- **Settings**: `briefing.auto_weather` (toggle, true), `briefing.morning_time` (number, 10)

- **Status**: 20/24 HP endpoints. Missing: schedule-reset, morning advanced endpoints, birthdays filters.

---

## Journal

- **What it does**: Daily journaling with timestamped mood-tagged entries, milestone tracking, three-things gratitude, heatmap, mood trends, AI reflection, and search.

- **Data**: Vault `50_Journal/{YYYY}/{YYYY-MM-DD}.md`. Sections: `### Journal` (entries), `### Milestone`, `#### Three successful things`.

- **API** (11 endpoints):

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/today | Day's entries, milestone, three_things (supports ?date=) |
| POST | /api/entry | Add timestamped entry with mood emoji |
| GET | /api/recent | Last N days summary (entry count, dominant mood) |
| GET | /api/heatmap | Entry count per day for last N months |
| GET | /api/mood-trend | Daily mood score (1-5) over last N days |
| GET | /api/reflect | LLM-generated reflection prompts from recent activity |
| POST | /api/ai-reflect | LLM narrative reflection from last N days of entries |
| GET | /api/search | Full-text search across journal dates |
| GET | /api/templates | Available templates: morning, evening, weekly |
| POST | /api/milestone | Save milestone text |
| POST | /api/three-things | Save three successful things |

- **Core algorithms**: Entry format: `- **HH:MM** emoji text`. Mood map: great=5, good=4, okay=3, low=2, bad=1. Heatmap: scan N*30 days, count non-empty entries per date.

- **Events**: Emits `journal:entry`, `journal:created`

- **Settings**: `journal.default_mood` (select, good)

- **Status**: 11/9 HP endpoints (exceeds HP). Full feature parity.

---

## Task

- **What it does**: Vault-wide task scanner with decay tiers, focus scoring, calendar view, snooze, and toggle. Scans `10_Projects/`, `20_Areas/`, `50_Journal/`.

- **Data**: Vault markdown checkboxes (`- [ ] text 📅 YYYY-MM-DD`). Cache: `data/apps/task/task-index.json` (5min TTL).

- **API** (7 endpoints):

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/tasks | Open tasks (?status=done for completed) |
| GET | /api/list | Legacy endpoint (Task objects) |
| POST | /api/refresh | Force rebuild task index from vault scan |
| POST | /api/snooze | Snooze task by N days (file, line, days) |
| GET | /api/calendar | Tasks grouped by date |
| GET | /api/focus | Top 3 tasks by focus score |
| POST | /api/toggle | Toggle checkbox done/undone in vault file |

- **Core algorithms**: Decay tiers: fresh (<=7d), aging (7-30d), stale (30-90d), zombie (>90d). Focus score: today=50, this_week=30, overdue(<30d)=20+days, zombie=1. Context bonus: career/health/english +10.

- **Events**: Emits `task:added`, `task:completed`

- **Settings**: `task.zombie_days` (number, 90), `task.focus_top_n` (number, 3)

- **Status**: 7/7 HP endpoints. Full parity.

---

## English

- **What it does**: Learning hub that aggregates events from 5 voice/reading apps into a unified dashboard. 8-tier level system, achievements, pronunciation scoring, practice breakdown by source.

- **Data**: Vault `20_Areas/Speaking-Practice/practice-log.md` (table). Event aggregation from speaking, shadowing, voice-review, reader, interview-studio, dictionary, lessons.

- **API** (13 endpoints):

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/stats | Total hours, sessions, level, streak |
| GET | /api/dashboard | Full dashboard: stats + recent + level + achievements |
| GET | /api/heatmap | Practice days heatmap |
| GET | /api/vocab | Vocabulary stats from dictionary events |
| GET | /api/level | Current level + next threshold |
| GET | /api/activity | Recent practice activity feed |
| POST | /api/log | Log a practice session (minutes, type, source) |
| POST | /api/pronunciation-score | Word-level comparison: target vs spoken |
| GET | /api/target | Daily practice target |
| POST | /api/target | Set daily target |
| GET | /api/analytics | Detailed analytics: by type, by day, trends |
| GET | /api/achievements | Achievement badges and milestones |
| GET | /api/breakdown | Practice breakdown by source app |

- **Core algorithms**: Levels: 0h Beginner, 5h Elementary, 15h Pre-Intermediate, 30h Intermediate, 60h Upper-Intermediate, 100h Advanced, 200h Proficient, 500h Fluent. Pronunciation: word-level match count / target words * 100.

- **Events**: Emits `english:level_up`. Listens to 11 event types from 7 apps.

- **Settings**: `english.target_hours` (number, 1.0)

- **Status**: 13/7 HP endpoints (186%). Backend exceeds HP.

---

## Hub

- **What it does**: Aggregated life dashboard. Health score ring, what-now suggestion, countdowns, goals, weather. Calls 10 other apps via `call_app()`.

- **Data**: No own data. Aggregates from expense, english, briefing, contacts, journal, task, tracker, healing, nutrition, focus.

- **API** (7 endpoints):

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/widgets | All widget data in one call |
| GET | /api/health-score | 5-dimension health ring |
| GET | /api/what-now | Single actionable suggestion |
| GET | /api/countdowns | Visa, PR, project deadlines |
| GET | /api/goals | Life goals progress (PR, savings, english, career) |
| GET | /api/weather | Weather passthrough from briefing |
| GET | /api/today | Today's activity summary |

- **Core algorithms**: Hub is the most-connected node in the topology graph. Widgets refresh on 19 event types from across the system.

- **Events**: Emits `hub:refreshed`. Listens to 19 event types.

- **Settings**: None (inherits from source apps)

- **Status**: 7/6 HP endpoints (117%). Intended as home screen (`/` -> `/hub/`).

---

## Contacts

- **What it does**: CRM from vault `@Person.md` files. Health scoring, contact frequency tracking, AI suggestions, persona chat, quick logging.

- **Data**: Vault `30_Resources/People/@*.md` (frontmatter: relationship, contact_frequency, last_contact, birthday, trust_level, energy, phone, email, linkedin, wechat). Quick Log section in each file.

- **API** (15 endpoints):

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/list | All contacts (legacy) |
| GET | /api/contacts | All contacts with filters |
| GET | /api/search | Search by name/relationship/tags |
| GET | /api/detail/{name} | Full contact with parsed frontmatter + body |
| POST | /api/log | Add quick log entry to a contact |
| GET | /api/frequency | Contact frequency analysis |
| GET | /api/due | Overdue contacts (days > frequency * 1.5) |
| GET | /api/notifications | Birthday + overdue alerts |
| POST | /api/ai-suggest | LLM suggests who to contact and why |
| GET | /api/stats | Contact count, relationship distribution |
| POST | /api/chat | Chat with/about a contact (LLM with persona context) |
| GET | /api/persona/{name} | Contact's personality profile |
| GET | /api/profile/{name} | Full profile: frontmatter + log + backlinks |
| POST | /api/create | Create new @Person.md |
| POST | /api/edit/{name} | Edit contact frontmatter |

- **Core algorithms**: Health score = 50 + trust*5 + recency_bonus(+20/+10/0/-10/-25) + energy_bonus(+10/-10/0). Clamped 0-100. Overdue = days_since > frequency_days * 1.5.

- **Events**: Emits `contacts:logged`

- **Settings**: `contacts.overdue_alert` (toggle, true), `contacts.birthday_alert_days` (number, 7)

- **Status**: 15/19 HP endpoints (79%). Missing: advanced profile views, bulk operations.

---

## Healing

- **What it does**: Mood tracking with energy levels, emotional correlations, care alerts, dream journaling with AI interpretation, grounding exercises.

- **Data**: Vault `20_Areas/Health/mood-log.md` (markdown table: Date|Time|Energy|Tags|Note). Dream files in vault. App state `healing-memories.json`.

- **API** (13 endpoints):

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/mood | Log mood (energy 1-10, tags, note) |
| GET | /api/trend | Mood trend over last N days |
| GET | /api/insight | AI analysis of mood patterns |
| GET | /api/history | Full mood history |
| GET | /api/streak | Consecutive logging days |
| GET | /api/care-check | Low energy alert: avg<=4 or 3-day streak<=4 |
| GET | /api/correlations | Per-tag average energy analysis |
| GET | /api/mood-calendar | Month grid with color-coded energy |
| GET | /api/export | Export mood data |
| POST | /api/dream | Log a dream |
| GET | /api/dreams | Dream journal list |
| POST | /api/dream-interpret | LLM dream interpretation |
| POST | /api/grounding | Guided grounding exercise |

- **Core algorithms**: Care-check: avg(last_3_days) <= 4 OR all 3 consecutive <= 4 -> needs_care. Correlations: per-tag avg energy, direction: positive(>=6), negative(<=4), neutral.

- **Events**: Emits `healing:mood-logged`

- **Settings**: `healing.care_threshold` (number, 4), `healing.mood_reminder` (toggle, true)

- **Status**: 13/15 HP endpoints (87%). Missing: AI companion, breathing exercises.

---

## Nutrition

- **What it does**: Meal tracking with LLM-powered calorie estimation, food database, macro progress rings, favorites, water tracking, meal planning, shopping lists.

- **Data**: App-local `data/apps/nutrition/` (nutrition.json, food-database.json, favorites.json, water.json). Targets in state.

- **API** (25 endpoints):

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/today | Today's meals with macro totals |
| POST | /api/smart-add | LLM parses natural language into food items with macros |
| POST | /api/log | Manual food logging |
| GET | /api/targets | Macro targets (calories, protein, carbs, fat) |
| POST | /api/targets | Set macro targets |
| GET | /api/suggestion | Remaining macros -> food suggestions |
| GET | /api/streak | Consecutive logging days |
| POST | /api/delete | Delete a food item |
| POST | /api/copy-day | Copy yesterday's meals to today |
| GET | /api/weekly-stats | 7-day summary with averages |
| GET | /api/food-db | Custom food database list |
| POST | /api/food-db | Add to food database |
| GET | /api/food-db/suggest | Autocomplete from food database |
| GET | /api/calorie-ranking | Top foods by average calories |
| GET | /api/protein-streak | Consecutive days hitting protein target |
| GET | /api/water | Today's water intake (0-8 glasses) |
| POST | /api/water | Log water |
| GET | /api/favorites | Saved meal favorites |
| POST | /api/favorites | Save a favorite meal |
| POST | /api/edit | Edit a logged food item |
| GET | /api/recent-foods | Recently logged foods |
| POST | /api/smart-log | Smart-add alias |
| GET | /api/plan | Weekly meal plan |
| GET | /api/shopping-list | Generate shopping list from meal plan |
| GET | /api/daily/{day} | Specific day's meals |

- **Core algorithms**: Smart-add pipeline: exact food-db match -> LLM parse (returns JSON array, multi-food split) -> merge results -> write. Suggestion: remaining = target - actual, if protein > 30 -> suggest high protein, if calories < 300 -> suggest light snack, plus food-db matches.

- **Events**: Emits `nutrition:logged`

- **Settings**: `nutrition.calories` (number, 2000), `nutrition.protein` (number, 80), `nutrition.carbs` (number, 250), `nutrition.fat` (number, 65)

- **Status**: 25/31 HP endpoints (81%). Missing: recipes CRUD, advanced shopping list.

---

## Focus

- **What it does**: Pomodoro timer with task suggestions, session history, achievement tiers, heatmap, daily goals, configurable timers.

- **Data**: App-local `data/apps/focus/` (sessions.json, max 500). Config in app state.

- **API** (10 endpoints):

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/suggest | AI task suggestion for next session |
| POST | /api/complete | Log completed focus session |
| GET | /api/stats | Total sessions, today count, streak |
| GET | /api/history | Recent sessions with tasks |
| GET | /api/heatmap | Sessions per day heatmap |
| GET | /api/goal | Daily goal and progress |
| POST | /api/goal | Set daily goal |
| GET | /api/achievements | Milestone tiers: 10/25/50/100/250/500/1000 |
| GET | /api/config | Timer config (work/break/long break) |
| POST | /api/config | Update timer config |

- **Core algorithms**: Achievement tiers: [10, 25, 50, 100, 250, 500, 1000] with emojis. Long break trigger: todayCount % longBreakEvery == 0. Ambient noise: Web Audio API (brown/pink/white noise, frontend).

- **Events**: Emits `focus:completed`

- **Settings**: `focus.daily_goal` (number, 4), `focus.work_min` (number, 25), `focus.break_min` (number, 5), `focus.long_break_min` (number, 15)

- **Status**: 10/7 HP endpoints (143%). Exceeds HP.

---

## Dictionary

- **What it does**: English-Chinese word lookup, vault storage, SM-2 SRS flashcards, quiz mode, word-of-day, frequency tracking, explain, spelling suggestions.

- **Data**: Vault `30_Resources/Learning/Dictionary/{Word} (en-US).md`. App-local `data/apps/dictionary/` (srs.json, frequency.json). External APIs: Free Dictionary, Datamuse, MyMemory Translation.

- **API** (15 endpoints):

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/lookup | Look up word (external API + vault check) |
| POST | /api/save | Save word to vault as markdown note |
| GET | /api/vault | All saved vault words |
| GET | /api/vault/{word} | Single vault word content |
| POST | /api/favorite | Toggle word favorite |
| GET | /api/suggest | Autocomplete via Datamuse |
| GET | /api/word-of-day | Random word from vault |
| GET | /api/srs/deck | Build review deck: due(level asc) + new(shuffled) |
| POST | /api/srs/review | Review word (quality 1-4) -> update level/streak |
| GET | /api/srs/stats | SRS overview: total, due, mastered, by-level |
| GET | /api/frequency | Word lookup frequency data |
| GET | /api/explain | LLM explanation with examples |
| POST | /api/didyoumean | Spelling correction via Datamuse |
| DELETE | /api/vault/{word} | Delete a vault word |
| GET | /api/quiz | Generate multiple-choice quiz |

- **Core algorithms**: SM-2 simplified: intervals = [0, 1, 3, 7, 14, 30, 60, 120] days. Quality 1=reset(level 0), 2=no advance, 3=+1 level, 4=+2 levels. Deck: due words (level asc) + new words (shuffled), up to limit. Quiz: random vault words, 1 correct + 3 distractors.

- **Events**: Emits `dictionary:word_saved`, `dictionary:word_reviewed`

- **Settings**: `dictionary.srs_new_per_day` (number, 5), `dictionary.quiz_count` (number, 5)

- **Status**: 15/16 HP endpoints (94%). Missing: dedicated delete-by-id endpoint.

---

## Projects

- **What it does**: Project portfolio scanner. Reads `10_Projects/*.md` frontmatter, infers status, tracks tasks per project, kanban view, AI health assessment.

- **Data**: Vault `10_Projects/*.md` (frontmatter: status, created, deadline, tags). 30-second cache.

- **API** (9 endpoints):

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/list | All projects (legacy) |
| GET | /api/projects | All projects with status/task counts |
| GET | /api/projects/{id} | Single project detail |
| POST | /api/refresh | Force rescan vault |
| POST | /api/projects/{id}/status | Update project status |
| POST | /api/projects/{id}/tasks/toggle | Toggle a task in project file |
| POST | /api/projects/{id}/tasks/add | Add task to project file |
| GET | /api/projects/{id}/health | AI health assessment |
| POST | /api/create | Create new project file |

- **Core algorithms**: Status inference: explicit frontmatter -> keyword scan ("completed"/"done") -> 90+ days no modification -> suggest shelved -> default: active. Lifecycle: idea -> active -> completed -> archived, with blocked/shelved branches.

- **Events**: Emits `projects:refreshed`, `projects:status_changed`, `projects:task_toggled`, `projects:task_added`, `projects:created`

- **Settings**: `projects.stale_days` (number, 90)

- **Status**: 9/8 HP endpoints (112%). Full parity.
