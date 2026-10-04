# CURRENT.md 與成本賬

## CURRENT.md

**「現況」段只描述現在，每次覆寫。** 歷史追加到最後的「日誌」段，一行一筆。
《說得太急》的 CURRENT 把每一版的歷史都堆在最上面，讀的人要翻好幾屏才知道現在的狀態。這個格式就是為了避免這種情況。

```markdown
# 〈歌名〉MV — CURRENT

## 現況（每次覆寫）
- 路線：Flow／Veo | Music Studio
- 階段：3 活母帶 v0
- 上一個已過關卡：2 treatment 批准（YYYY-MM-DD）
- 下一個關卡：用戶看 v0 說「有感覺」
- 還缺：…
- 母帶：living-master/living-master-rNN.mp4（佔位 N／共 M 位置）
- 預算：上限 1,000｜已用 0｜證明 100｜修補預留 200
- 授權：上傳參考素材到 Flow（是／否）；買點數（否）

## 已退役（不得帶入新版）
- vN：原因

## 日誌（追加）
- YYYY-MM-DD 階段 2 → 3：treatment 批准；v0 渲染 r001
- YYYY-MM-DD 階段 3 過關：用戶看 v0 說「有感覺」← 付費前必查
```

階段編號與 `docs/MV-PRODUCTION-GUIDE.md` §3 一致（0–13）。

## 成本賬 `<歌名>-mv-cost-ledger.md`

**提交當下就記**，不要事後補。不知道的寫「未知」，不寫 0。

```markdown
| 日期 | 階段 | 項目 | job ID | 報價 | 實扣 | 退款 | 提交前餘額 | 提交後餘額 | 用途／結果 |
|---|---|---|---|---|---|---|---|---|---|
```

- 每批提交前讀一次餘額，寫進「提交前餘額」。
- 斷線或出現低點數警告後，先查列表與餘額，再決定要不要重送。
- 結案時加「結算」段：總實扣、成片實際用到的素材比例、上限剩餘、這次的超支原因。

## 活母帶檔案（`scripts/mv/living_master.py`）

用法寫在腳本開頭。這裡只記你讀檔、判斷時要知道的事。

- `living-master-timeline.json`（schema 1，欄位名與 Spark 相同）：
  - `destination_start_frame` / `destination_end_frame` 是半開區間，從 0 首尾相接到歌曲總幀數；
  - 來源區間 `end_frame − start_frame` 永遠等於位置長度：不拉伸、不循環；
  - `source_kind` 是 `still` 或 `video`，字卡就是 `still`；
  - `production_approved` 只有人會設（`swap --approve` 或 `approve`），工具自己從不設。
- 每次 `swap` / `approve`，舊時間線都會存進 `history/living-master-timeline-rNNN.json`，然後修訂號加一。
- `init` 絕不覆寫已存在的時間線。
- 審看版叫 `living-master-rNNN.mp4`；每個未核准位置的整段幀，右上角都燒了 `PLACEHOLDER <stable_id>`。
- 最終版叫 `living-master-final-rNNN.mp4`。只要還有未核准位置，`render --final` 就退出 2。
- 每次渲染旁邊都有側檔 `<母帶檔名>.living-master.json`，記錄 `master_sha256`、`placeholder_slots`（未核准位置數）、`final`。

**上傳檢查**：`youtube_push_song.py` 在連 YouTube 之前會拒絕：
1. 檔名像審看版（`living-master-r*`）的檔；
2. sha256 對上某個「佔位數大於 0」側檔的檔。搜尋範圍是母帶往上兩層的資料夾，加上你給腳本的歌曲資料夾，往下遞迴。所以改名的硬連結也擋得住；
3. 在有活母帶的 MV 裡（`release/` 旁邊有 `living-master/` 和時間線），任何追溯不到無佔位渲染的檔。

第 3 點靠「側檔傳下去」：`cards.py`、`burn_subtitles.py` 的輸入若對上無佔位的側檔，會替輸出寫一份 `final: true`、記下 `derived_from` 的側檔；`render_shots.py` 在 spec 設了 `living_master` 時，只接受核准過的片段，並替成片寫側檔。`--skip-qa` 和 `--force` 都跳不過。

全部位置都核准後，審看版和最終版通常逐位元相同，所以側檔檢查不會擋它；但檔名檢查仍擋 `living-master-r*`，上傳一律用最終版。

擋不住的：刪掉側檔或 `living-master/` 資料夾。沒有活母帶的影片（例如靜態視覺化版、舊 MV）刻意放行，這是和「找不到側檔就當未核准」相反的取捨，為的是不擋住非 MV 的上傳。

**核准**：`approve` 和 `swap --approve` 會拒絕任何 sha256 等於 `cards/` 裡字卡的來源；字卡永遠不能核准。核准只在用戶看過那一格以後做。

參考：Spark 的 r96 時間線（60 個位置、5637 幀）可以通過 `verify`，渲染出來也和已交付的 v96 一樣是 5637 幀、逐幀 PSNR ≥ 51 dB（徽章角落除外）。它存放在 EmptyOS 未進 git 的 `data/mv-proof-inspect/` 工作區，公開 clone 裡沒有。
