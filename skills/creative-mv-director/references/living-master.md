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
3. 把 `living-master-r001.mp4` 給用戶對著歌看。**要等用戶說「有感覺」，才可以花任何一點。** 把日期記進 `CURRENT.md`。30 秒情緒樣片從這支截，不另做。

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
- **每一批付費生成之前**，先把已有素材換進去、重新渲染，給用戶看。

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
