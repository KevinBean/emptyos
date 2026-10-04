# MV 製作指南（全流程）

整理日期：2026-09-26。入口 skill：`creative-mv-director`。

這份文件說明一支 MV **怎麼做**：每個階段做什麼、交什麼、誰批准、用什麼工具。
**為什麼這樣做**寫在 [MV 製作經驗總庫](MV-PRODUCTION-LESSONS.md)（下稱「經驗庫」）：§A 原則、§B 清單、§D 主題索引、§F 案例。
本地 Music Studio 路線的程式契約另見 [MV-GENERATION-WORKFLOW.md](MV-GENERATION-WORKFLOW.md)。

---

## 0. 三條不可跳過的規則

1. **活母帶（living master）先於任何付費影片。**
   第 3 階段做出全長 v0：每個位置先放字卡或靜圖，上面有大字 PLACEHOLDER。用戶對著歌看過、說「有感覺」，才可以花點數生成影片。
   之後每一批付費生成之前，都要先把母帶更新到最新。
2. **佔位不交付。**
   只要還有一個位置沒核准，`living_master.py render --final` 就退出 2。`youtube_push_song.py` 會拒絕任何名為 `living-master-r*` 的檔，以及任何 sha256 對上「佔位數大於 0」渲染側檔的檔（所以改名的硬連結也擋得住），`--skip-qa` 和 `--force` 都跳不過。
   在有活母帶的 MV 裡（`release/` 旁邊有 `living-master/`），上傳的檔還必須能追溯到一支沒有佔位的渲染：
   - `cards.py`、`burn_subtitles.py` 會把側檔「傳下去」；
   - `render_shots.py` 在 spec 設了 `living_master` 時，只接受在活母帶裡核准過的片段，並寫出最終側檔。
   所以工具以外的重新編碼、或拿沒核准的片段直接做後期，一樣上傳不了。**擋不住的**只剩：刪掉 `living-master/` 資料夾，或刪掉側檔。
   佔位畫面上的燒錄標記是最後一道防線。
3. **「太靜」是節奏問題，不是禁用靜圖。**
   解法是換鏡、插入鏡、動態空鏡，不是刪掉佔位、不是跳過 v0。
   《說得太急》把「太靜」當成禁令，結果一直沒有做全長母帶（經驗庫 §F）。

---

## 1. 兩條路線

| | Flow／Veo 路線（主線） | Music Studio 本地路線 |
|---|---|---|
| 用在 | 2026-09-12 起交付的全部 MV | 本地 FLUX＋I2V 實驗、Porch-Light-Low、那道彩虹 |
| 生成 | 瀏覽器 Google Flow（Nano Banana 靜圖、Veo 影片），Blender 補動作 | `apps/personal/music-studio`（僅本機，不在公開版） |
| 剪輯組裝 | `scripts/mv/living_master.py` | Music Studio rough-cut |
| 共用 | 審看與交付母帶都用同一個逐幀 EDL 組裝器 `emptyos/sdk/media/edl.py`；Flow 路線的調色成片另由 `render_shots.py` 從核准片段重算 | 同左（組裝器） |
| 入口 skill | `creative-mv-director` → `tool-flow-stills` 等 | `creative-mv-director` → `creative-mv-generator` |

`living_master.py` 只剪輯組裝，**不生成、不核准**。它不是第二條生產管線。

---

## 2. 資料夾標準

每支 MV 一個資料夾（通常在歌曲筆記旁，例如 `mv-flow-YYYYMMDD/`）：

```
mv-flow-YYYYMMDD/
├── CURRENT.md                  # 階段狀態（director skill 讀寫）
├── song-treatment.md           # 情緒命題、主線、唱／不唱分流
├── art-direction.md            # 色彩分區、參考、字體
├── <歌名>-mv-cost-ledger.md    # 成本賬，提交當下記
├── analysis/                   # 歌詞時間軸、段落、唱句起點表
├── living-master/
│   ├── living-master-timeline.json   # 位置表（schema v1）
│   └── living-master-rNN.mp4 (+ .living-master.json 側檔)
├── stills/  clips/  lipsync/  post/  audio/
└── release/                    # release-package.md、母帶硬連結、縮圖
```

`CURRENT.md` 與成本賬的模板在 `skills/creative-mv-director/references/state-and-ledger.md`。

---

## 3. 階段

每個階段依序列出：**目標／輸入／產出／關卡／工具／預算／常見失敗**。
關卡沒過，director skill 不進下一階段。

### 階段 0：開案與預算
- **目標**：確定這支 MV 做什麼、花多少、誰可以上傳。
- **輸入**：發佈版音訊、歌詞筆記。
- **產出**：
  - `CURRENT.md`（stage: 0）
  - 成本賬：上限、證明預算、修補預留，例如 1,000／100／200
  - 上傳授權記錄
- **關卡**：用戶確認預算數字。**不買點數**，除非用戶明確說可以。
- **工具**：`creative-youtube-channel`（歌曲筆記）。
- **常見失敗**：
  - 腳本照手稿寫，不照發佈版。光。可見的音源被截，不可說的發佈版沒有 Bridge。
  - 先花錢後記帳。

### 階段 1：歌曲分析
- **目標**：知道每一秒在唱什麼，剪點可以落在哪裡。
- **產出**（放在 `analysis/`）：
  - 歌詞時間軸
  - 段落表
  - 每小節響度
  - **唱句起點表**，第 8 階段剪點用
- **關卡**：抽 3 句耳聽核對時間。
- **常見失敗**：用小節網格代替唱句起點。光。可見 52 句裡有 25 句被剪斷（經驗庫 §E1）。

### 階段 2：風格與 treatment
- **可選探索**：若已有起點與預期結尾，但中間的畫面關係尚未成立，可用 [improvisation pass](CREATIVE-IMPROVISATION-METHOD.md) 手動探索；Music Studio 也提供兩條 AI 候選路徑。兩條製作路線都只把選中的元素寫進 treatment。這是待驗證的創作方法，不代替情緒命題與主線批准。
- **目標**：一句話的情緒命題、一條主線，並且定出這支片的視覺語言。
- **產出**：`song-treatment.md`、`art-direction.md`，內容包括：
  - 現在／回憶色彩分區
  - 人物鏡比例
  - **每個段落是唱（SING）還是不唱（ACT）**
  - 片頭、字幕、水印樣式（同一張 EP 沿用已發佈的樣式）
- **關卡**：用戶批准情緒命題與主線。
- **工具**：`creative-mv-art-director`。
- **常見失敗**：換地點當成推進故事；沒有主線（說得太急）。

### 階段 3：活母帶 v0（0 點）
- **目標**：用戶在花任何一點之前，就能對著歌看到全長的片子。
- **做法**：先寫 `plan.json`，依歌曲順序列出每個位置 `{"start": 秒, "title": "這鏡要讓觀眾感到什麼", "note": "…"}`，第一個從 0 開始（見 `scripts/mv/living_master.py` 開頭說明）：
  ```
  python scripts/mv/living_master.py init   living-master/ --audio <發佈版> --plan plan.json
  python scripts/mv/living_master.py render living-master/
  ```
  - 每個位置有 `stable_id` 和目標幀區間，先放字卡（寫鏡頭意圖）或免費靜圖。
  - 畫面上燒錄 PLACEHOLDER 標記。
- **給用戶看**：每一次活母帶審看都用審看頁，**寄出 standalone 那一份**（影片和縮圖都內嵌，換一台裝置也打得開；相對路徑版在別處只剩「沒影片沒圖」）：
  ```
  python scripts/mv/review_page.py living-master/ --standalone
  ```
  逐句歌詞、剪點品質、每輪要用戶決定的事寫在 `living-master/review.json`（格式見腳本開頭）。超過 16 MB 會警告：先做一支 720p 審看版（`ffmpeg -i living-master-rNNN.mp4 -vf scale=1280:720 -crf 28 living-master-rNNN-720p-review.mp4`，同一版號的 `*review*` 會被自動選用）。
- **產出**：`living-master-r001.mp4`＋側檔（記錄佔位數量）。在 `CURRENT.md` 記下用戶說「有感覺」的日期，這是之後每次付費前要先查的一行。
- **關卡**：**用戶對著歌看完，說「有感覺」。這是第一個付費關卡。**
- 30 秒情緒樣片從這裡取，不另做。
- **常見失敗**：
  - 把全長剪輯排在花錢之後（說得太急 v2 計畫）。
  - 佔位版被當成成片交出去。

### 階段 4：場景與人物
- **目標**：場景和人物先在靜圖階段定下來，之後不再變。
- **做法**：
  - 主景先做滿生活感，其他角度從主景衍生，逐區核對跨角度的物件。
  - 人物以一張核准近景為身份母版；所有人物鏡只從這條身份鏈派生。每張新人物圖都用 `scripts/mv/lipsync/identity_check.py --ref 主角=母版.png --expect 主角 新圖.png` 比對：中位數 ≥ 0.45 通過，0.35–0.45 要問用戶，低於 0.35 失敗；另外要看工具附的下巴放大圖（鬍子、年齡，ArcFace 看不出來）。
  - **絕不用重畫角色的方式造新機位**（經驗庫 §A5、§E9）。
  - 每個場景留一張無人臉的空鏡或窗景，給剪輯當插入鏡。
- **工具**：`creative-mv-art-director`、`tool-flow-stills`、`tool-blender-scene-reference`。
- **預算**：靜圖免費（Flow），0 點。

### 階段 5：靜態畫面
- **目標**：每個位置都有一張核准過的畫面。
- **做法**：每張審過的靜圖用 `swap` 換掉字卡，母帶升為 v1：
  ```
  python scripts/mv/living_master.py swap living-master/ <stable_id> stills/<file>.png
  ```
- **關卡**：用戶看 v1（`review_page.py --standalone`，同階段 3）。靜圖本身要能單幀成立。

### 階段 6：動態影片（付費）
- **目標**：只為值得動的鏡頭花錢，而且先證明做得到。
- **證明**：
  - 樣片跨時長選：短、中、長各一個，再加最難的一個（經驗庫 §E4）。
  - 避開模型弱項：人與剛體互動、全身跳躍、複雜舞步（§A3）。
- **停損**：同一問題最多試三次；連續兩次同類失敗就換做法（§E3）。
- **關卡**：**每一批付費生成前，母帶已經換上最新素材，並且用戶看過。**
- **記帳**：每次提交當下記 job ID、報價、實扣或退款。
- **工具**：Flow Veo；動作改走 `tool-blender-comfyui-video`。本地路線用 `creative-mv-generator`。
- 片段用 `swap` 放入。**來源不夠長或幀率不同就拒絕**，不拉伸、不循環。用戶看過這一格以後才 `approve`；字卡永遠不能核准。
- **產出：每支留下的片段都有目視過的片段檔案**（MV 庫 `SCHEMA.md` §2a 的 `profile`；庫就是素材箱，不另寫 `footage-bin.md`）。它記錄地點與場景版本、光的狀態、每個人的左右站位與服裝、場景細節（窗戶樣式、號誌在哪一側）、首尾動作、可用區間與壞秒數：
  ```
  python scripts/mv/clip_profile.py screen clips/*.mp4 --ref 她=母版.png --ref 他=母版.png
  python scripts/mv/clip_profile.py draft clips/S008.mp4 --asset-id 歌名:S008 --location 排練室 --project 歌名 > S008.json
  #   看 S008.sheet.jpg 補完光、服裝、場景細節、動作、壞秒數，填 reviewed
  python scripts/mv/clip_profile.py record S008.json
  ```
  機器只填量得到的（人、左右、身份、跳幀）。光、服裝、場景細節靠人看縮圖；沒填光的草稿不能寫入。

### 階段 7：對口型（只限 SING 鏡頭）
- **目標**：要唱的鏡頭嘴對得上，不唱的鏡頭嘴是閉的。
- **做法**：`scripts/mv/lipsync/`，由 vault 裡 One More Hour 的 lipsync-test 工具整理而來。重跑短劇測試的 S3，分數和當時一樣是 6.797 對 2.114。
  - 生成：`infinitetalk_workflow.py '<json>'`，先加 `--print` 看圖再送出。雙人畫面一定要給 `masks`（左、右、背景），否則工具拒絕。
  - 驗收：`lipsync_check.py 影片 本句.wav 錯句.wav --frames <來源幀數>`。錯句要用同一個聲音唱的另一句；`--frames` 要給**來源**幀數，因為 InfiniteTalk 的輸出會補到整個窗。全部條件同時成立才過：
    - LSE-C ≥ 4.5
    - 本句分數 − 錯句分數 ≥ 2.0
    - |offset| ≤ 6 幀，而且最佳位移不在搜尋邊界
    - 長句（至少兩個 81 幀窗）逐窗再量一次，每一窗都要 LSE-C ≥ 3.5，抓出越唱越不同步的情況
  - 退出碼：0 通過、1 失敗、3 無法判定、2 量不了（輸入壞了、缺模型或套件）。
  - 兩個音檔都要用**人聲分軌**，不要用混音：伴奏會被當成人聲，SyncNet 也沒在混音上驗證過。門檻是用說話的短劇台詞校準的，用在唱歌上還沒驗證。
- **前提與例外**：
  - 唱句（從第一個到最後一個有聲段）至少 1 秒；更短分不出對錯，判「無法判定」。錯句也不能比這更短，否則同樣判「無法判定」。
  - 一半以上的幀找不到臉，判「無法判定」；完全找不到臉，判失敗（臉在生成中崩掉或消失是真缺陷）。
  - 臉低於 120 px 的鏡頭至今沒有通過過，工具會提出警告；要唱的鏡頭先排成近景。
- ACT 鏡頭用 `openness.py 影片 --closed-windows 0-4.5,9-12` 看那幾段的張嘴程度，有異狀再用眼睛確認。這個代理指標只拿來找問題，不當驗收。
- **常見失敗**：同一個人一會兒唱一會兒閉嘴（§A4）；拿 Haar 張嘴程度當驗收（短句分不出對錯）。

### 階段 8：剪輯
- 剪點落在唱句起點或句間空隙，**句中剪點數必須為 0**。
- 相鄰兩鏡的行進方向、光、同一物件都要一致；避免構圖幾乎一樣的鄰鏡。
- **每次給用戶看之前，手動跑 `clip_profile.py joins living-master/`**（沒有工具會自動跑它，是交出審看頁前的一步），要 0 個未處理的衝突。它逐個剪點比對（中間夾的靜圖跳過）：同地點的光（狀態、主光方向、色溫）、同地點兩個人的左右對調、同一支來源裡跳掉的秒數；並拿每格跟**這個地點、這個人上一次出現**比（場景版本與細節、服裝），不只跟前一格比。每格用到的秒數碰到壞秒數或已記的缺陷也算。影片格沒有檔案或只有草稿都算未通過；靜圖和字卡只計數。它**看不出**陌生人闖入或臉變了——那些要先在目視時記成 `defects`，之後才擋得住。刻意的轉變在 `plan.json` 剪點後那一格寫理由：剪點上的變化（例如燈在剪點上變）用 `"join_ok"`，跟更早一格比出來的變化（例如換幕換裝）用 `"change_ok"`；兩者都不能免掉缺陷。〈在你說完以前〉v7 用戶的 18 條鏡頭意見裡，一半是這一類（窗戶樣式、上衣顏色、打光、號誌位置、闖入的人、同一支片跳接），當時沒有任何紀錄能事先發現。
- 人臉鏡用原速；補時長用插入鏡。慢放只用於近乎靜止的環境鏡，速度 ≥ 0.72。
- 同一段素材只用一次。
- **工具**：`render_shots.py plan shots.json` 只寫出 EDL、不渲染。它會檢查每鏡的幀數算術和最低速度 0.72，同一素材同一構圖不得用兩次，同一素材的來源時間也不得重疊，檢查不過就拒絕。

### 階段 9：調色與後期
- **做法**：
  - 先做每鏡曝光匹配（目標 0.165，增益限制在 0.55–1.35），再做風格化：現在／回憶兩套色、光暈、4% 霧、細顆粒。
  - 需要時加 2.39 遮幅，字幕放在下方黑邊。
  - 裁切放大的插入鏡用 30% ESRGAN＋70% Lanczos。
- **關卡**：給用戶看同一鏡頭的並排比較圖選方案，選完再做前後對照。
- **工具**：先跑 `upscale_crops.py shots.json`，預先渲染裁切插入鏡。每支插入鏡旁邊有 `.json` 印記，記下它是用哪些設定做的；設定一變就重做。再跑 `render_shots.py render shots.json 輸出.mp4`：它先逐一檢查素材幀率、長度、印記與佔位，再做逐鏡曝光匹配、套色、合成和環境聲混音。調色字串在 `post.py`。這些工具都由《說得太急》rescue-v18 整理而來。用同一份 v18 鏡頭表重跑，得出的 ffmpeg 指令、每鏡曝光增益都和當時相同；抽一支裁切插入鏡重做，也和當時逐位元相同。

### 階段 10：文字
- 片名、畫面文字、字幕、水印分成四層，最後在交付解析度燒錄。
- 畫面文字與字幕要能一眼區分：例如畫面文字直排、放在暗部，只出現在不唱的空隙。
- **工具**：`cards.py cards.json 畫面.mp4 輸出.mp4`（畫面文字、片名、水印；文字全寫在 cards.json，不寫進程式），`burn_subtitles.py 歌詞.srt 輸入.mp4 輸出.mp4`（雙語字幕，重疊的句子會被截短，同一時間只顯示一句）。繪製都在 `text_render.py`。
  - 用 PIL 產生疊加圖，不用 ffmpeg drawtext／subtitles：救回時 drawtext 因 fontconfig 崩潰過（2026-09-26 簡單測試 subtitles 能用，但 PIL 版每台機器畫出來都一樣）。
  - 字體路徑從 `emptyos.toml [mv_tools.fonts]` 讀取（`scripts/mv/mv_config.py`）。

### 階段 11：聲音
- 原曲不替換、不重生成。
- 環境聲一律用真實錄音：下載 3–4 個候選，疊在歌曲開頭給用戶試聽選擇。
- 混音用 `amix normalize=0`，並逐秒核對歌曲電平沒有變。
- **工具**：`ambience.py 錄音.mp3 bed.wav --duration <歌長> --segment 開始:結束:取材起點:淡入:淡出 …`。每段各自取錄音裡不同的一段，免得聽得出重複；再把每段拉到同一個 RMS 電平。下載素材要先得到用戶同意。

### 階段 12：交付與發佈
```
python scripts/mv/living_master.py render --final living-master/   # 有未核准位置 → 退出 2
python scripts/youtube_push_song.py "<release 資料夾>"              # dry run
python scripts/youtube_push_song.py "<release 資料夾>" --yes         # 私享上傳
```
- **驗證**：完整解碼、幀數、尺寸、音訊與母帶一致；ffprobe 要上傳的那個檔。
- **排程**：定時公開，並加上 AI 合成聲明。
  - 頻道固定週三或週六 20:00（雪梨）。
  - 轉公開只在 Studio 手動做。
- **工具**：`creative-youtube-channel`。

### 階段 13：結案
- 結清成本賬，更新 CURRENT。
- 在經驗庫 §F 加一個案例，新規則併入 §B–§D；與舊規則衝突的寫進 §E。

---

## 4. 救回（已判失敗的 MV）

照經驗庫 §C 的順序：
1. 診斷主線。
2. 刪到只剩這條線。
3. 只取乾淨區間。
4. 用插入鏡補時長。
5. 用畫面文字與真實環境聲補脈絡。
6. 小步快跑，每輪只改用戶點名的時間碼。

救回同樣從活母帶開始：先把現有可用素材放進 `living-master-timeline.json`，缺口放字卡，再逐一替換。

---

## 5. 需要用戶決定的事

| 決定 | 階段 | 形式 |
|---|---|---|
| 預算上限、是否可買點數 | 0 | 數字 |
| 上傳參考素材到 Flow 專案 | 0 | 一次授權 |
| 情緒命題與主線 | 2 | 批准 treatment |
| v0 有沒有感覺 | 3 | 看全長母帶的 standalone 審看頁（第一個付費關卡） |
| 每一批付費生成 | 6 | 看更新後母帶的 standalone 審看頁＋報價 |
| 調色方案 | 9 | 並排比較圖 |
| 環境聲 | 11 | 試聽候選 |
| 下載外部素材 | 任何階段 | 逐項同意 |
| 發佈時間 | 12 | 排程 |

藝術取捨以外的技術判斷（剪點、曝光、工具選擇），由 director 自己決定並記錄理由。

---

## 6. 工具狀態

| 工具 | 狀態 |
|---|---|
| `scripts/mv/mv_config.py` | 已完成 |
| `emptyos/sdk/media/edl.py`、`living_master.py`、`text_render.py`（字卡、佔位標記）、上傳佔位檢查 | 已完成（第 1 期） |
| `post.py`、`render_shots.py`、`cards.py`、`burn_subtitles.py`、`upscale_crops.py`、`ambience.py` | 已完成（第 2 期）。與 rescue-v18 對照：指令、增益、字卡／字幕 PNG、雨聲逐位元相同；插入鏡抽驗一支相同 |
| `scripts/mv/lipsync/`（`syncnet.py`、`lipsync_check.py`、`openness.py`、`infinitetalk_workflow.py`、`face_onnx.py`） | 已完成（第 3 期）。S3／S2 分數、openness、四種 InfiniteTalk 圖都和原工具相同 |
| `scripts/mv/review_page.py`（活母帶審看頁，`--standalone` 內嵌版） | 已完成 |
| `scripts/mv/lipsync/identity_check.py` | 已完成（第 4 期）。與原工具數字相同；門檻以兩個角色的已知分數校準，「複查」只提問不擋流程 |
| `scripts/mv/clip_profile.py`（片段檔案 `screen`／`draft`／`record`，剪點檢查 `joins`） | 已完成（2026-09-29）。檔案存在 MV 庫的素材記錄；`check_mv_library.py` 列出還沒有檔案的使用中片段（只提示） |

所有工具的完整用法寫在各腳本開頭；操作順序見 `skills/creative-mv-director/references/`。
