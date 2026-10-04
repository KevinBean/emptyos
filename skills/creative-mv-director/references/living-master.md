# 活母帶操作（階段 3、5、6、12）

檔案格式與上傳檢查寫在 `state-and-ledger.md`；這裡是操作順序。

## v0：任何付費影片之前

1. 從 `analysis/` 的唱句起點表和 treatment 寫 `living-master/plan.json`。每個位置寫出**存在理由**，不是畫面描述：

   ```json
   [
     {"start": 0,    "title": "空廚房，兩副碗筷",   "note": "讓觀眾先知道少了一個人"},
     {"start": 9.6,  "title": "卡座，他一個人",     "note": "話是在這裡說的"},
     {"start": 15.6, "title": "下樓，背對鏡頭",     "note": "主歌進來，開始走"}
   ]
   ```

   第一個從 0 開始。剪點落在唱句起點或句間空隙（經驗庫 §E1）。
2. 建立並渲染：
   ```
   python scripts/mv/living_master.py init   living-master/ --audio <發佈版> --plan living-master/plan.json
   python scripts/mv/living_master.py render living-master/
   ```
3. 做審看頁給用戶對著歌看：
   ```
   python scripts/mv/review_page.py living-master/ --standalone
   ```
   **每一次寄給用戶的活母帶審看 ＝ 這個工具的 `-standalone.html`，寄那一份**（影片、縮圖都內嵌；相對路徑版在別的裝置打不開）。每輪要用戶決定的事、段落、幕、縮圖來源寫在 `living-master/review.json`（格式見腳本開頭）。超過 16 MB 會警告：先做一支 720p 審看版（`ffmpeg -i living-master-rNNN.mp4 -vf scale=1280:720 -crf 28 living-master-rNNN-720p-review.mp4`，同版號的 `*review*` 會被自動選用）。**要等用戶說「有感覺」，才可以花任何一點。** 把日期記進 `CURRENT.md`。30 秒情緒樣片從這支截，不另做。

## 換素材（v1 以後）

```
python scripts/mv/living_master.py swap living-master/ S004 stills/s04.png
python scripts/mv/living_master.py swap living-master/ S004 clips/s04.mp4 --in-frame 12
python scripts/mv/living_master.py approve living-master/ S004
python scripts/mv/living_master.py render living-master/
```

- `swap` 保留位置 id 和目標幀；舊時間線自動存進 `history/`。
- 片段太短、幀率不同，工具會拒絕，不會替你拉伸或循環。改用別的入點、換更長的片段，或回頭改剪輯。
- `approve` 只在用戶看過這一格以後才下。工具會拒絕核准字卡（按 sha256 比對 `cards/`）。
- **每一批付費生成之前**，先把已有素材換進去、重新渲染，用 `review_page.py --standalone` 給用戶看。
- **先有素材池，再剪（用戶 2026-09-28）**：流程是「腳本 → 素材（有餘量：多幾條、多一點時長、多角度）→ 剪輯 → 依素材調整腳本 → 剪輯 → 補素材 → 循環」，不是一格配一支。先寫整首的情緒曲線（每段強度），每支片只演**一個持續的情緒狀態**，不要在 8 秒裡自己醞釀又爆發——曲線靠剪輯組出來。所有生成過的片段都進素材箱，**素材箱就是 MV 庫的片段檔案**（`SCHEMA.md` §2a `profile`：地點、光、服裝、場景細節、站位、首尾動作、可用與壞秒數、強度），用 `scripts/mv/clip_profile.py screen → draft → 目視補完 → record` 建立，不另寫手工表。從箱子粗剪、列缺口、只補缺口；每次給用戶看之前跑 `clip_profile.py joins`（用戶 2026-09-29：片段要有檔案，而且這是標準）。
- **鏡頭是剪點的一半，要按銜接來規劃**：開拍前寫下每場戲的左右站位與背景約定；換素材後做剪點對照（每格來源窗口的最後一幀｜下一格第一幀，從來源取，母帶有佔位標記），逐點判為連續、刻意斷開（段落轉換，寫明理由）或斷掉，記進剪點帳。同一場戲的接續鏡頭，**用前一格選用窗口的最後一幀當下一格的起始幀**生成，不要各自從母版重畫（用戶 2026-09-28：「鏡頭各寫各的，沒有考慮鏡頭銜接」）。
- **每支回來的片段都要決定怎麼用：整段、取一段、還是不用**——不要預設從第 0 幀整段換進去。依密集審片寫下可用區間與壞區間，選 `--in-frame` 讓這一格要的那一拍落在位置裡；可用區間比位置短，才考慮有限放慢（只限自然動作）、讓鄰格讓位或重抽。在庫的 `reason` 記下取哪一段、為什麼。人物鏡頭另跑 `identity_check.py`（對母版＋對自己的起點靜圖），看漂移。

## 交付

```
python scripts/mv/living_master.py verify living-master/ --json
python scripts/mv/living_master.py render living-master/ --final
```

- `--final` 只要還有一格沒核准，就退出 2。這不是可以繞過的警告。
- 後期（調色、字卡、字幕、環境聲）只接 `living-master-final-*.mp4`，或由 `render_shots.py` 從核准片段重算（spec 要設 `living_master`）。側檔會一路傳下去，上傳檢查才認得成片。見 `post-and-grade.md`。

## 常見錯誤

- **做了 v0 卻沒給用戶看。** 渲染出來不等於過關；關卡是用戶那一句話。
- **因為「太靜」就把字卡全刪掉、改用付費片段填滿。** 這是調節奏的問題，不是取消佔位（經驗庫 §E15）。
- **把審看版硬連結進 `release/`。** 上傳腳本會依 sha256 拒絕；正確做法是 `--final` 之後再放。
