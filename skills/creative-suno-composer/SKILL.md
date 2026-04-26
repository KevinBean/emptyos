---
name: suno-composer
description: Help create Suno AI music - minimal prompts, maximum creativity
---

# Suno AI Composer

歌曲创作工作流。**核心原则：极简 Prompt，后置 Review。**

## Reference

- **Style Guide**: `30_Resources/Technology/Suno AI 曲风参考.md`
- **制作经验**: `30_Resources/Technology/洞察疗愈专辑制作经验.md`
- **Healing Songs**: `20_Areas/Health/inner child healing songs.md`, `20_Areas/Health/insight healing songs.md`

---

## Quick Start

### 触发词

| User Says | Action |
|-----------|--------|
| "写首歌" / "create a song" | 极简 Prompt 生成 |
| "疗愈歌曲" / "healing song" | 加一句洞察描述 |
| "推荐曲风" / "what style" | Style Prompt 推荐 |
| "优化歌词" / "polish lyrics" | openai_exec 优化 |
| "优化歌名" / "song title" | 歌名生成 |

### 核心原则

| 原则 | 说明 |
|------|------|
| **Prompt 极简** | 只给灵感，其他交给 AI |
| **语言不限** | 中文、英文、混合都行 |
| **曲风可混** | 不强制单一风格 |
| **无人设** | 不加「专家」标签 |
| **规则后置** | 检查在生成后，不在 prompt 中 |

---

## 默认极简 Prompt

**必须通过 Bash 调用 openai_exec**（不要用 Task tool）：

```bash
python openai_exec.py "写一首歌《歌名》。

[一句话灵感]"
```

### 示例

**最简形式**：
```bash
python openai_exec.py "写一首歌《蓝》。

关于那种深深的、静静的悲伤，像海一样。"
```

**加一点方向**（可选）：
```bash
python openai_exec.py "写一首歌《玻璃墙》。

关于在别人情绪风暴中保护自己。想要画面感，不要说教。"
```

**疗愈歌曲**：
```bash
python openai_exec.py "写一首歌《习惯》。

洞察：我不需要证明自己值得被爱。
旧信念 → 新信念：我必须努力才能被爱 → 我本来就值得。"
```

### 不要在 Prompt 中加的东西

| 删除 | 原因 |
|------|------|
| ❌ "你是华语歌词创作者" | 限定思维框架 |
| ❌ "每行7-10字" | 生成后检查即可 |
| ❌ "押韵用 -ang" | 让 AI 自由选择 |
| ❌ "参考周杰伦风格" | 除非用户主动要求 |
| ❌ "不要说教" | 生成后 review 时修 |
| ❌ 指定语言（中文/英文） | 让 AI 根据主题自然选择 |

---

## 工作流

```
┌─────────────────────────────────────────────────────────────┐
│  1. 收集灵感（一句话）                                        │
│     └─ 不需要详细描述，简单即可                               │
└──────────────────────┬──────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────────┐
│  2. OpenAI 生成初稿（极简 Prompt）                            │
│     └─ python openai_exec.py "写一首歌《X》。[灵感]"         │
└──────────────────────┬──────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────────┐
│  3. Claude Review（用下方清单）                              │
│     └─ 检查自然感、可唱性、情绪传达                          │
└──────────────────────┬──────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────────┐
│  4. OpenAI 优化（1-2轮，针对具体问题）                       │
│     └─ 提供：当前版本 + 问题列表                             │
└──────────────────────┬──────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────────┐
│  5. 生成 Style Prompt + 保存笔记                             │
│     └─ 主题、Style Prompt、歌词、production notes            │
└─────────────────────────────────────────────────────────────┘
```

---

## 生成后 Review

> **以下清单用于生成后检查，不要放入 Prompt！**

### 通用检查项

- [ ] **自然感**：朗读一遍，听起来像人话吗？
- [ ] **可唱性**：有气口吗？节奏顺吗？
- [ ] **情绪传达**：情绪旅程完整吗？
- [ ] **字数/音节**：中文 7-10字 / 英文 6-10 syllables（超过12就要拆）
- [ ] **押韵**：主韵统一吗？（90%+ 同韵）

### 陷阱警告

#### 语序扭曲（最常见问题）

很多「文艺」歌词把「诗意」理解成「打乱语序」，导致：
- 唱起来不顺口
- 听起来像翻译腔
- 失去情感冲击力

| 问题歌词 | 修正版本 | 问题分析 |
|----------|----------|---------|
| 咖啡杯雾气模糊你 | 咖啡杯的雾 模糊了你 | 缺少虚词 |
| 童年油彩晕开我的名 | 小时候画脸谱 画着画着忘了自己 | 意象堆叠 |
| 初见眼睛躲开光 | 初见时 你眼睛躲开光 | 缺主语 |

**自然感检查**：
1. 不看字，单凭听觉能听懂吗？
2. 「的」「了」「着」该有的有吗？
3. 这一行有几个独立意象？（超过1个就警惕）
4. 有动词带动节奏吗？

#### 说教/直白（Pop 风格要避免）

| 避免 | 改用 |
|------|------|
| "我学会了..." | 用场景暗示 |
| "原来..." | 用画面展示 |
| "没关系" | 用行动表达 |

#### 意象过密（Artistic 风格要注意）

每行最多一个核心意象，用动词/场景带过其他元素。

**参考标杆**（《玻璃墙》）：
```
以前你情绪砸落像雨飘   ← 语序正常，一个比喻
我像窗纱无力只让雾飘   ← 语序正常，一个比喻
每滴雨滑过把心浇到老   ← 动词带动（滑过、浇）
```

---

## 三种风格参考

保留作为理解参考，但**不影响 Prompt 生成**：

| 维度 | Healing 治愈 | Pop 流行 | Artistic 艺术 |
|------|-------------|----------|--------------|
| **核心目标** | 疗愈效果 | 传唱度 | 艺术表达 |
| **表达方式** | 直接说出洞察 | 画面叙事 | 意象隐喻 |
| **语言风格** | 温暖、肯定 | 口语、自然 | 诗意（但语序仍需自然） |
| **副歌特点** | 重复核心信念 | catchy hook | 升华意象 |

### 同一主题「允许自己慢」三种表达

**Healing**：慢一点 没关系的 / 你不用追赶任何人

**Pop**：窗外的雨落得很慢 / 茶杯里还有余温 / 不着急收拾昨天的梦

**Artistic**：芭蕉听雨 一滴一滴 / 都是时间的脚印

---

## OpenAI 优化 Prompt

当需要修改时：

```bash
python openai_exec.py "优化以下歌词：

[当前版本]

问题：
1. 第三行语序不自然
2. 副歌不够 catchy
3. Bridge 意象太密

保持原有情感，修复这些问题。"
```

---

## Production Direction Notes

每个 section tag 后加 `(production direction)` 指导 Suno 音频生成。

```
[Verse 1]
(mid-tempo groove with piano lead, crisp drums, shimmering guitars)
Lyrics here...

[Chorus]
(full band lift; pounding drums, handclaps, stacked vocals)
Hook line here...
```

**要点**：描述乐器、能量、动态、氛围。简洁但有画面感。

---

## Style Prompt 生成

### 三层架构

```
[曲风] | [情绪+乐器] | [人声+制作]
```

示例：
```
Ambient dream pop, 78 BPM | warm, introspective, Rhodes piano, soft pads | female vocal, whispered verses, lo-fi production
```

### 曲风速查

| 情绪 | 推荐曲风 | BPM |
|------|----------|-----|
| 温暖治愈 | Ambient dream pop | 70-85 |
| 轻松释然 | Lo-fi indie | 80-95 |
| 坚定力量 | Indie pop/rock | 95-110 |
| 忧郁内省 | Sad indie | 65-80 |

---

## 歌名生成

```bash
python openai_exec.py "为这首歌生成 5 个歌名。

主题：[主题描述]
情绪：[情绪描述]

要求：2-5字，有画面感，避免俗套词（勇敢、梦想、相信）。"
```

**好歌名标准**：简短有力、有画面感、有情绪钩子、易记易传。

---

## 笔记记录格式

```markdown
## 歌名（洞察X：主题）

**主题**: 一句话概括
**曲风/Style**: `完整 Style Prompt`

### 歌词

[完整歌词含 production notes]

### 使用说明

**这首歌治愈什么？** [描述]
**什么时候听？** [场景]
```

---

## 专辑管理

状态符号：⏳待创作 | 🔄进行中 | ✅已完成

同一专辑建议统一 BPM 范围、主曲风、押韵倾向。

---

## Troubleshooting

| 问题 | 解决 |
|------|------|
| 歌词太诗意/翻译腔 | 检查语序、虚词、意象密度 |
| 押韵混乱 | 优化时指定主韵 |
| 行太长 | 让 openai_exec 拆分 |
| 不够 catchy | 强调 hook 需要重复 |

---

## Related

- YouTube Channel: `youtube-channel` skill
- Style Reference: `30_Resources/Technology/Suno AI 曲风参考.md`

---

<details>
<summary>Archive: 完整版 Prompts（点击展开）</summary>

### 中文歌词规范（历史参考）

| 项目 | 规范 |
|------|------|
| **字数** | 普通行 7-10字，Hook 5-8字，最多12字 |
| **押韵** | 统一主韵，推荐开口韵 -ang/-ao/-ai |
| **风格** | talk-sung，像说话不像诗 |

### talk-sung 参考

| 要做 | 不要做 |
|------|--------|
| 像跟自己说话 | 像在写诗 |
| 口语化用词 | 书面语/成语 |
| 短句、有气口 | 长句不断句 |

**参考标杆**：《习惯》副歌
```
习惯 让好事落下来
习惯 不用再证明
你说好 那就是好
这就够了 不用再做什么
```

### Era Reference（英文歌曲）

| Era | Style Keywords | BPM | Reference Feel |
|-----|----------------|-----|----------------|
| 70s Beach Boys | Soft rock, layered harmonies | 105-115 | Sunny, warm |
| 70s Glam | Theatrical rock, flamboyant | 110-120 | Bowie vibe |
| 80s Synth-pop | Electronic pop, sequenced | 110-125 | Cool, retro |
| 90s Grunge | Raw rock, distorted | 90-110 | Heavy, real |
| 2010s Americana | Road-weary, bittersweet | 95-110 | Baritone, folk |

### 英文 Style Prompts（历史版本）

**Healing Mode (English)**:
```bash
python openai_exec.py "Write healing lyrics for '[Title]'.
Theme: [insight]
Old belief → New belief: [transformation]
Conversational, like Phoebe Bridgers. 6-10 syllables per line."
```

**Pop Mode (English)**:
```bash
python openai_exec.py "Write pop lyrics for '[Title]'.
Theme: [one sentence]
Show don't tell. No preaching. Catchy hook. 6-10 syllables."
```

**Artistic Mode (English)**:
```bash
python openai_exec.py "Write artistic lyrics for '[Title]'.
Core imagery: [image]
Let images carry meaning. Leave space. Poetic but not pretentious."
```

### Legacy Pipeline

> 实验结论：直接生成优于 Pipeline

6阶段流程（已废弃）：INPUT → CONCEPT → STYLE → LYRICS → REVIEW → OUTPUT

</details>
