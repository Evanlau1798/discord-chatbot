# Discord Chatbot

繁體中文 | [English](README.en.md)

一個給 Discord 用的個人向 AI 聊天 bot。支援人設、記憶、上網查資料、讀 URL、看圖片/GIF/影片附件，也可以串接本機 OpenAI-compatible 生圖服務。

Discord 伺服器：[加入討論](https://discord.gg/p222BzAtGj)

主要使用情境很單純：

- 在伺服器用 `@mention` 叫 bot 回覆
- 在 DM 裡直接聊天
- 用 `/persona` 切換人設
- 讓 bot 需要時自己搜尋、讀網頁、看圖片或記住長期偏好

## 功能

- **人設對話**
  - repo 預設人設 key 是 `example`
  - 可在 `.env` 設定 `DEFAULT_PERSONA_KEY` 指定實際預設使用的人設檔案
  - 可在 `persona/*.json` 新增人設
  - 可在 `persona/imagen/*.json` 補充生圖用的角色外觀描述
  - `/persona` 可切換目前使用的人設

- **長期記憶**
  - 使用 Fernet 加密後存入 SQLite
  - 記錄穩定偏好、稱呼、長期設定等資訊
  - `/memory_view` 查看記憶，`/memory_reset` 清除記憶

- **DM 與伺服器上下文**
  - DM 會保存短期聊天歷史
  - 伺服器對話以「回覆鏈」作為上下文，不讀整個頻道歷史
  - 可從快取還原過去圖片訊息的文字摘要
  - 預設最多同時處理 3 則 Discord 訊息請求，可用 `.env` 調整

- **上網搜尋與讀網頁**
  - 搜尋一律走本機 OpenSERP，聚合 Google、Bing、DuckDuckGo 與 Ecosia
  - 搜尋結果會讓 Google 來源優先，Bing 來源排在後面
  - 多個搜尋關鍵字預設最多執行 3 個，且每次搜尋間隔 1 秒
  - 使用者貼 URL 時會直接讀 URL，不先搜尋
  - 一般網頁優先用 HTTP reader，必要時用 Patchright/Chromium fallback
  - YouTube 影片會用 `yt-dlp` 擷取字幕；找 YouTube 影片連結時也會優先用 `yt-dlp` 搜尋
  - X/Twitter 貼文會嘗試讀取公開文字與圖片 metadata

- **圖片 / GIF / 影片理解**
  - 支援 Discord 圖片 attachment、圖片 URL、Discord GIF picker、Tenor/Giphy URL
  - GIF 會拆成最多 60 張時間序圖片
  - Discord 影片 attachment 會用 ffmpeg/ffprobe 抽成最多 60 張 JPEG frame
  - 不會把完整影片直接送給 GenAI

- **生圖**
  - 可串接 localhost OpenAI-compatible image API
  - 需設定 `AI_IMAGINE_ENABLED=1` 才會啟用繪圖協定
  - 生圖失敗時仍會保留文字回覆
  - 預設規則是不在圖片中加入明文文字，除非使用者明確要求

## 需求

- Python 3.9+
- Discord Bot token
- Google GenAI API key
- OpenSERP 搜尋服務（從 `reference/openserp` 固定版本建置）
- `yt-dlp`：YouTube 字幕擷取與影片搜尋
- `ffmpeg` / `ffprobe`：影片附件抽幀
- Patchright Chromium：網頁 browser fallback

安裝 Python 套件：

```bash
python -m pip install -r requirements.txt
python -m patchright install chromium
```

確認外部工具：

```bash
yt-dlp --version
ffmpeg -version
ffprobe -version
```

如果 `ffmpeg` / `ffprobe` 不在 PATH，可在 `.env` 指定路徑；支援 `~`，例如 `FFMPEG_BIN=~/.local/bin/ffmpeg`。

## 設定

專案不會提交 `.env`。請複製範例檔後填入自己的 key：

```bash
cp .env.example .env
```

至少需要設定：

```env
DISCORD_BOT_TOKEN=...
GEMINI_API_KEY=...
MEMORY_ENCRYPTION_KEY=...
```

產生 `MEMORY_ENCRYPTION_KEY`：

```bash
python - <<'PY'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
PY
```

更多可用參數請看 [.env.example](.env.example)。

Discord 訊息處理預設同時執行 3 則請求；這是 async 併發數，不是 OS thread 數：

```env
AI_CHAT_MAX_PARALLEL_REQUESTS=3
```

搜尋相關設定預設如下，可依需要調整：

```env
OPENSERP_BASE_URL=http://127.0.0.1:17000
OPENSERP_LANGUAGE=zh-TW
OPENSERP_REGION=TW
OPENSERP_TIME_RANGE=
OPENSERP_MAX_QUERIES_PER_TURN=3
OPENSERP_DESIRED_SOURCES=3
YTDLP_REQUEST_COOLDOWN_SECONDS=1
YOUTUBE_SEARCH_LIMIT=5
YOUTUBE_SEARCH_MAX_QUERIES_PER_TURN=1
YOUTUBE_SEARCH_QUERY_COOLDOWN_SECONDS=1
YTDLP_SEARCH_SLEEP_REQUESTS=1
```

OpenSERP 會平行查詢多個引擎、去重並擷取前 3–5 個來源。Bot 最多同時送出 3 個 OpenSERP 請求；Google 在服務端限制為全域每秒一次，遇到 CAPTCHA 時不重試 Google，其他引擎仍會回傳 partial success。

先確認固定版本原始碼位於 `reference/openserp`，再執行 `./start_openserp.sh`。服務只會映射到 `127.0.0.1:17000`，且預設關閉 CORS、request proxy URL 與 CAPTCHA solver。停止服務使用 `./stop_openserp.sh`。
YouTube 影片搜尋仍使用獨立的 `yt-dlp` queue，預設每輪只執行 1 個 query，並讓 request 間隔 1 秒。

若要啟用生圖，請在 `.env` 設定 `AI_IMAGINE_ENABLED=1`、`AI_IMAGINE_BASE_URL`、`AI_IMAGINE_API_KEY` 與 `AI_IMAGINE_MODEL`。關閉時 bot 不會在 prompt 中要求模型輸出 `imageGeneration`。
生圖額度限制在開源預設中關閉；若設定 `AI_IMAGINE_RATE_LIMIT_ENABLED=1`，每個帳號會在成功繪圖後開始計算 24 小時窗口，預設最多 3 次。可用 `AI_IMAGINE_DAILY_LIMIT` 與 `AI_IMAGINE_RATE_LIMIT_WHITELIST` 調整限制與白名單；`IMAGINE_QUOTA_ADMIN_USER_ID` 可指定允許重置所有繪圖額度的 Discord user id。

如果你要使用自己的私有人設，可以放在 ignored 檔名，例如 `persona/my.private.json`，再把 `.env` 的 `DEFAULT_PERSONA_KEY` 設成對應檔名去掉 `.json` 後的 key。請不要把未授權作品、台本或角色文本直接提交到公開 repo。

## 啟動

直接啟動：

```bash
python main.py
```

## Discord 指令

- `/persona`：查看與切換人設
- `/forgotjuice`：清除 DM 對話歷史
- `/chat_history`：將 DM 對話歷史私訊給使用者
- `/memory_view`：查看長期記憶
- `/memory_reset`：清除長期記憶
- `/imagine_quota`：查看剩餘繪圖額度
- `/imagine_quota_reset_all`：管理員重置所有繪圖額度

## 資料庫與 runtime 檔案

以下路徑已被 `.gitignore` 排除：

- `databases/`
- `AIHistory/`
- `tmp/`
- `imagine-tmp/`

不需要手動建立資料庫。程式會在需要時自動建立：

- `databases/user_memories.db`
- `databases/image_context_cache.db`
- `databases/imagine_rate_limits.db`
- `databases/AI_user_choice.pickle`
- `AIHistory/user_history.pickle`

所以 repo 內沒有資料庫是正常狀態。

## 開發

跑測試：

```bash
python -m unittest
```

編譯檢查：

```bash
python -m compileall main.py utils extensions
```
