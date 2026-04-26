---
name: speaking-practice
description: Manage speaking practice sessions - help record, analyze recordings with audio metrics, track progress, and provide feedback on speaking skills
---

# Speaking Practice Manager

帮助管理讲话练习，包括录制、分析、追踪进步。

## Location

- **Main Doc**: `20_Areas/Speaking-Practice/Speaking Practice.md`
- **评分标准**: `20_Areas/Speaking-Practice/评分标准.md`
- **Recordings**: `20_Areas/Speaking-Practice/recordings/`
- **Scripts**:
  - `scripts/speaking_recorder.py` - 录音 GUI
  - `scripts/analyze_audio.py` - 音频指标分析

## 完整分析流程

当用户说"分析录音"或"分析我的练习"时，执行以下自动化流程：

### Step 1: 找到录音文件
```bash
ls -la 20_Areas/Speaking-Practice/recordings/*.wav | tail -1
```

### Step 2: 确认转录文件存在
- 检查同名 .txt 文件是否存在
- 如果不存在，运行 Whisper 转录

### Step 3: 运行音频指标分析
```bash
python3 20_Areas/Speaking-Practice/scripts/analyze_audio.py "录音文件.wav"
```

输出指标：
- 语速 (WPM)
- 停顿次数、平均停顿、最长停顿、停顿占比
- 音量变化系数

### Step 4: 分析转录文本
读取 .txt 转录文件，分析：
- **口头禅检测**：统计"这样的一个"、"然后"、"就是"等出现次数
- **逻辑结构**：检查是否有开头、主体、结尾
- **内容评估**：是否有明确观点、具体例子

### Step 5: 综合评分
按 `评分标准.md` 打分：

**客观指标（音频分析）**：
| 指标 | 参考 | 影响维度 |
|------|------|----------|
| 语速 | 150-200 WPM | 流畅度 |
| 停顿占比 | <15% | 流畅度 |
| 音量变化 | >20% | 自然度 |

**主观评估（文本分析）**：
| 维度 | 满分 |
|------|------|
| 流畅度 | 5 |
| 清晰度 | 5 |
| 逻辑性 | 5 |
| 自然度 | 5 |
| 内容 | 5 |

英文录音额外评估：发音、语法、词汇、地道度

### Step 6: 生成分析报告
创建 `recordings/YYYY-MM-DD__HH-MM-SS__analysis.md`，包含：
- 音频指标表格
- 各维度评分及依据
- 口头禅统计
- 做得好的地方
- 改进建议
- **纠正练习计划**（基于检测到的问题）
- 金句收藏
- 完整转录文本

### Step 6.5: 生成纠正练习计划
基于口头禅检测和改进建议，生成个性化练习计划：

1. **选择本周重点**：挑出现次数最多的口头禅
2. **制定练习步骤**：
   - Day 1-2: 意识训练（重听标记）
   - Day 3-4: 替换练习（30秒短录音）
   - Day 5-7: 整合练习（重录同一话题）
3. **设定量化目标**：如 "然后" 6次 → ≤3次
4. **添加 checkbox** 便于追踪

参考 `评分标准.md` → 纠正练习方法

### Step 7: 更新进度追踪
在 `Speaking Practice.md` 的进度表添加记录：
```markdown
| 日期 | 话题 | 时长 | 总分(/25) | 备注 |
| YYYY-MM-DD | 话题 | X.X min | XX | [[analysis链接]] |
```

### Step 8: 检查里程碑
更新已达成的里程碑（如：第1次录制、连续7天等）

---

## 快速命令

### 启动录音
```bash
python3 20_Areas/Speaking-Practice/scripts/speaking_recorder.py &
```

### 分析最新录音
```bash
# 找到最新录音
latest=$(ls -t 20_Areas/Speaking-Practice/recordings/*.wav | head -1)
# 分析音频指标
python3 20_Areas/Speaking-Practice/scripts/analyze_audio.py "$latest"
```

### Whisper 转录
```bash
whisper "recording.wav" --output_format txt --output_dir recordings/
```

---

## 评分参考

详见 `评分标准.md`，包含：
- 音频客观指标评分标准
- 5个通用维度详细定义
- 4个英语专项维度
- 口头禅检测列表
- 评分示例

---

## Encouragement

练习讲话是长期积累。提醒用户：
- 每次进步一点点就是成功
- 客观数据帮助发现盲点
- 不完美没关系，重要的是持续练习
- 对比自己的历史记录，不和别人比
