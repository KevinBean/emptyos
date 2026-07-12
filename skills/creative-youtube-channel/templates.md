# 3:30 Channel — Song Note Template, SEO & Checklists

## Song note template (Phase 1.1)

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

---

## SEO 优化 (Phase 1.3)

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

---

## Publishing Checklist (发布时打勾)

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
