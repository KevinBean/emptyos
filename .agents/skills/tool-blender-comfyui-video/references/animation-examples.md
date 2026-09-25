# 可運行的 Blender 動畫樣例

搭配 repository `docs/BLENDER-ANIMATION-GUIDE.md` 使用。這兩個樣例不需要模型、網路、字體或下載素材；只示範可控制的動畫機制，不是完成的 MV 鏡頭，也不代表所有曲線都符合下一首歌。

## 生成工程及預覽影格

在 Blender 背景模式執行本 skill 的 `scripts/build_animation_examples.py`：

```text
blender --background --factory-startup --threads 8 --python scripts/build_animation_examples.py -- --output /absolute/output/path --render
```

在 skill 目錄執行，或把 script 換成絕對路徑。省略 `--render` 只建立兩份 `.blend` 和 manifest。使用新的輸出目錄；同名輸出會被覆寫。預覽為 Cycles CPU、640×360、24 fps、8 samples；這是低成本驗證設定，不是最終品質設定。

將影格序列轉成預覽（每個樣例分別執行）：

```text
ffmpeg -framerate 24 -start_number 1 -i /absolute/output/path/01-paper-handoff/frame-%04d.png -c:v libx264 -crf 18 -pix_fmt yuv420p /absolute/output/path/01-paper-handoff.mp4
```

## 01 紙張接力：6 秒

輸出 `01-paper-handoff.blend`。固定承接面與攝影機；A 貼入、停留呼吸、揭離時 B 在不同位置貼入。時間線 markers 指示貼入、可讀與揭下區間。

方法：細分紙面、儲存在工程內的絕對 Shape Keys、正背面材質、真實接收面陰影。深色條紋代表印刷內容，隨紙面一起變形；**沒有排版歌詞**，避免把字體依賴與幾何教學混在一起。背面有低強度纖維凹凸。

可調入口：`paper()` 的位置、顏色、start、enter_end、release、end；捲曲半徑在 shape-key 建立段；呼吸幅度在逐幀動畫段。改 fps 時必須同步換算事件幀，不只改 render.fps。

驗收／練習：

- 平面部分是否仍留在承接面？捲邊是否連續、內容是否跟著紙面？
- 揭離高光與陰影是否隨曲率改變？暖白背面是否可辨？
- 比較 67–101 幀 A 揭離、77–99 幀 B 貼入的重疊；調整事件時間，找出不同樂句適合的視線交接。
- 最後一小段直接隱藏物件是教學簡化，**不是完整自由飄離解法**。製作鏡頭時補上離場軌跡或讓紙完整離開畫面，避免可見 pop。
- 微幅尺度呼吸只是示範；真實黏附紙張可改成保留接觸點的邊緣彎曲。不要照搬到每張場景貼紙。

## 02 起跳與落地：4 秒

輸出 `02-jump-blocking.blend`。球體是明確的 action proxy，用來看動作弧線，不是人物 rig。

時間點：第 1 幀準備、第 17 幀起跳、第 41 幀頂點、第 65 幀接觸、第 81 幀停穩。從畫面左向右，著地 → 騰空 → 著地。空中段使用拋物線高度，落地緩衝時球底維持地面高度；不依賴外部 physics cache。

驗收／練習：

- 只看輪廓與陰影，能否辨識起跳、落地和緩衝？
- 人為從頂點才開始剪，對照完整動作，觀察缺少準備後理解有何改變。
- 將落地後片段放慢，與原速比較，理解「沒有變形」不等於「速度銜接自然」。
- 這是逐幀烘焙的誇張動作練習，未提供人體生物力學、腳底 IK 或通用物理模擬。

## 03 雙掛點軟布與動畫啞光：5 秒案例

參考 [軟布案例、參數與失敗修正](soft-cloth-animation-example.md)。這是
另一份打包的《Spark》案例工程，不由前述兩樣例生成器建立。它示範
掛點間餘量、柔軟模擬、UV 花紋，以及避免塑膠感的動畫材質。
來源工程與原曲試看位置列於案例文件；藝術驗收仍未完成。

## 重開與驗證

兩份工程均不依赖 frame handler、外部音樂或貼圖。用新 Blender 程序重開並渲染一個中間幀，確認保存的 shape keys／動畫仍生效。檢查影格總數和解碼，另以正常速度觀看。

本輪實際輸出位置見 repository `docs/BLENDER-ANIMATION-GUIDE.md` 的配套樣例連結。預覽不帶音樂；投入 MV 時必須在真實樂句中再次驗收，不把無聲樣例當作節奏已通過。
