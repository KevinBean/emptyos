---
name: creative-youtube-channel
description: Manage the 3-30 Channel YouTube MUSIC channel — create song notes, check the release pipeline, generate videos, track releases. Use when the user says "3:30 channel", "song pipeline", "release status", or music-channel work. NOT for the AI-Engineering tutorial channel (use creative-youtube-ai-engineering) or generating the MV itself (use creative-mv-generator).
---

# YouTube Music Channel Manager

This skill manages the **3:30 Channel (AI Music)** project for publishing Suno-generated songs.

## Reference files (read on demand)

`SKILL_DIR` = `{home}/.claude/skills/creative-youtube-channel/`. The harness loads only this file; read a sibling with the Read tool when you reach the step that needs it.

| File | Priority | Read when |
|---|---|---|
| `<SKILL_DIR>/templates.md` | Required | Creating a song note, writing YouTube Title/Description/Tags (SEO), or running the publish checklist |
| `<SKILL_DIR>/scripts.md` | Required | Generating an animated / multi-scene MV, a Shorts clip, or a cover image; Codex dispatch; AI voice conversion |

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

Animated MV / Multi-Scene MV / Shorts / Cover generation, Codex dispatch, AI voice conversion: `<SKILL_DIR>/scripts.md`.

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

如果歌曲笔记不存在，使用模板创建。Song-note template: `<SKILL_DIR>/templates.md`.

#### Step 1.2: 填写版权清单

确保以下项目已完成再上传：
- [x] Suno 截图已保存（必须包含账号名、日期、订阅标识）
- [x] Suno 链接已记录
- [x] 视频文件已生成 (.mp4)

#### Step 1.3: SEO 优化

Title 格式 / Description 结构 / Tags 策略: `<SKILL_DIR>/templates.md`.

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
cd "{vault}\10_Projects\YouTube-Music-Channel\scripts"
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

Publishing Checklist (发布时打勾): `<SKILL_DIR>/templates.md`.

---

## Quality Standards

Before marking any song complete:
- [ ] All 版权记录 items checked
- [ ] YouTube description matches template
- [ ] Audio backed up to OneDrive/Suno/
- [ ] Linked to inner child healing songs note
