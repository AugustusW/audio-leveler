# audio-leveler

修正「聲音忽大忽小」的語音類音訊——podcast、演講、訪談、會議錄音。

這個工具的價值不在於會下 ffmpeg 指令，而在於**先量測再決定**，因為選錯濾鏡等於
完全沒效果。

開發時用的那一集，整體響度量出來是 `-16.8 LUFS`——本來就正好是 podcast 慣例值。
網路上流通的 `loudnorm` 一條龍調的就是這個數字，所以每一種做法套上去，那集聽起來
都跟原本一樣難聽。真正的問題在下一層：響度在**每個段落內部**移動的幅度。

## 安裝

需要 [ffmpeg](https://ffmpeg.org/)（附帶 `ffprobe`），只有在傳 URL 時才需要
[yt-dlp](https://github.com/yt-dlp/yt-dlp)。

```bash
brew install ffmpeg          # macOS；Debian/Ubuntu 用 apt install ffmpeg
pip install yt-dlp           # 選用，處理 URL 來源時才需要
```

工具不會自動幫忙安裝。缺哪一個會明確講出來，並附上安裝方式。

## 使用

```bash
python3 skills/audio-leveler/scripts/cli.py measure <來源>
python3 skills/audio-leveler/scripts/cli.py apply <來源> --filter speech
```

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

| 濾鏡 | 適用 | 做了什麼 |
|---|---|---|
| `speech` | `intra` > `drift`：段落內起伏 | `speechnorm`，再兩段式 linear `loudnorm` |
| `segmented` | `drift` > `intra`：段落間有落差 | `dynaudnorm`（約 2.5 分鐘窗），再兩段式 linear `loudnorm` |
| `loudness` | 兩者都小，只是整體音量不對 | 只做兩段式 linear `loudnorm` |

每次產出結束後都會重新量測輸出檔，並回報三種結局之一：收斂 N%、幾乎沒有變化、
變得更差。沒有改善就會如實這樣講——工具不會在沒有證據的情況下，把自己的產出說成
成功。

其他旗標：`--out PATH`、`--target-lufs`（預設 −16）、`--mono auto|force|never`、
`--force` 覆寫既有檔案。

## 當作 skill 使用

判讀數字的指引寫在 `skills/audio-leveler/SKILL.md`。程式裡完全不含判斷：
`measure.py` 只產生數字，`apply.py` 只執行明確給定的參數，決定數字代表什麼發生在
兩者之間。會這樣切，是因為那些門檻來自很少量的錄音——寫死在程式裡等於把那幾支
錄音的特性一起凍進去。

## 已知限制

- **只處理語音。** 音樂的動態範圍是創作意圖，不是缺陷。
- 這個版本**不做降噪**。
- `--filter` 沒有自動模式，理由見上。
- **`segmented` 目前效果不好。** 用前後兩半差 18 LU 的合成素材實測，ffmpeg 內建的
  動態處理濾鏡都幫不上什麼忙：dynaudnorm 最好到 16.4 LU、compand 16.9、
  speechnorm 17.5，原始為 18.5。要修正這麼大的段落落差，需要真正的分段處理
  （偵測邊界、各自正規化、再接起來），這個版本沒有做。這條分支保留是因為 `apply`
  一定會重新量測並如實回報沒有改善，但別期待它現在能修好漂移素材。
- `speech` 分支在一支真實錄音上驗證過（spread 10.0 → 5.8 LU，聽感確認）。
- `drift` 要有一個以上的窗才有意義。窗長會隨素材長度縮放，但短於約兩分鐘的素材
  仍然只切得出少數幾個窗。
- 假立體聲偵測是以整檔為單位。若一支節目本體是 dual mono、片頭卻是真立體聲，回報
  的會是片頭那個較低的分離度——這個答案是安全的，但不精確。
- 超過兩聲道時跳過假立體聲判斷；`--mono force` 仍會依該 layout 的正確係數降混。
- `~/.cache/audio-leveler` 的下載快取不會自動清理。

## 授權

MIT，見 [LICENSE](LICENSE)。

來源解析層（Apple Podcasts lookup、yt-dlp 下載、快取鍵）移植自
[audio-tldr-skill](https://github.com/AugustusW/audio-tldr-skill)，MIT © AugustusW。
詳見 `scripts/source.py` 檔頭。
