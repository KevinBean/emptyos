# Periodic Note Structure Templates

Structure skeletons for daily / weekly / monthly / yearly notes. Copy the relevant block when creating or filling a note.

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
