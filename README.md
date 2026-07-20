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
- 所選模型來源的 API key（localhost OpenAI-compatible 可免 key）
- OpenSERP 搜尋服務（啟動腳本使用固定 stable release image）
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
AI_CHAT_PROVIDER=gemini
GEMINI_API_KEY=...
MEMORY_ENCRYPTION_KEY=...
```

若使用 `./run_bot.sh`，`MEMORY_ENCRYPTION_KEY` 初次可保持空白；腳本會在停止或啟動任何服務前詢問是否安全產生並寫回 `.env`。非互動環境不會自動產生或等待輸入，必須預先設定。請備份並長期保留同一把 key；更換後，既有加密記憶將無法解密。

`AI_CHAT_PROVIDER` 支援 `gemini`、`nvidia` 與 `openai_compatible`。未設定時維持使用 Gemini。

NVIDIA API Catalog 預設使用 `https://integrate.api.nvidia.com/v1`，也可以把 base URL 指向自架 NIM：

```env
AI_CHAT_PROVIDER=nvidia
NVIDIA_API_KEY=...
NVIDIA_MODEL=模型 ID
```

`.env.example` 已預先配置 message strategy、thinking、輸出長度、NVCF、OpenSERP、YT-DLP、影片與 ASR 的建議值。初次設定通常只需填入 secret；生圖功能預設關閉。

NVIDIA API Catalog 沒有 Gemini `cachedContents` 的建立、查詢或刪除 API，因此本專案不會替 NVIDIA 模擬快取資源或本地回應快取。自架 NIM 可由部署端設定 `NIM_ENABLE_KV_CACHE_REUSE=1` 使用透明 prefix/KV cache；它主要改善重複長前綴的首 token 延遲，不代表 API Catalog 提供 cached-token 計費折扣。API Catalog 是 trial 服務，不可直接作為正式 production subscription；正式環境需使用合約允許的 hosted 服務或自架 NIM。相關限制請參考 [NVIDIA NIM KV cache 設定](https://docs.nvidia.com/nim/large-language-models/1.12.0/configuration.html) 與 [NVIDIA API Trial Terms](https://assets.ngc.nvidia.com/products/api-catalog/legal/NVIDIA%20API%20Trial%20Terms%20of%20Service.pdf)。

一般 OpenAI-compatible `/chat/completions` 服務使用以下設定；localhost 服務不需要驗證時可將 key 留空：

```env
AI_CHAT_PROVIDER=openai_compatible
OPENAI_COMPAT_API_KEY=
OPENAI_COMPAT_BASE_URL=http://127.0.0.1:8000/v1
OPENAI_COMPAT_MODEL=模型 ID
```

圖片會以標準 `image_url` 或 base64 data URL 傳送，影片則先抽幀並整理為 contact sheet。所選模型必須支援視覺輸入，否則服務會回報模型錯誤。模型來源重試失敗後不會自動把對話送往其他 provider。

若不使用 `run_bot.sh`，可手動產生 `MEMORY_ENCRYPTION_KEY`：

```bash
python - <<'PY'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
PY
```

完整且可直接使用的預設設定請看 [.env.example](.env.example)。

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

OpenSERP 會平行查詢多個引擎、去重並先擷取最多 5 個候選頁面，再由 Bot 依相關性、來源 profile 與網域多樣性選出 3–5 個來源。`OPENSERP_TIME_RANGE` 僅適合有發佈日期的內容，可使用 `today`、`week`、`month`、`year` 或 `YYYYMMDD..YYYYMMDD`；即時天氣等動態頁面應留空。Bot 最多同時送出 3 個 OpenSERP 請求；Google 在服務端限制為全域每秒一次，遇到 CAPTCHA 時不重試 Google，其他引擎仍會回傳 partial success。

執行 `./start_openserp.sh` 會使用官方 stable release `0.8.6`，並以 image manifest digest 鎖定內容；不會從 `main`、`latest` 或本機 `reference/openserp` 自動更新建置。服務只會映射到 `127.0.0.1:17000`，且預設關閉 CORS、request proxy URL 與 CAPTCHA solver。未來升級 stable release 時，需明確更新腳本中的 release 與 digest。停止服務使用 `./stop_openserp.sh`。
YouTube 影片搜尋仍使用獨立的 `yt-dlp` queue，預設每輪只執行 1 個 query，並讓 request 間隔 1 秒。

若要啟用生圖，請在 `.env` 設定 `AI_IMAGINE_ENABLED=1`、`AI_IMAGINE_BASE_URL`、`AI_IMAGINE_API_KEY` 與 `AI_IMAGINE_MODEL`。關閉時 bot 不會在 prompt 中要求模型輸出 `imageGeneration`。
繪圖協議只接受明確的 `create` 或 `edit` operation。模型依「輸出是否依賴來源圖片」的語意原則選擇 operation；harness 不以自然語言關鍵字猜測意圖，只驗證 `edit` 使用本輪可信候選圖片 ID，未知 ID 最多允許一次受限修正。近期圖片不會直接加入首輪多模態輸入；首輪只列出同一使用者與頻道內可回溯的訊息參考 ID，模型確實需要時才可請求一次延遲載入。直接附件、回覆鏈與明確 Discord 訊息連結仍會直接成為可信候選；歷史參考不存在或載入失敗時不會退回從零生圖。
生圖額度限制預設已配置，但在生圖關閉時不會生效；啟用後，每個帳號會在成功繪圖後開始計算 24 小時窗口，預設最多 3 次。可用 `AI_IMAGINE_DAILY_LIMIT` 與 `AI_IMAGINE_RATE_LIMIT_WHITELIST` 調整限制與白名單；`IMAGINE_QUOTA_ADMIN_USER_ID` 可指定允許重置所有繪圖額度的 Discord user id。

如果你要使用自己的私有人設，可以放在 ignored 檔名，例如 `persona/my.private.json`，再把 `.env` 的 `DEFAULT_PERSONA_KEY` 設成對應檔名去掉 `.json` 後的 key。請不要把未授權作品、台本或角色文本直接提交到公開 repo。

## 啟動

建議使用整合啟動腳本；它會檢查加密 key、重啟本專案的 OpenSERP 與 bot，並在 Discord Gateway ready 後啟動 OpenVINO ASR：

```bash
./run_bot.sh
```

若直接執行 `python main.py`，必須事先在 `.env` 設定合法的 `MEMORY_ENCRYPTION_KEY`，且不會自動管理本機 companion services。

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
