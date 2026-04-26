---
name: podcast-studio
description: Generate AI podcasts from topics or vault content — two-host conversation with optional background music and video
---

# Podcast Studio Skill

Generate NotebookLM-style two-host conversational podcasts via the Podcast Studio mini-app (port 8010).

## Trigger Words

| User Says | Action |
|-----------|--------|
| "make a podcast about X" / "generate podcast" | Topic podcast |
| "podcast from today's notes" / "daily podcast" | Auto from today's journal |
| "weekly review podcast" | Auto from this week's journals |
| "project update podcast" | Auto from active projects |

## Mode 1: Topic Podcast

User provides a topic. Call the API:

```bash
curl -s -X POST http://localhost:8010/api/podcast/generate \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "TOPIC_HERE",
    "auto_research": true,
    "language": "en",
    "voice_pair": "en_mf",
    "duration": "medium",
    "bgm_enabled": false,
    "video_enabled": false
  }'
```

## Mode 2: Vault Auto-Generation

Generate from vault content automatically:

```bash
curl -s -X POST http://localhost:8010/api/podcast/auto \
  -H "Content-Type: application/json" \
  -d '{
    "source": "vault_daily",
    "language": "en",
    "voice_pair": "en_mf",
    "duration": "short"
  }'
```

**Sources**: `vault_daily` (today's journal + weekly plan), `vault_weekly` (past 7 days), `vault_projects` (recent project updates)

## Parameters

| Param | Values | Default | Notes |
|-------|--------|---------|-------|
| language | `en`, `zh`, `bi` | `en` | English / Chinese / Bilingual |
| voice_pair | `en_mf`, `en_mm`, `en_ff`, `zh_mf`, `zh_mm`, `zh_ff`, `bi_mf` | `en_mf` | Speaker voice combo |
| duration | `short`, `medium`, `long` | `medium` | ~3 / ~5 / ~10 min |
| bgm_enabled | `true`/`false` | `false` | Mix background music from generated_music/ |
| bgm_track | filename or `null` | `null` | Specific BGM track, or null for random |
| video_enabled | `true`/`false` | `false` | Generate MP4 with AI-generated images + subtitles |
| auto_research | `true`/`false` | `true` | Web search for context (topic mode only) |

## Response

```json
{
  "ok": true,
  "filename": "podcast_1773654904_topic.mp3",
  "video_filename": "podcast_1773654904_topic.mp4",
  "topic": "...",
  "duration_s": 312.5,
  "segments": 20,
  "elapsed_s": 45.2
}
```

**Audio**: `http://localhost:8010/api/podcast/stream/{filename}`
**Video**: `http://localhost:8010/api/podcast/stream/{video_filename}`

## Timing

| Config | Approx Time |
|--------|-------------|
| Audio only, short | ~25s |
| Audio only, medium | ~45s |
| Audio + BGM | +5s |
| Audio + Video (5 images) | +90s (gpt-image) +30s (ffmpeg) |

## After Generation

1. Report: filename, duration, segment count, generation time
2. The podcast is playable in the Music Player (shows under "Podcasts" filter)
3. Web UI: `http://localhost:8010/podcast`

## Workflow Tips

- For daily briefing: use `vault_daily` source with `short` duration
- For deep dives: use topic mode with `long` duration + `auto_research: true`
- For sharing: enable `video_enabled` to get an MP4 with slides
- Chinese topics: set `language: "zh"` and `voice_pair: "zh_mf"`
