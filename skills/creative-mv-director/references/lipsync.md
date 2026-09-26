# 對口型（階段 7，只限 SING 鏡頭）

工具在 `scripts/mv/lipsync/`，每支腳本開頭都有完整說明。這裡是判斷順序。

## 開拍前

- treatment 已經標出每段是 SING 還是 ACT。同一個人**不要**一會兒唱、一會兒閉嘴（經驗庫 §A4）。
- 要唱的鏡頭排成近景：臉低於 120 px 的鏡頭至今沒有通過過。
- 準備**人聲分軌**（demucs 之類），不要用混音。每個 SING 鏡頭要兩段人聲：本句，加上同一個聲音唱的另一句當錯句。兩段都要超過 1 秒。

## 生成

```
python scripts/mv/lipsync/infinitetalk_workflow.py '<json>' --print    # 先看圖
python scripts/mv/lipsync/infinitetalk_workflow.py '<json>'
```

- 用 `steps 6 / start_step 0`。`start_step 2` 會保留原片的嘴型，對嘴就失效了。
- 要唱的人要寫 `pos`；預設提示詞是「一個男人對鏡頭唱」。
- 雙人畫面：一定要給 `masks`（左、右、背景）和 `pos`。
- 輸出會補到整個窗，比要求的長。剪的時候切到人聲結束的地方。

## 驗收

```
python scripts/mv/lipsync/lipsync_check.py 片段.mp4 本句.wav 錯句.wav --frames <來源幀數>
```

| 結果 | 退出碼 | 下一步 |
|---|---|---|
| 通過 | 0 | 還要**用眼睛**看：牙、笑、嘴角撕裂、身份。分數看不出這些 |
| 失敗 | 1 | 最多重試三次；連續兩次同類失敗就換做法（經驗庫 §E3） |
| 無法判定 | 3 | 句子太短、錯句太短或臉太少。改用較長的句子或更近的景，不要硬判 |
| 量不了 | 2 | 輸入或環境有問題，先修好再量 |

- 閉嘴鏡頭（ACT）：`python scripts/mv/lipsync/openness.py 片段.mp4 --closed-windows 0-4.5` 看有沒有張嘴。這只是找問題的代理指標，有異狀要用眼睛確認。
- 身份：`python scripts/mv/lipsync/identity_check.py --ref 主角=核准近景.png --expect 主角 片段.mp4`。「複查」要問用戶，不能直接擋掉；而且要看下巴放大圖，確認沒有長出鬍子、沒有老化。

## 誠實說明

- 你聽不到聲音。「嘴對得上」是量出來的，要寫成「SyncNet 通過」，不要寫「聽起來對」。
- 這些門檻是用說話的短劇台詞校準的，用在唱歌上尚未驗證。第一次用在唱歌上時，每一個通過都要讓用戶看過，並把結果記進經驗庫 §F。
