# Guided Generation — 用几何约束换取可控性

> 调查问题：怎么让生成效果更好？别人怎么做的？Blender 参考？真实物理模型参考？
> 日期：2026-07-27 · 方法：`.claude/rules/deep-research.md`（本地取证 → 一手来源 → 三角验证 → 证据分级）

## 一句话结论

**别人不是靠"更好的提示词"或"更大的模型"解决可控性的，而是把结构（几何 + 相机运动）
从生成模型手里拿走，交给一个确定性的 3D 渲染去负责，只让生成模型负责"外观"。**

Blender 是对的方向。真实物理模型基本是错的方向 —— 但它在一个特定环节上确实赢，见 § 4。

## 1. 为什么这正好是我们已经实测到的痛点

`.claude/rules/dev-gotchas.md` 里有一条**实测**记录（2026-07-25，无所住 scene 02，
同模型同种子，只改提示词）：

> 摇臂上升的提示词把湿漉漉的街道变成了旋转的屋顶，主体走进一片白色虚空；
> 而锁定机位的提示词完整保住了 5 秒内的每一个地标。

当时的处理是**禁止相机运动** —— `apps/personal/music-studio/prompts.py` 的
`NO_CAMERA_MOVES` 常量，写死进两个场景规划器。这是一个**绕过**，代价是整部 MV
永远只能是三脚架固定机位，牺牲掉全部运镜语言。

CineScene（arXiv 2602.06959，2026）把这件事说成了一句设计原则：

> "...enabling camera-controlled video synthesis with consistent scenes...
> **prioritizes explicit geometric consistency over natural language camera instructions.**"

即：**用自然语言描述相机运动是弱路径，用几何条件约束才是强路径。**
我们的实测和这篇论文是各自独立得到同一结论的 —— 三角验证成立。

## 2. 别人具体怎么做（社区实践）

标准做法叫 **3D blockout → control map → 生成模型重绘**：

1. Blender 里搭一个**粗糙**的场景（灰模、无材质、无灯光）——只要形体和空间关系对。
2. **把相机运动 K 在 Blender 里**（摇臂、推轨、变焦都行，因为这是真的渲染，不是猜的）。
3. 渲染出**控制图序列**而不是成片：depth（深度）、normal（法线）、canny（边缘）、
   openpose（骨骼）、segmentation（分割）。
4. 把这个序列作为 `control_video` 喂给 ControlNet / VACE，由生成模型逐帧上色、上材质、
   上光影，同时被几何**锁死**。

社区里 toyxyz 的骨骼装备是事实标准（一次渲染同时输出 depth/canny/openpose/normal/seg）。

PrevizWhiz（arXiv 2602.03838，2026，做过真实电影从业者用户研究）给出的分工描述最清楚：

> 粗糙 3D 几何提供**空间结构与相机信息**，无需高精度资产或绑定专业知识；
> 生成模型负责**风格化与视觉精修**。

它同时**明确点名了局限**：`continuity`（连续性）—— 也就是说这套方法解决单镜头内的
几何一致性，但**镜头与镜头之间**的一致性仍然是未解问题，不要期望它顺带解决。

## 3. 我们手上已经有什么（本次核查，全部对着 live server 验过）

这是最意外的部分：**控制类模型早就躺在盘里，从来没被引用过。**

```
grep -rn -i "vace|controlnet" apps/ plugins/ emptyos/   →  0 hits
```

盘上实际有：

| 资产 | 大小 | 用途 | 现状 |
|---|---|---|---|
| `Wan2_1-VACE_module_1_3B_bf16` | 1.4 GB | VACE 控制模块（配 wan2.1 t2v 1.3B） | **从未使用** |
| `Wan2_1-VACE_module_14B_fp8` | 2.8 GB | 同上（配 14B） | **从未使用** |
| `wan2.2-ti2v-5b-controlnet-depth-v1` | 660 MB | 深度 ControlNet，**正好配现在生产用的 TI2V-5B** | **从未使用** |
| `wan2.1_t2v_1.3B_bf16` | 2.6 GB | VACE 的底模 | 未使用 |

节点侧（`/object_info` 实查）：
- `WanVaceToVideo`（**core 节点**）：`control_video: IMAGE`、`control_masks: MASK`、
  `reference_image: IMAGE`、`strength: FLOAT` → 输出 conditioning + latent。
- `WanVideoVACEModelSelect` / `WanVideoVACEEncode`（WanVideoWrapper）——VACE 模块的加载口。
- `ControlNetLoader`（core）**已经能看到**那个 wan2.2 深度 controlnet 文件。
- `DepthAnything_V2` 已装。

Blender 插件侧（`plugins/blender/plugin.py`）：已有 `render` / `render_animation` /
`run_script` / `import_model` / `get_scene_info`，并且已注册为 `draw` provider（priority=10）。
**缺的只有一件事**：`render` 只输出成品 PNG，没有 depth/normal AOV 通道。
不过 `run_script`（任意 bpy）是现成的逃生口，不改插件也能做出来。

## 4. 真实物理模型 —— 分开看

必须把"结构"和"外观"两件事拆开，否则会得到错误答案：

| 需求 | 谁赢 | 为什么 |
|---|---|---|
| **相机运动、空间几何、遮挡关系、连续性** | **Blender 完胜** | 实体模型要产出深度序列，得靠摄影测量（photogrammetry）或运动控制轨 —— 为了同一个信号付出数量级更高的成本。Blender 里改一个关键帧就完事。 |
| **材质真实感、光照、镜头缺陷** | **实物有优势** | 论文与实践都指出：AI 之所以"看着不对"，是因为不懂光如何与物体交互、镜头如何畸变。一张真实照片天然带着这些先验。 |
| **人物身份一致性** | **实物/真人照片** | 这条我们**已经在用** —— reader 应用的 IPAdapter 角色一致性工作流。 |

所以正确的组合不是二选一，而是：

> **Blender 管结构，真实照片管外观。**
> 几何走 `control_video`，质感走 `reference_image` / IPAdapter。
> VACE 的接口恰好同时收这两路 —— 这不是巧合，它就是为这个分工设计的。

搭实体模型再拍照来做 MV 分镜，投入产出比很差；但**拍一张真实材质/光照的参考照**
喂 reference 通道，是便宜且有效的。

## 5. 三档路线（按代价从低到高）

### 第 0 档 —— 已经存在，可能被低估了

`parallax` 模式已经实现并可用：`assembler.py:parallax_clip` /
`layered_parallax_clip`，用 DepthAnythingV2 的深度图对单张静帧做 2.5D 推轨扭曲。
**这已经是一个"几何的、零幻觉的相机运动"** —— 它不经过生成模型，所以不可能把街道
变成屋顶。UI 里标着 "experimental"。

局限是诚实的：单视角深度无法补出被遮挡的几何，所以只能做小幅度移动，推太狠会拉花。

**建议：在投入任何新东西之前，先真跑一遍 parallax 模式**，确认它到底能到什么程度。
这是唯一一个零新增基础设施的选项。

### 第 1 档 —— 最小改动，直接接现有生产链路

Blender 渲染带相机运动的**深度序列** → 现有 `wan22_i2v.json` 上加
`ControlNetLoader` + `ControlNetApplyAdvanced` 两个节点。

为什么这档最优先：那个 depth controlnet 就是**为现在生产用的 Wan 2.2 TI2V-5B 训练的**，
底模不用换，显存预算不变，工作流是增量而非重写。

**待验证的风险（我没有执行验证，不能打包票）**：core 的 `ControlNetApplyAdvanced`
类型上收 `CONTROL_NET` 所以能连上，但 wrapper 另外提供了
`WanVideoControlnetLoader → WANVIDEOCONTROLNET` 这个**专用类型**，暗示视频 controlnet
的预期消费者是 wrapper 采样器而非 core KSampler。**这一条必须先跑一次才能确认**，
不要凭类型签名下结论。

### 第 2 档 —— 完整可控性

VACE 全家桶：`control_video`（深度/边缘/骨骼序列）+ `control_masks`（局部重绘）+
`reference_image`（外观/身份）+ `strength`（保真度旋钮，对应 PrevizWhiz 的
"adjustable resemblance"）。

代价是要换到 WanVideoWrapper 采样栈 + wan2.1 底模 —— **是一条新工作流，不是改几行**。
显存上 1.3B + VACE 模块 ≈ 4 GB，在 16 GB 卡上非常宽裕；14B 组合 ≈ 16.6 GB，这张卡放不下。

## 6. 证据分级

| 结论 | 等级 |
|---|---|
| 文字描述相机运动会毁掉镜头 | **实测**（本仓库，同模型同种子对照） |
| 几何条件优于语言指令 | **一手来源已读**（CineScene 摘要，独立复现同一结论） |
| 粗糙 3D 提供结构、生成模型提供风格 是标准分工 | **一手来源已读**（PrevizWhiz 摘要 + 真实从业者用户研究） |
| VACE / depth-controlnet / Blender 渲染面 在本机可用 | **实测**（`/object_info` 实查 + 文件系统清点 + 代码 grep） |
| 社区做法（toyxyz 多通道装备、Blender→ComfyUI 桥） | **检索所得**，未逐个核实仓库 |
| core `ControlNetApplyAdvanced` 能驱动 Wan 视频 controlnet | **未验证 —— 必须先跑** |
| 单视角 parallax 的实际画质上限 | **未测量 —— 应先跑一次** |

## 7. 明确不建议做的事

- **不要为了这个去搭实体模型/微缩景观。** 结构上 Blender 完胜，实物只在外观参考上有价值，
  而外观参考一张照片就够了。
- **不要期望它解决跨镜头一致性。** PrevizWhiz 自己点名 `continuity` 仍是未解问题。
  镜头之间的一致性还得靠 IPAdapter / 角色 LoRA / 统一色彩管线。
- **不要先做第 2 档。** 换采样栈 + 换底模是一次性大改；第 1 档能用现有底模验证
  "几何约束是否真的解决了运镜问题"这个核心假设。先证伪，再投入。
- **不要在验证前就删掉 `NO_CAMERA_MOVES`。** 那条约束现在是对的；只有当控制链路
  真的跑通，它才应该从"禁止运镜"改成"运镜由 Blender 负责，提示词仍不描述运镜"。

## 8. 追问：Blender 模型本身能生成吗？

这是正确的追问 —— 上面那条链只是把难点往上游推了一格。答案分两半：**问题部分自我消解，
剩下的部分应该用代码模型解决，而不是网格模型。**

### 8.1 先消解：blockout 不是资产

关键区分是 **blockout（灰盒）≠ asset（资产）**。PrevizWhiz 的原话是"无需高精度资产
或绑定专业知识"。一个 blockout 就是**摆在大致正确空间位置上的 8 个灰色方块 + 一条
相机轨迹**，没有材质、没有灯光、没有细节 —— 因为这些**全部由生成模型补**。

所以需要生成的不是"一个好看的 3D 场景"，而是**布局 + 相机路径**。
那是一个**代码问题**，不是网格生成问题。这个区分决定了后面所有选择。

### 8.2 几何从哪来 —— 四条路，按代价排序

**第 0 条：根本不生成 —— 用你已经有的那张静帧。**
你本来就用 FLUX/Klein 生成了一张好看的静帧。深度图 → 位移网格 → 丢进 Blender →
推真实相机。**零建模**。这其实就是现有 `parallax_clip` 的 3D 正规版（现在的实现是
2D 扭曲，换成 Blender 里的分层位移网格能吃更大的运镜）。
局限诚实：单视角深度补不出被遮挡的部分，转多了会出空洞 —— 移动幅度受限。

**第 1 条（推荐）：用代码模型生成 bpy 脚本。**
LLM 写 Python → `blender.run_script(script)` → 灰盒场景 + 相机关键帧 → 渲深度序列。

这条路在本仓库**已经有完整先例和现成接缝**：
- `model` capability（`apps/personal/robot-modeller/`）的形状就是这个：
  *LLM 写 Python → 对着 SDK 编译 → 返回记录目录*。已经跑通并注册为 provider。
- `plugins/blender/plugin.py::run_script` 就是官方认可的执行缝 —— 代码注释明确写着
  任意 `exec` over RPC **已因安全移除**，`run_script` 起 headless 子进程做隔离。

而且产出物是**代码**：可读、可 diff、可版本化、可手改 —— 完全符合 EmptyOS
"一切皆可生成、一切可复现"的取向，而不是一个不透明的网格 blob。

**现存缺陷（顺手记下）**：`render_from_prompt` 的 headless fallback 文档字符串声称
"does loosely interpret the prompt"，实际实现无论提示词是什么都加一只 Suzanne 猴头
（`primitive_monkey_add`），完全没读 prompt。它是这条路上唯一需要替换的那段。

**第 2 条：网格生成模型 —— 只适合"英雄道具"，不适合场景。**
这台机器上其实已经有一整套：一个独立的 ComfyUI 安装（独立 venv、端口 8189，
路径按 `[plugins.comfyui-3d] launcher` 配置），装了 `ComfyUI-TRELLIS2`（图生3D）、
`ComfyUI-UniRig`（自动绑骨）、`ComfyUI-GeometryPack`。
**当前状态：未启动，且与 EmptyOS 零对接**（`grep -rn "8189|ComfyUI_3D|trellis|unirig"` → 0 命中）。

但研究结论明确不支持拿它做场景：TRELLIS 系是**物体尺度**的。要做场景得靠切块拼接
（TRELLISWorld 的多块去噪融合、SynCity 的 2D 平铺后再抬升），而这些方法的保真度
**上限被 TRELLIS 自身限死**，且推理时间随场景大小线性增长、并行则爆显存。
拿它生成一个具体道具（一把椅子、一个雕像）很合适；拿它生成一条街，不合适。

**第 3 条：手搓。** 控制力最高，成本最高。只在某个镜头真的值得时才做。

### 8.3 诚实的边界

- **LLM 能在 bpy 里写出的是"布局"，不是"美感"。** 但 blockout 只需要布局。
  要求它写出好看的场景是用错了工具。
- **验证极便宜**：渲一张深度图看一眼就知道对不对 —— 这是这条路最好的性质，
  失败在几秒内可见，不用等一次 5 分钟的 I2V 渲染。
- **不要为此去接 ComfyUI_3D。** 它是独立环境（3D 依赖与主环境冲突才分开装的），
  接进来是一整条新链路，而它解决的是"物体"而非"场景"—— 与当前瓶颈不对口。
  等真的需要一个具体英雄道具时再说。
- **未验证**：LLM 一次写对 bpy 布局脚本的成功率，我没有实测。这应该是第 1 条路
  投入前的第一个实验 —— 写 3 个场景，看出来的深度图能不能用。

### 8.4 所以整条链变成

```
歌词/场景描述
   → [代码模型] bpy 布局脚本      ← 生成的是代码，不是网格
   → Blender 渲染 depth/canny 序列（相机运动在这里是"渲染"的，不是"猜"的）
   → VACE / depth-ControlNet 逐帧重绘
   → 成片
```

真实照片走 `reference_image` 通道管质感；TRELLIS 只在需要具体道具时被调用一次。

## 9. 实验：3 个 bpy 布局场景 → 深度图（2026-07-27 实测）

脚本：`scripts/blockout_depth_probe.py`（Blender 5.0.1，EEVEE，512×288，每场景渲 3 帧）

```bash
blender -b -noaudio -P scripts/blockout_depth_probe.py -- street_crane <outdir>
```

九宫格对照（行=场景，列=帧 1/25/49）：`docs/assets/guided-generation-depth-probe.png`

### 9.1 结论：管道成立，作者能力是瓶颈 —— 但瓶颈可被秒级检出

| 场景 | 相机运动 | 结果 |
|---|---|---|
| `street_crane` | 摇臂上升 + 俯仰 | **通过** —— 透视走廊清晰，人物独立可辨，几何在整个运镜中纹丝不动 |
| `room_dolly` | 室内推轨 | **失败** —— 末帧只剩 5 个灰阶，推进了一面无特征的墙 |
| `horizon_orbit` | 环绕 110° | **部分** —— 前景视差极好，但远景山脊只放在一侧，转出画面后上半幅全黑 |

**关键一条：`street_crane` 正是 2026-07-25 那个把湿街道变成旋转屋顶的失败案例。**
作为几何渲染，同一个摇臂上升完全稳定 —— 建筑不会动，因为它们不是被猜出来的。
这就是本次调查要验证的核心假设，验证通过。

**两次失败都是"布局"错误，不是"管道"错误** —— 而且都是同一种：
*相机运动的终点没有可看的东西*。这是空间推理失误，不是语法失误。
LLM 首次写出的 3 个场景里有 2 个带这个缺陷，这个比例应该被如实记住。

### 9.2 验收守卫（已用本次数据反测）

代价约 2 秒、纯 numpy、无需 GPU：

| 信号 | 阈值 | 依据 | 定位 |
|---|---|---|---|
| `FLAT` 灰阶数 | `< 24` | room_dolly 末帧 **5**；健康帧 108–224 | **可门禁** —— 没有任何合法画面只有 5 个灰阶 |
| `DRIFT` 帧均值漂移 | `> 40` | room_dolly **76.7**；street_crane **7.0** | **可门禁** —— 固定 near/far 下大漂移即景深塌缩 |
| `VOID` 远端钳位占比 | `> 25%` | horizon_orbit 25.7/31.7%；street_crane 最高 14% | **仅提示** —— 夜空/雾天合法地就是大片黑 |

按 `.claude/rules/audits.md` 的规矩：**确定的那半门禁，含糊的那半只提示。**
两个门禁信号都能独立抓到 room_dolly，且对 street_crane 零误报。

这条守卫兑现了 §8.3 那句"验证极便宜"：失败在**渲染深度图后 2 秒内**可见，
不必等一次 5 分钟的 I2V 才发现镜头是废的。

### 9.3 两个必须记住的实现细节

- **Blender 5.0 移除了 `scene.node_tree`**（改为 `compositing_node_group`），
  所以经典的 RenderLayers→Normalize→Composite 深度配方在这台机器上直接失效。
  改用 `view_layer.material_override` + 一个 CameraData→MapRange→Emission 材质，
  深度直接从 beauty pass 出来，API 稳定且跨版本。
- **绝不做逐帧归一化。** 必须用**固定 near/far**。逐帧自动归一化会在相机一移动时
  重映射范围，控制信号随之呼吸，重绘就会闪。上表的 `DRIFT` 列就是这件事的度量。

顺带一个成本数据点：脚本第一版因 `action.fcurves` 在 Blender 5.0 被槽位化 Action 取代
而报错。**LLM 写 bpy 需要版本锁定的知识或防御式写法**，这是这条路的真实摩擦。

### 9.4 下一步

管道假设已验证，尚未验证的是**下游**：把 `street_crane` 的深度序列真正喂进
depth-ControlNet / VACE，确认重绘是否守住几何。那才是 §5 第 1 档要跑的实验。

## 10. B0 / B1 实测（2026-07-28）

### 10.1 B0 —— 控制序列就绪

`scripts/blockout_depth_probe.py`（已升级为完整序列渲染器）：

```bash
blender -b -noaudio -P scripts/blockout_depth_probe.py -- \
  street_crane <outdir> --frames 49 --width 1024 --height 576
```

**49 帧 @ 1024×576，9.2 秒，8MB。** 相对一次 5 分钟的 I2V 渲染基本免费。

| 守卫 | 实测 | 门禁 | 结果 |
|---|---|---|---|
| `FLAT` 最小灰阶数 | 152 | < 24 | 通过（6× 余量） |
| `DRIFT` 帧均值漂移 | 9.2 | > 40 | 通过 |
| `VOID` 远端钳位 | 最高 14.0% | > 25%（仅提示） | 通过 |
| **`SMOOTH` 逐帧步长**（新增） | max/median = **1.23×** | > 3× | 通过（无跳变） |

`SMOOTH` 是有了完整序列才能做的检查：逐帧 `mean|Δ|` 的最大值对中位数之比。
相机跳变会表现为尖峰。1.23× 说明运动是平滑连续的。

输出格式是**无损 PNG 序列**，喂给 `VHS_LoadImagesPath`（收目录直出 IMAGE batch）。
**不要走 mp4** —— h264 的色度子采样会给深度斜坡引入 banding。

### 10.2 B1 —— 两条 core 路径都被证伪

**B1a（Wan 2.2 depth ControlNet + core KSampler）—— 失败，3 秒：**

```
ControlNetLoader (node 61): RuntimeError
"ERROR: controlnet file is invalid and does not contain a valid controlnet model."
```

**B1b（core `WanVaceToVideo` + VACE 模块）—— 失败，41 秒：**

```
UNETLoader (node 1): RuntimeError
"ERROR: Could not detect model type of: Wan2_1-VACE_module_1_3B_bf16.safetensors"
```

**两次失败是同一个根因**：这些权重是 **WanVideoWrapper 格式**，core 节点读不懂。
`ControlNetLoader` 和 `UNETLoader` 在**类型层面**都接得上（`CONTROL_NET`、`MODEL`），
但类型能连≠格式能读。wrapper 之所以另有 `WanVideoControlnetLoader → WANVIDEOCONTROLNET`
和 `WanVideoVACEModelSelect → VACEPATH` 这两个专用类型，原因就在这里。

> **这印证了计划里那句"必须实测，不能凭类型签名下结论"。**
> 两次证伪合计消耗 GPU 时间 **< 1 分钟**。

### 10.3 结论的正确措辞

**被证伪的是"用 core 节点走捷径"，不是几何引导方向本身。**
上游（Blender → 深度序列）已验证可用且极便宜；被否掉的是"在现有 wan22 工作流上加两个
core 节点就能收工"这个乐观假设。

两条仍然活着的续接，都已核实可行：

| 续接 | 代价 | 状态 |
|---|---|---|
| **(a) 换 WanVideoWrapper 栈** | 无需下载，但是一张新图（`WanVideoModelLoader` + `WanVideoVACEModelSelect` + `WanVideoVACEEncode` + `WanVideoSampler` + wrapper VAE），约 12-15 节点 | **四个节点的枚举里都能看到盘上对应文件** —— 底模 `wanvideo\wan2.1_t2v_1.3B_bf16`、模块 `Wan2_1-VACE_module_1_3B_bf16` 齐全 |
| **(b) 下载合并版 VACE checkpoint** | ~4GB（`wan2.1_vace_1.3B_preview_bf16`），换来 core 节点可用、图改动最小 | 未下载 |

(a) 无需下载但是栈级改动；(b) 要下载但改动最小。**建议先 (a)** —— 它不花钱、不占盘，
而且如果 wrapper 栈跑通，那 Wan 2.2 的 depth ControlNet 也能沿同一条 wrapper 路走
（`WanVideoControlnetLoader` + `WanVideoControlnet`），一次投入解锁两种控制方式。

## 11. B1c —— wrapper 栈跑通，核心假设验证通过（2026-07-28）

工作流已固化：`plugins/comfyui/workflows/wan21_vace_depth.json`（对着 live 0.28.3 逐字段验过）。
证据图：`docs/assets/guided-generation-b1c-three-way.png`。

### 11.1 同模型 off/on 对照

种子、提示词、模型、步数、cfg、shift、调度器、分辨率、帧数**全部相同**，
唯一差别是 `WanVideoVACEEncode.input_frames` 是否接入深度序列。

| | 结果 |
|---|---|
| **OFF** | 一条好看的夜街 —— 但 **49 帧几乎完全静止**。同一盏路灯、同样的建筑与人影位置、同样的透视。**没有任何运镜。** 716s |
| **ON** | 几何与 blockout 对应（走廊宽度、两侧建筑收敛、中景竖直物体位置），**摇臂上升被执行**：视点升高、俯角加大、路面占比增加、灭点下移。湿沥青反光/霓虹/氛围全部由模型补。220s |

**这就是"几何来自渲染，外观来自模型"的分工，端到端验证通过。**

顺带独立复现了原始发现的另一面：不给几何条件时，用文字要求运镜这次不是把街道变成屋顶，
而是**干脆不动**。两种失败模式不同，结论一致 —— **文字无法可靠驱动相机**。

### 11.2 诚实的细节：轨迹被跟随，但不是逐帧锁定

用边缘图做了一个对齐矩阵（输出第 i 帧 vs 深度第 j 帧的相关性）：

- 匹配帧相关性 **−0.0106** 明显高于错配帧（|i−j|>8）的 **−0.0340** —— 存在帧特异性对齐。
- 最佳匹配序列**单调递增**（输出 0/6/12/…/48 → 深度 10/12/17/23/30/38/41/43/46），
  几何按正确顺序推进。
- 但**平均偏移 5.0 帧**（开头最大，10 帧），仅 22% 落在 ±4 帧内。

即：VACE 在 `strength=1.0` 下跟随控制序列的**轨迹**，但不逐帧锁死。
可调轴：`strength`、`vace_start_percent`/`vace_end_percent`、模型容量（1.3B vs 14B）。

### 11.3 两个把 ComfyUI 弄崩的坑（已写进工作流 `_constraints`）

- **分辨率必须落在模型的原生带**。1.3B 是 480p 模型；按 1024×576 喂它（序列长度 29952）
  在 16GB 卡上**无 Python 异常直接原生崩溃** —— 日志停在
  `Sampling start / 49 frames at 1024x576`。换 832×480 后 220s 正常完成。
  崩溃前日志已显示模型 + VACE 模块加载成功、采样器启动，**所以那次崩溃证明的是显存，
  不是兼容性** —— 这个区分很重要，否则会误判成"wrapper 路也不通"。
- **T5 不能用 fp8-*scaled* 权重**：`LoadWanVideoT5TextEncoder` 显式拒绝
  （`Invalid T5 text encoder model, fp8 scaled is not supported`）。
  用 `umt5-xxl-enc-fp8_e4m3fn.safetensors`。

### 11.4 已知限制

blockout 里那根代表人物的**无特征圆柱，被重绘成了系柱/立柱，不是人**。
深度只描述形体，不描述语义。人物需要额外通道 —— 类人代理网格，或 openpose 控制层
（`WanVideoVACEEncode` 的 `ref_images` 与 `input_masks` 是现成的接入点）。

## 12. B2 / B3 —— 作者循环与集成（2026-07-28）

### 12.1 B2：LLM 只产出数据，永不产出代码

`scripts/blockout_spec.py`（schema + 验证 + prompt）→ `blockout_depth_probe.py --spec`
**解释**验证过的数据，调用我们写的图元。物体名一律自动生成（`obj_0`…），
模型的字符串永远进不了名字查找。**注入面为零，不需要加固。**

这比原计划的"生成 bpy 源码再执行"更好 —— 后者仍要面对"生成的代码是否安全"。
顺带纠正计划里一处误判：`blender.run_script()` 起的是 headless 子进程，
它隔离的是**正在运行的 Blender 实例**，不是文件系统或网络。**它是进程边界，不是信任边界。**

三个全新 brief 的实测：小巷推进**首次通过**；广场环绕被 `SMOOTH 3.45×` 拒绝，
把抱怨喂回后作者把相机关键帧从 5 个改为 9 个均匀分布，**3.45×→1.40×，一次通过**；
山坡摇臂三次全拒 —— 而那是**我的门禁错了**（见 12.2）。

### 12.2 循环揪出了守卫自己的标定 bug

山坡摇臂一直卡 `DRIFT`。但摇臂上升揭示山谷，均值深度**本来就该大变**。
DRIFT 只在两个场景上标定过，第三种合法镜头类型就推翻了它：

| 序列 | 均值漂移 | 最小 p95−p5 |
|---|---|---|
| room_dolly（真塌缩） | 74.3 | **3** |
| 山坡摇臂（被误拒） | 56.8 | **168** |
| 小巷（最接近的合法 case） | 4.8 | 34 |

均值漂移分不开前两行；深度跨度差 50 倍。**`RANGE` 才是 DRIFT 一直声称要测的东西**，
它接替成为门禁，DRIFT 降为提示。修正后五个序列全部判对。

这是 `.claude/rules/audits.md` 那条纪律的实例：**信任一个启发式之前，先拿已知健康的
案例测它。** 我只测了两个就发布了。

### 12.3 B3：几何控制是「镜头的属性」，不是「整片的模式」

原计划说"加一个新 visual mode"。看过实际代码后改掉了 —— 一支真实 MV 会**有些镜头
锁定、有些运镜**，整片级 mode 表达不了；而且新 mode 要穿过 6 个 `mode != "video"`
守卫外加 fan-out 授权门，在一个一天涨了 4000 行（6949→10883）、仍被并行编辑的文件
里风险过高。

改为 **per-scene opt-in**：场景携带 `blockout` spec 即启用，走 parallax 那条现成的
per-scene 分支先例。全部 stage 机制原样不动（几何镜头同样需要好静帧作外观锚点）。

- `plugins/comfyui/plugin.py::generate_guided_video` —— 薄封装，`generate_depth` 的形状
- `apps/personal/music-studio/blockout.py` —— 渲染 + 守卫 + 组片（新模块，让 `visual.py` diff 最小）
- `visual.py` —— 只加了 import、checkpoint 一段、`_build_clips` 一个分支
- `emptyos.toml` —— `vace_workflow` 键

**两个刻意的设计：**

1. **checkpoint 指纹条件式写入**，照抄 `layered-atmosphere` 的先例。若无条件加入，
   几何功能出现之前**每一个已验收片段都会失效重渲** —— 数小时 GPU，且悄无声息。
2. **失败不回退**。几何镜头之所以是几何镜头，就是因为它需要运镜；悄悄换成静止镜头
   属于 video 分支已经明令拒绝的那种"silent degradation"。失败即留空并报错。

暗置于 `[apps.music-studio] feature.geometry-camera.enabled`。
**诚实的状态：渲染链路已通且暗置，但没有任何 stage 产出 `blockout` 字段，也没有 UI，
所以正常渲染流程里仍然每个镜头都是锁定的。** 下一步是让规划阶段产出它。

测试：`tests/test_unit_music_studio_blockout.py`（13 项）+ `test_unit_blockout_spec.py`（24 项）。

## 交叉引用

- `.claude/rules/dev-gotchas.md` § 媒体 —— 运镜实测记录、帧数/尺寸网格约束
- `apps/personal/music-studio/prompts.py` `NO_CAMERA_MOVES` —— 当前的绕过方案
- `apps/personal/music-studio/assembler.py` `parallax_clip` —— 已有的几何相机运动
- `plugins/blender/plugin.py` —— 已有 3D 渲染面；缺 AOV 通道输出
- `.claude/rules/model-ability.md` § validity gate —— 生成侧先验 vs 事后校验的同构讨论
- PrevizWhiz arXiv 2602.03838 · CineScene arXiv 2602.06959
