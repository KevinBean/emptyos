---
name: youtube-channel
description: Manage the 3:30 Channel YouTube music channel - create song notes, check pipeline status, generate videos, track releases
---

# YouTube Music Channel Manager

This skill manages the **3:30 Channel (AI Music)** project for publishing Suno-generated songs.

## Project Location

- **Main Plan**: `10_Projects/YouTube-Music-Channel/YouTube Music Channel Plan.md`
- **Songs**: `10_Projects/YouTube-Music-Channel/songs/YYYY-MM-DD__SongName/`
- **Assets**: `10_Projects/YouTube-Music-Channel/assets/`
- **Suno Guide**: `30_Resources/Technology/Suno AI 曲风参考.md` — 曲风、提示词技巧、中文歌词技巧

## Commands

### Status Check
When user asks about channel status, pipeline, or what songs are ready:
1. Read all song notes in `songs/` folder
2. Check 版权记录 section for each song
3. Report:
   - ✅ **Uploaded**: YouTube link checkbox checked
   - ⏳ **Ready**: Video generated, no YouTube link yet
   - 🔄 **In Progress**: Missing checklist items

### New Song
When user wants to create a new song note:
1. Ask for: 歌名, 曲风, BPM, 语言, 系列 (if not provided)
2. Create folder: `songs/YYYY-MM-DD__歌名/`
3. Create note using template from existing songs (copy structure from `One Step 就好.md`)
4. Include: 基本信息, Suno Prompt, 歌词, YouTube上传信息, 版权记录

### Generate Video (Static Background)
When user wants to create a video with static breathing background:
1. Locate song folder and read note
2. Check prerequisites exist: audio (.mp3), cover (.png), lyrics
3. Get BPM and style from note
4. Look up color scheme in main plan (配色方案 table)
5. Generate breathing background using Python script from plan
6. Run ffmpeg composite command from plan
7. Update note: check "生成横屏视频"

### Generate Animated MV (AI Video Background)
When user wants to create a video with AI-animated background:

**Script**: `10_Projects/YouTube-Music-Channel/scripts/generate_animated_mv.py`

**Usage**:
```bash
cd "{vault}/10_Projects/YouTube-Music-Channel"

# Auto-detect files from song folder (uses LLM for prompt if API key available)
python scripts/generate_animated_mv.py "songs/2026-01-08__计划型冒险家/"

# With custom animation prompt
python scripts/generate_animated_mv.py "songs/xxx/" --animation-prompt "neon pulse, dreamy glow, slow zoom"

# Skip animation (use static cover for quick test)
python scripts/generate_animated_mv.py "songs/xxx/" --skip-animation

# Skip LLM prompt generation
python scripts/generate_animated_mv.py "songs/xxx/" --no-llm

# Explicit files
python scripts/generate_animated_mv.py --audio song.mp3 --cover cover.png --title "歌名"
```

**Process**:
1. Parses song note for metadata (style, mood, lyrics)
2. Generates animation prompt:
   - Uses OpenAI/Gemini API if available (creative prompt from song context)
   - Falls back to: `{style}, {mood}, subtle motion, smooth loop, cinematic`
3. AnimateDiff generates 24-frame pingpong loop from cover
4. Loops to song duration
5. Composites with dark overlay + text shadows for readability
6. Outputs final MP4

**Visual Design**:
- Left side: Title + author + channel (with shadows)
- Right side: Scrolling lyrics on dark overlay (40% opacity)
- Background: Animated cover (full screen, looping)

**Prerequisites**:
- ComfyUI running (localhost on Home PC, or `100.91.167.57:8188` via Tailscale)
- AnimateDiff installed in ComfyUI
- ffmpeg in PATH
- `pip install requests mutagen`
- (Optional) `OPENAI_API_KEY` or `GEMINI_API_KEY` for smart prompt generation

**Output**: ~50-80 MB for 2-4 minute songs

### Generate Multi-Scene MV (Advanced)
When user wants a video with different animations for each song section:

**Script**: `10_Projects/YouTube-Music-Channel/scripts/generate_multiscene_mv.py`

**Usage**:
```bash
cd "{vault}/10_Projects/YouTube-Music-Channel"

# Auto-detect sections from lyrics + audio analysis
python scripts/generate_multiscene_mv.py "songs/2026-01-08__计划型冒险家/"

# Limit to fewer scenes (faster)
python scripts/generate_multiscene_mv.py "songs/xxx/" --scenes 4

# Skip audio analysis (use lyrics timing only)
python scripts/generate_multiscene_mv.py "songs/xxx/" --no-audio-analysis

# Custom output path
python scripts/generate_multiscene_mv.py "songs/xxx/" --output "custom_name.mp4"
```

**Process**:
1. Parses lyrics for section markers ([Verse], [Chorus], [Bridge], etc.)
2. Uses librosa audio analysis to refine section boundaries and detect energy
3. Generates unique animation prompt per section via Gemini CLI
4. AnimateDiff creates different animation for each section
5. Crossfade transitions (1s) between scenes
6. Overlays lyrics and song info

**Features**:
- Per-section LLM prompts based on lyrics content + energy level
- Audio-based section boundary detection
- Energy-aware animation (high energy = dynamic, low = calm)
- Smooth crossfade transitions

**Prerequisites**: Same as Animated MV + `pip install librosa numpy`

**Output**: ~60-80 MB for 2-4 minute songs

### Upload Checklist
When user is uploading or has uploaded a song:
1. Read the song note
2. Verify all copyright items are documented
3. After upload, update: YouTube链接, 上传日期
4. Link to [[inner child healing songs]]

---

## 🚀 Complete Publishing SOP

**CRITICAL**: 每次发布前 Claude 必须按此流程执行，不可跳过任何步骤。

### Phase 1: Pre-Upload (每首歌)

#### Step 1.1: 创建歌曲笔记

如果歌曲笔记不存在，使用模板创建：

```markdown
---
title: 歌名
type: song
album: "[[专辑笔记]]"
language: Chinese/English
status: draft
created: YYYY-MM-DD
published:
youtube_id:
playlist:
tags:
  - song
  - chinese/english
  - 主题tag
---

# 歌名

**YouTube**: (待上传)

**Playlist**: Playlist名 (`PLAYLIST_ID`)

---

## 发布信息

**Title**:
```
(YouTube 标题)
```

**Description**:
```
(YouTube 描述)
```

**Tags**: (逗号分隔)

---

## 版权记录清单

- [ ] 歌曲note（汇总所有信息）
- [ ] 生成日期: YYYY-MM-DD
- [ ] Suno生成截图（账号名、生成日期、订阅标识）
- [ ] Suno链接
- [ ] Prompt & style
- [ ] 歌词原文
- [ ] 音频原文件（+ OneDrive备份）
- [ ] 封面图
- [ ] 横屏视频
- [ ] 竖屏视频
- [ ] (可选) Shorts 剪辑
- [ ] (可选) AI变声翻唱版本
- [ ] YouTube链接
- [ ] 上传日期

---

## Style Prompt

```
(Suno style prompt)
```

---

## 歌词

(歌词或链接到专辑笔记)

---

## Related

- [[专辑笔记]]
- [[中文/英文版本]]
```

#### Step 1.2: 填写版权清单

确保以下项目已完成再上传：
- [x] Suno 截图已保存（必须包含账号名、日期、订阅标识）
- [x] Suno 链接已记录
- [x] 视频文件已生成 (.mp4)

#### Step 1.3: SEO 优化

**Title 格式**:
- 中文: `《歌名》 | 副标题/情绪 | AI Music (Suno)`
- 英文: `Song Name | Subtitle/Mood | AI Music (Suno)`

**Description 结构**:
```
[Hook - 第一句最重要，显示在搜索结果]

[2-3句主题解释]

Language: Chinese/English
Created with AI (Suno). Lyrics & prompt curated by me.

---

📀 Album/专辑: [专辑名]
🎧 Theme/系列: [系列名]

#hashtag1 #hashtag2 #hashtag3
```

**Tags 策略**:
- 核心方法关键词 (grey rock, BIFF, etc.)
- 目标受众搜索词 (healing music, 治愈音乐)
- 曲风标签 (indie, trip hop, electro pop)
- 语言标签 (中文歌, Chinese song)
- 通用标签 (AI music, Suno)

### Phase 2: Upload

#### Step 2.1: 确认上传参数

**上传前必须确认**:
- [ ] 视频文件路径正确
- [ ] Title/Description/Tags 已优化
- [ ] 目标 Playlist ID 正确
- [ ] Privacy 设置正确 (public/private/unlisted)

**Playlist IDs 速查**:
在专辑笔记或主计划中查找 Playlist ID。

#### Step 2.2: 执行上传

**脚本位置**: `10_Projects/YouTube-Music-Channel/scripts/youtube_upload.py`

**单首上传命令**:
```bash
cd "{vault}/10_Projects/YouTube-Music-Channel/scripts"
python youtube_upload.py \
  --file "视频路径.mp4" \
  --title "YouTube 标题" \
  --description "YouTube 描述" \
  --tags "tag1,tag2,tag3" \
  --privacy public \
  --playlist PLAYLIST_ID
```

**注意**:
- Description 中的引号需要转义: `\"`
- 换行用实际换行，不用 `\n`
- Privacy: `public` 立即公开, `private` 定时发布用

#### Step 2.3: 定时发布 (可选)

如需定时发布：
1. 上传时用 `--privacy private`
2. 去 YouTube Studio → Content → 选择视频 → Visibility → Schedule
3. 设定发布时间

### Phase 3: Post-Upload

#### Step 3.1: 更新歌曲笔记

上传成功后立即更新：

```markdown
# frontmatter 更新
status: published
published: YYYY-MM-DD
youtube_id: VIDEO_ID

# 正文更新
**YouTube**: https://www.youtube.com/watch?v=VIDEO_ID

# 版权清单勾选
- [x] YouTube链接: https://www.youtube.com/watch?v=VIDEO_ID
- [x] 上传日期: YYYY-MM-DD
```

#### Step 3.2: 更新发布日历

更新 `YouTube Music Channel Plan.md` 的发布日历：

```markdown
| 日期 | 星期 | 歌曲 | 系列 | 状态 |
|------|------|------|------|------|
| MM-DD | Day | 歌名 | 系列 | ✅ 已发布 `VIDEO_ID` |
```

#### Step 3.3: 更新周记录

更新当周的周计划 `50_Journal/2026/2026-WXX.md`：

在 **Review → 完成** 部分添加：
```markdown
- [x] 发布 [歌名] 到 YouTube ✅ YYYY-MM-DD
  - URL: https://www.youtube.com/watch?v=VIDEO_ID
```

#### Step 3.4: 更新专辑笔记 (如适用)

如果是专辑中的歌曲，更新专辑笔记的发布状态表。

### Phase 4: 批量发布

发布多首歌时：

1. **先创建所有歌曲笔记** (Phase 1.1)
2. **填写所有版权清单** (Phase 1.2)
3. **优化所有 SEO** (Phase 1.3)
4. **批量上传** (可并行执行多个上传命令)
5. **批量更新记录** (Phase 3)

### Publishing Checklist (发布时打勾)

```markdown
## 发布检查清单

### Pre-Upload
- [ ] 歌曲笔记已创建
- [ ] Suno 截图已保存
- [ ] Suno 链接已记录
- [ ] 视频文件已生成
- [ ] Title 已优化
- [ ] Description 已优化
- [ ] Tags 已优化
- [ ] Playlist ID 已确认

### Upload
- [ ] 上传命令已执行
- [ ] 上传成功，获得 Video ID

### Post-Upload
- [ ] 歌曲笔记已更新 (status, youtube_id, 版权清单)
- [ ] 发布日历已更新
- [ ] 周记录已更新
- [ ] 专辑笔记已更新 (如适用)
```

---

### Create Shorts
When user wants to clip a Shorts from vertical video:
1. **估算**：根据歌词结构估算目标片段（引流用 Pre-Chorus + Chorus 1）
2. **检测**：用波形分析验证精确开始/结束时间点
3. **剪辑**：用 ffmpeg 重新编码（⚠️ 不用 -c copy，会黑屏）
4. 更新歌曲 note 勾选 Shorts 项

详细方法见 `YouTube Music Channel Plan.md` → Shorts 剪辑

## Generate Cover Image

使用 DALL-E 3 生成封面图，每次生成 4 张（2 竖版 + 2 横版）方便挑选。

**脚本位置**: `10_Projects/YouTube-Music-Channel/scripts/generate_cover.py`

**用法**:
```bash
cd "10_Projects/YouTube-Music-Channel"

# 基本用法（输出到当前目录）
python3 scripts/generate_cover.py "主题描述"

# 指定输出目录
python3 scripts/generate_cover.py "主题描述" "./songs/2026-01-10__歌名/"
```

**输出文件**:
```
cover_vertical_1.png  - 竖版 1024x1792 (9:16) 用于 Shorts/Suno
cover_vertical_2.png  - 竖版 1024x1792 (9:16)
cover_wide_1.png      - 横版 1792x1024 (16:9) 用于 YouTube 视频背景
cover_wide_2.png      - 横版 1792x1024 (16:9)
```

**示例**:
```bash
# City pop 风格
python3 scripts/generate_cover.py "霓虹城市夜景，粉紫色调，复古未来感"

# 温暖治愈风
python3 scripts/generate_cover.py "温暖的深夜房间，一盏小台灯，治愈氛围"

# Lo-fi 风格
python3 scripts/generate_cover.py "雨天窗边，咖啡杯，慵懒午后"

# 自信能量风
python3 scripts/generate_cover.py "屋顶边缘的身影，准备起跳，城市灯光，粉紫霓虹"
```

**自动添加的默认参数**:
- Lo-fi 风格、暗背景、暖色焦点
- 无文字、无 logo
- 居中构图、安全边距

**依赖**: 需要设置 `OPENAI_API_KEY` 环境变量

---

## Codex Integration

For heavy tasks, suggest dispatching to codex:
```bash
codex exec "Generate YouTube video for 《歌名》 using workflow in YouTube Music Channel Plan.md"
```

## AI Voice Conversion (Optional)

将 Suno 生成的歌曲转换为自己的声音。

### Workflow

```
原始歌曲.mp3
      ↓ Demucs 分离
vocals.mp3 + no_vocals.mp3
      ↓ Applio 转换
vocals_output.wav
      ↓ FFmpeg 混合
歌名_cover.mp3
```

### Commands

**Step 1: 分离**
```bash
# 复制到英文路径避免编码问题
copy "歌曲.mp3" "D:\temp\input.mp3"
python -m demucs "D:\temp\input.mp3" -o "D:\temp\output" --two-stems=vocals --mp3
```

**Step 2: 转换**
1. 打开 `D:\Applio\run-applio.bat`
2. Inference → 选择模型 → 上传 vocals.mp3 → Convert

**Step 3: 混合**
```bash
ffmpeg -y -i "vocals_output.wav" -i "no_vocals.mp3" \
  -filter_complex "[0:a]aformat=channel_layouts=stereo[v];[v][1:a]amix=inputs=2:duration=longest:weights=1.2 0.8[out]" \
  -map "[out]" -ar 44100 -b:a 320k "歌名_cover.mp3"
```

### References

- [[Applio]] - AI 变声工具
- [[Demucs]] - 音频分离工具
- `YouTube Music Channel Plan.md` → AI 变声翻唱章节

---

## Quality Standards

Before marking any song complete:
- [ ] All 版权记录 items checked
- [ ] YouTube description matches template
- [ ] Audio backed up to OneDrive/Suno/
- [ ] Linked to inner child healing songs note
