# Discord Chatbot

繁體中文 | [English](README.en.md)

一個給 Discord 用的個人向 AI 聊天 bot。支援人設、記憶、上網查資料、讀 URL、看圖片/GIF/影片附件，也可以串接本機 OpenAI-compatible 生圖服務。

主要使用情境很單純：

- 在伺服器用 `@mention` 叫 bot 回覆
- 在 DM 裡直接聊天
- 用 `/persona` 切換人設
- 讓 bot 需要時自己搜尋、讀網頁、看圖片或記住長期偏好

## 功能

- **人設對話**
  - 預設人設 key 是 `akira`，請建立 `persona/akira.json` 或設定 `DEFAULT_PERSONA_KEY`
  - `persona/example.json` 只是開源範例；只有 example 時 bot 會提醒尚未設定人設資訊
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

- **上網搜尋與讀網頁**
  - 搜尋一律走 SearXNG，預設同時使用 Google 與 Bing
  - 搜尋結果會讓 Google 來源優先，Bing 來源排在後面
  - 多個搜尋關鍵字預設會合併成單次查詢，降低短時間觸發 CAPTCHA 的機率
  - 使用者貼 URL 時會直接讀 URL，不先搜尋
  - 一般網頁優先用 HTTP reader，必要時用 Patchright/Chromium fallback
  - YouTube 影片會用 `yt-dlp` 擷取字幕
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
- SearXNG 搜尋服務
- `yt-dlp`：YouTube 字幕擷取
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

搜尋相關設定預設如下，可依需要調整：

```env
SEARXNG_ENGINES=google,bing
SEARXNG_MERGE_QUERIES=1
SEARXNG_MAX_QUERIES_PER_TURN=1
SEARXNG_QUERY_COOLDOWN_SECONDS=1
```

如果 Google 仍頻繁出現 CAPTCHA，應優先在 SearXNG 的 `settings.yml` 設定 `outgoing.proxies`。SearXNG 支援同協定多個 proxy 並以 round-robin 分配請求；使用 proxy 時，真正對搜尋引擎顯示的出口 IP 會由 proxy 端決定。

```yaml
outgoing:
  proxies:
    all://:
      - http://proxy1:8080
      - http://proxy2:8080
```

若要啟用生圖，請在 `.env` 設定 `AI_IMAGINE_ENABLED=1`、`AI_IMAGINE_BASE_URL`、`AI_IMAGINE_API_KEY` 與 `AI_IMAGINE_MODEL`。關閉時 bot 不會在 prompt 中要求模型輸出 `imageGeneration`。

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

## 資料庫與 runtime 檔案

以下路徑已被 `.gitignore` 排除：

- `databases/`
- `AIHistory/`
- `tmp/`
- `imagine-tmp/`

不需要手動建立資料庫。程式會在需要時自動建立：

- `databases/user_memories.db`
- `databases/image_context_cache.db`
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
