# 3:30 Channel — Video, Shorts, Cover & Voice Scripts

## Generate Animated MV (AI Video Background)
When user wants to create a video with AI-animated background:

**Script**: `10_Projects/YouTube-Music-Channel/scripts/generate_animated_mv.py`

**Usage**:
```bash
cd "{vault}\10_Projects\YouTube-Music-Channel"

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

## Generate Multi-Scene MV (Advanced)
When user wants a video with different animations for each song section:

**Script**: `10_Projects/YouTube-Music-Channel/scripts/generate_multiscene_mv.py`

**Usage**:
```bash
cd "{vault}\10_Projects\YouTube-Music-Channel"

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

---

## Create Shorts
When user wants to clip a Shorts from vertical video:
1. **估算**：根据歌词结构估算目标片段（引流用 Pre-Chorus + Chorus 1）
2. **检测**：用波形分析验证精确开始/结束时间点
3. **剪辑**：用 ffmpeg 重新编码（⚠️ 不用 -c copy，会黑屏）
4. 更新歌曲 note 勾选 Shorts 项

详细方法见 `YouTube Music Channel Plan.md` → Shorts 剪辑

## Generate Cover Image

使用 gpt-image-1 生成封面图，每次生成 4 张（2 竖版 + 2 横版）方便挑选。

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
