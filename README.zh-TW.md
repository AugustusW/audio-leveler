# audio-leveler

> **忽大忽小的語音錄音 → 音量一致。先量測，才知道該修的是哪一種毛病。**

[English](./README.md) | 繁體中文

[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)](#安裝)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-skill%20%2B%20plugin-orange.svg)](https://claude.com/claude-code)

一個 agent skill——採開放 [SKILL.md 標準](https://developers.openai.com/codex/skills)，
為 [Claude Code](https://claude.com/claude-code) 打造——用來修 podcast、演講、訪談、
會議錄音那種「聲音忽大忽小」的問題。它**先量測響度**，再套用對症的處理階段，
然後**重新量測自己的產出**，告訴你到底有沒有改善。

## 為什麼？

聽 podcast 每隔幾分鐘就要伸手調音量，是很糟的體驗。但「音量不一致」不是一種毛病，
是好幾種，而且對症的做法彼此無效。

開發時用的那一集，整體響度量出來是 `-16.8 LUFS`——本來就正好是 podcast 慣例值。
網路上流通的 `loudnorm` 一條龍調的就是這個數字，所以每一種做法套上去，那集聽起來
都跟原本一樣難聽。真正的問題在下一層：響度在**每個段落內部**移動的幅度。

所以這個工具不從處理開始，而是從量測開始，並把數字交給要做決定的人。

## 特色

- **先量測再決定。** 三個數字——整體寬度、段落之間的漂移、段落之內的起伏——把需要
  不同做法的毛病分開。
- **程式裡不含判斷。** `measure.py` 只出數字，`apply.py` 只執行明確參數，決定寫在
  `SKILL.md` 交給宿主模型做。
- **每次產出都會被檢查。** `apply` 會重新量測自己的輸出，回報收斂、沒有變化、或更差。
  無效的處理不會被講成成功。
- **階段可以疊。** `--filter segmented,speech` 依序修段落之間的落差與段落之內的起伏。
- **全程兩段式 linear `loudnorm`**，不用會產生抽送感的 dynamic 模式。
- **來源**：本地檔、Apple Podcasts 單集連結、任何 yt-dlp 抓得到的 URL——依集數身分
  快取，所以先 measure 再 apply 只會下載一次。

## 安裝

需要 [ffmpeg](https://ffmpeg.org/)（附帶 `ffprobe`），只有在傳 URL 時才需要
[yt-dlp](https://github.com/yt-dlp/yt-dlp)。

```bash
brew install ffmpeg          # macOS；Debian/Ubuntu 用 apt install ffmpeg
pip install yt-dlp           # 選用，處理 URL 來源時才需要
```

工具不會自動幫忙安裝。缺哪一個會明確講出來，並附上安裝方式。

## 用法

```bash
python3 skills/audio-leveler/scripts/cli.py measure <來源>
python3 skills/audio-leveler/scripts/cli.py apply <來源> --filter speech
```

Windows 上的直譯器通常是 `python` 或 `py -3`，不是 `python3`。另請注意 Windows
不在已驗證的範圍內，見[狀態](#狀態)。

`<來源>` 可以是本地音訊或影片檔、Apple Podcasts 單集連結，或任何 yt-dlp 抓得到的
URL。下載會依集數身分快取，所以同一個連結先 measure 再 apply 只會下載一次。

### measure

```
Source: 0:54:07, 2 channel(s)
Integrated loudness: -16.8 LUFS (LRA 9.2 LU)

Short-term loudness (3s window, gated):
  spread (p95 - p5): 10.0 LU
  drift  (between 6-minute windows): 3.9 LU
  intra  (within a window, median): 15.5 LU
  percentiles: p5 -22.6 / p25 -20.5 / p50 -18.6 / p75 -16.1 / p95 -12.6 LUFS
  speech ratio: 100% of samples above the gate

Channels: stereo kept: channel separation 24.9 dB. Below the 60 dB margin
required to call it fake stereo, so a downmix could lose content.
```

`intra` 遠大於 `drift`，代表響度是在**每個段落內部**移動，而不是段落與段落之間有
落差——講者與麥克風的距離在變，或是多人音量不一。這正是 `--filter speech` 對症的
狀況。

加 `--json` 會改為輸出原始診斷契約，供模型或腳本讀取。

### apply

`--filter` 為必填，而且**刻意不提供 `auto`**：沒有東西在讀那些數字時，這個工具不猜。

| 階段 | 適用 | 做了什麼 |
|---|---|---|
| `speech` | `intra` > `drift`：段落內起伏 | `speechnorm`，再兩段式 linear `loudnorm` |
| `segmented` | `drift` > `intra`：段落間有落差 | 由素材自己的逐窗響度算出增益曲線，平滑後隨時間套用，再兩段式 linear `loudnorm` |
| `loudness` | 兩者都小，只是整體音量不對 | 只做兩段式 linear `loudnorm` |

階段可以疊，依序套用：`--filter segmented,speech` 同時修段落之間的落差與段落之內的
起伏。`loudness` 的意思是「前面什麼都不加」，不能跟別的疊。

實測（前後兩半差 18 LU 的素材，原始 spread 18.5 LU）：

| | 處理後 spread | 處理後 drift |
|---|---|---|
| `speech` | 17.5 | 17.1 |
| `segmented` | **4.0** | **0.0** |
| `segmented,speech` | **3.9** | **0.0** |

對照組：ffmpeg 內建的動態處理濾鏡在同一支素材上都差得遠——`dynaudnorm` 最好到
16.4 LU、`compand` 16.9、`speechnorm` 17.5。它們被設計成溫和地移動增益，而持續
18 dB 的修正正是它們刻意不做的事。

每次產出結束後都會重新量測輸出檔，並回報三種結局之一：收斂 N%、幾乎沒有變化、
變得更差。沒有改善就會如實這樣講——工具不會在沒有證據的情況下，把自己的產出說成
成功。

輸出格式依 `--out` 的副檔名決定——`mp3`、`m4a`、`wav`、`flac`。`--out talk.wav` 就是
真的寫出 WAV，無損格式也不會帶位元率設定。沒給 `--out` 時預設是來源同目錄的
`<原檔名>-leveled.mp3`。不支援的副檔名會在開工前直接拒絕，而不是默默寫成別的格式。

其他旗標：`--out PATH`、`--target-lufs`（預設 −16）、`--mono auto|force|never`、
`--force` 覆寫既有檔案。

## 運作原理

1. **`ebur128`** 每 0.1 秒回報一次 short-term 響度（3 秒滑動窗）。靜音的濾除門檻是相對於
   檔案自身的 integrated 響度，而不是固定值——固定門檻會在整體音量改變時讓統計母體跟著
   變，處理前後的比對就失去意義。
2. **從這條序列算出三個統計量**：`spread`（p95 − p5）、`drift`（窗與窗之間）、
   `intra`（窗之內）。統計窗長隨素材長度縮放。
3. **假立體聲判斷量的是 L−R 差訊號**，不是比對兩聲道的 RMS。RMS 是能量統計量，延遲一個
   聲道不會改變它，所以聽得出來的立體聲兩聲道 RMS 也可能只差 0.0012 dB。
4. **由你（或宿主模型）選擇處理階段。** 沒有自動模式。
5. **產出是一條 filtergraph**：單聲道降混在最前面（true-peak 超取樣的成本隨聲道數），
   接著是選定的階段，最後是 `linear` 模式的兩段式 `loudnorm`。
6. **重新量測產出**並與輸入比對。

`linear` 模式有三個前提，而 ffmpeg 一個都不會講——它會靜默改用這個工具存在就是為了避開的
dynamic 模式。工具自己檢查這三個前提，在目標達不到時**於開算之前**就拒絕，並給出一個
真的可用的目標值。

## 當作 skill 使用

判讀數字的指引寫在 `skills/audio-leveler/SKILL.md`。程式裡完全不含判斷：
`measure.py` 只產生數字，`apply.py` 只執行明確給定的參數，決定數字代表什麼發生在
兩者之間。會這樣切，是因為那些門檻來自很少量的錄音——寫死在程式裡等於把那幾支
錄音的特性一起凍進去。

## 已知限制

以下是刻意如此，不是測試不足——後者見[狀態](#狀態)。

- **只處理語音。** 音樂的動態範圍是創作意圖，不是缺陷。
- 這個版本**不做降噪**。
- **`--filter` 沒有自動模式。** 沒有模型在讀量測結果時，這個工具不猜，一律要求明確指定。
- **`drift` 要有一個以上的窗才有意義。** 窗長會隨素材長度縮放，但短於約兩分鐘的素材仍然
  只切得出少數幾個窗。
- **`segmented` 會把安靜段落推上來**，所以預設的 −16 LUFS 目標比較常達不到。工具會明講
  並給出一個真的可用的目標值。
- **超過兩聲道**時跳過假立體聲判斷；`--mono force` 仍會依該 layout 的正確係數降混。

## 狀態

v0.1.0（[CHANGELOG](./CHANGELOG.md)）——165 條測試，其中 159 條完全離線（ffmpeg、ffprobe
與 yt-dlp 都被 mock，不需網路也不需媒體檔）。其餘 6 條會真的呼叫 ffmpeg，CI 予以排除。

| 元件 | 驗證版本 |
|---|---|
| macOS | 26.5.1（Apple M4 Pro） |
| Windows | 僅量測路徑，在 Codex 下 |
| Python | 本機 3.9.6 與 3.12.13；CI 涵蓋 3.10–3.13 |
| ffmpeg | 8.1 |
| yt-dlp | 2026.07.04 |

**已在真實素材上端對端驗證**：

- macOS：一集 54 分鐘的 podcast，由 Apple Podcasts 連結解析、量測、以 `speech` 處理，
  並經聽感確認——spread 10.0 → 5.8 LU。
- Windows（在 Codex 下）：一集 50 分鐘的節目完成解析、下載、快取與量測——spread 4.16 LU，
  skill 據此**正確地拒絕處理**，沒有把一支本來就穩定的錄音當成問題來修。這是「不需處理」
  這條路徑第一次跑在真實素材上，在此之前只有單元測試涵蓋。

**尚未涵蓋**：Windows 上只跑過量測路徑，實際的處理輸出還沒跑過。Linux 除了 CI 的單元測試
之外未經實測。`segmented` 只在合成的階梯素材上驗證過，還沒跑過真實的漂移錄音。假立體聲
偵測是以整檔為單位，所以本體 dual mono、片頭卻是真立體聲的節目會回報片頭那個較低的分離度
——安全但不精確。下載快取不會自動清理。

歡迎提 issue 與 PR。

## 授權

MIT。見 [LICENSE](./LICENSE)。

來源解析層（Apple Podcasts lookup、yt-dlp 下載、快取鍵）移植自
[audio-tldr-skill](https://github.com/AugustusW/audio-tldr-skill)，MIT © AugustusW。
詳見 `scripts/source.py` 檔頭。

---

> 值得聽的錄音，值得用同一個音量聽完。
