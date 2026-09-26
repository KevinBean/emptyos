# 後期（階段 8–11）

六支工具（`render_shots`、`post`、`upscale_crops`、`cards`、`burn_subtitles`、`ambience`）由〈說得太急〉rescue-v18 整理而來，每支腳本開頭都有完整說明。每首歌的決定都寫在兩個檔：`shots.json`（剪輯、調色、裁切）和 `cards.json`（畫面文字、片名、水印）。程式裡不放任何歌曲內容。

## 順序

```
python scripts/mv/render_shots.py plan   post/shots.json               # 只寫 EDL、不渲染
python scripts/mv/upscale_crops.py       post/shots.json               # 有裁切插入鏡才需要
python scripts/mv/render_shots.py render post/shots.json post/picture.mp4
python scripts/mv/cards.py cards.json post/picture.mp4 post/carded.mp4
python scripts/mv/burn_subtitles.py 歌詞.srt post/carded.mp4 post/final.mp4
```

環境聲要在 `render_shots` 之前做好（`ambience.py`），並寫進 `shots.json` 的 `ambience`。

## 要用戶選的，用看的、聽的給

- 調色：同一批鏡頭做並排比較圖（例如 A／B／B+ 遮幅），請用戶選。選定後，再給同一時間點的前後對照。
- 環境聲：先下載 3–4 支真實錄音（要先取得同意），疊在歌曲開頭給用戶試聽，選好再用 `ambience.py` 做成音軌。
- 畫面文字：先寫好句子給用戶看，再排版。文字只放在不唱的空隙、畫面的暗部。

## 預設與理由（rescue-v18 的選擇，可在 spec 裡改）

| 項目 | 預設 | 理由 |
|---|---|---|
| 曝光 | 每鏡對齊到平均亮度 0.165，增益限 0.55–1.35 | 生成片亮度差很多。光線變化本身是劇情的鏡頭（`keep_exposure`）保留原樣 |
| 兩套色 | 現在：冷夜；回憶：暖、淡、4% 霧 | 一剪到回憶，觀眾就知道是過去；12% 霧被嫌太糊 |
| 質感 | 光暈、0.7 px 柔化、粗顆粒、暗角 | 去掉生成片的塑膠感 |
| 遮幅 | 2.39:1，字幕放在下方黑邊 | 用戶選的電影感；字幕和畫面文字分開 |
| 放大 | 30% ESRGAN + 70% Lanczos | 和全畫幅鏡頭的清晰度落在同一範圍 |
| 慢放 | 最低 0.72，而且只給近乎靜止的鏡頭 | 人臉慢放更顯呆（經驗庫 §E2） |

## 工具會擋下的

- 同一素材用兩次、來源時間重疊、速度低於 0.72、裁切比例和畫面不同。
- 幀率不同、片段太短、裁切插入鏡是舊設定做的。
- 任何仍有佔位的渲染，包括改了名的副本。
- 有活母帶的 MV：`shots.json` 要設 `"living_master": "../living-master"`，這樣每支片段都會被確認是核准過的；做完的成片會帶著側檔，上傳檢查才認得。沒有這一項，成片就上傳不了。
