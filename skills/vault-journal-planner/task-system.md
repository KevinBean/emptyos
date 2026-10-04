# Task Management System

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
