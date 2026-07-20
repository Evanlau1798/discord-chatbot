# Discord Chatbot

[繁體中文](README.md)

A personal AI chatbot for Discord. It supports personas, memory, web search, URL reading, image/GIF/video understanding, YouTube transcripts, and optional local OpenAI-compatible image generation.

Discord server: [Join the discussion](https://discord.gg/p222BzAtGj)

The main usage is simple:

- Mention the bot in a server channel
- Chat directly in DM
- Switch personas with `/persona`
- Let the bot search, read pages, inspect media, or remember long-term preferences when needed

## Features

- **Persona chat**
  - Repo default persona key: `example`
  - Set `DEFAULT_PERSONA_KEY` in `.env` to choose the actual default persona file for your deployment
  - Add personas in `persona/*.json`
  - Add image-generation visual references in `persona/imagen/*.json`
  - Switch personas with `/persona`

- **Long-term memory**
  - Stored in encrypted SQLite with Fernet
  - Intended for stable preferences, names, goals, and long-term settings
  - View with `/memory_view`, reset with `/memory_reset`

- **DM and server context**
  - DM keeps short-term chat history
  - Server context follows reply chains instead of reading the whole channel
  - Cached image summaries can be restored into future context
  - Handles up to 3 Discord message requests concurrently by default, configurable in `.env`

- **Web search and page reading**
  - Search always uses local OpenSERP across Google, Bing, DuckDuckGo, and Ecosia
  - Search results prefer Google sources first and place Bing sources after them
  - Multiple search queries execute up to 3 queries by default, with a 1-second delay between searches
  - User-provided URLs are read directly, not searched first
  - Normal pages use HTTP extraction first, then Patchright/Chromium fallback
  - YouTube videos use `yt-dlp` for transcripts; YouTube video-link searches also prefer `yt-dlp`
  - X/Twitter posts are read from public metadata when possible

- **Image, GIF, and video understanding**
  - Supports Discord image attachments, image URLs, Discord GIF picker, Tenor/Giphy URLs
  - GIFs are split into up to 60 chronological frames
  - Discord video attachments are sampled into up to 60 JPEG frames with ffmpeg/ffprobe
  - Full videos are not uploaded to GenAI

- **Image generation**
  - Supports a local OpenAI-compatible image API
  - Set `AI_IMAGINE_ENABLED=1` to enable the image-generation protocol
  - Text replies are still sent if image generation fails
  - By default, image prompts avoid visible text unless the user explicitly asks for it

## Requirements

- Python 3.9+
- Discord Bot token
- API key for the selected model provider (optional for local OpenAI-compatible services)
- OpenSERP search service using the stable release image pinned by the startup script
- `yt-dlp` for YouTube transcripts and video search
- `ffmpeg` / `ffprobe` for video attachment frame sampling
- Patchright Chromium for browser fallback

Install Python dependencies:

```bash
python -m pip install -r requirements.txt
python -m patchright install chromium
```

Check external tools:

```bash
yt-dlp --version
ffmpeg -version
ffprobe -version
```

If `ffmpeg` / `ffprobe` are not in PATH, set their paths in `.env`; `~` is supported, for example `FFMPEG_BIN=~/.local/bin/ffmpeg`.

## Configuration

The repo does not commit `.env`. Copy the example file and fill in your own values:

```bash
cp .env.example .env
```

Minimum required variables:

```env
DISCORD_BOT_TOKEN=...
AI_CHAT_PROVIDER=gemini
GEMINI_API_KEY=...
MEMORY_ENCRYPTION_KEY=...
```

`AI_CHAT_PROVIDER` accepts `gemini`, `nvidia`, or `openai_compatible`. Gemini remains the default when it is unset.

NVIDIA API Catalog defaults to `https://integrate.api.nvidia.com/v1`; the base URL may also point to a self-hosted NIM:

```env
AI_CHAT_PROVIDER=nvidia
NVIDIA_API_KEY=...
NVIDIA_MODEL=model-id
```

`.env.example` already provides practical defaults for message strategy, thinking, output length, NVCF, OpenSERP, YT-DLP, video, and ASR. Initial setup normally requires only the secrets; image generation remains disabled by default.

NVIDIA API Catalog does not expose Gemini-style `cachedContents` create, read, or delete operations, so this project does not emulate NVIDIA cache resources or cache completed responses locally. Self-hosted NIM deployments can enable transparent prefix/KV reuse with `NIM_ENABLE_KV_CACHE_REUSE=1`; this primarily improves time to first token for repeated long prefixes and does not imply an API Catalog cached-token billing discount. API Catalog is a trial service rather than a production subscription. See the [NVIDIA NIM KV cache configuration](https://docs.nvidia.com/nim/large-language-models/1.12.0/configuration.html) and [NVIDIA API Trial Terms](https://assets.ngc.nvidia.com/products/api-catalog/legal/NVIDIA%20API%20Trial%20Terms%20of%20Service.pdf).

Use these settings for a generic OpenAI-compatible `/chat/completions` service. Leave the key empty for localhost services that do not require authentication:

```env
AI_CHAT_PROVIDER=openai_compatible
OPENAI_COMPAT_API_KEY=
OPENAI_COMPAT_BASE_URL=http://127.0.0.1:8000/v1
OPENAI_COMPAT_MODEL=model-id
```

Images are sent as standard `image_url` or base64 data URLs. Videos are sampled and presented as contact sheets. The selected model must support vision input or the provider will return a model error. Exhausting retries never sends the conversation to another provider automatically.

Generate `MEMORY_ENCRYPTION_KEY`:

```bash
python - <<'PY'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
PY
```

See [.env.example](.env.example) for the complete ready-to-use defaults.

Discord message handling runs 3 requests concurrently by default. This is async concurrency, not OS thread count:

```env
AI_CHAT_MAX_PARALLEL_REQUESTS=3
```

Default search-related settings:

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

OpenSERP queries multiple engines in parallel, deduplicates results, and extracts up to 5 candidate pages before the bot selects 3–5 sources by relevance, source profile, and domain diversity. `OPENSERP_TIME_RANGE` is only for publication-dated content and accepts `today`, `week`, `month`, `year`, or `YYYYMMDD..YYYYMMDD`; leave it blank for dynamic pages such as live weather dashboards. The bot permits at most 3 concurrent OpenSERP requests. Google is globally limited to one request per second by the service; a CAPTCHA ends that Google attempt without blocking partial results from other engines.

Run `./start_openserp.sh` to use the official stable release `0.8.6`, pinned by its image manifest digest. The script does not build from `main`, `latest`, or the local `reference/openserp` checkout. The service maps only to `127.0.0.1:17000` and disables CORS, request-supplied proxy URLs, and CAPTCHA solving by default. Future stable upgrades require an explicit release and digest update in the script. Stop it with `./stop_openserp.sh`.
YouTube search continues to use its separate `yt-dlp` queue, with one query per turn and a one-second request interval by default.

To enable image generation, set `AI_IMAGINE_ENABLED=1`, `AI_IMAGINE_BASE_URL`, `AI_IMAGINE_API_KEY`, and `AI_IMAGINE_MODEL` in `.env`. When disabled, the bot does not ask the model to output `imageGeneration`.
The drawing protocol accepts only explicit `create` or `edit` operations. Obvious edit requests are bound to trusted candidate image IDs. If the model incorrectly returns `create`, the bot permits one constrained repair attempt; a second violation cancels the image action instead of falling back to generation from scratch.
Image quota protection is configured by default but remains inactive while image generation is disabled. Once enabled, each account starts its own 24-hour window after a successful image generation, with 3 uses by default. Configure the limit and whitelist with `AI_IMAGINE_DAILY_LIMIT` and `AI_IMAGINE_RATE_LIMIT_WHITELIST`; set `IMAGINE_QUOTA_ADMIN_USER_ID` to the Discord user id allowed to reset all image quotas.

For private personas, use an ignored filename such as `persona/my.private.json`, then set `DEFAULT_PERSONA_KEY` in `.env` to the file key without `.json`. Do not commit unlicensed scripts, transcripts, or character text to a public repo.

## Run

Start directly:

```bash
python main.py
```

Or use the tmux startup script:

```bash
./start_bot_tmux.sh
```

## Discord Commands

- `/persona`: view and switch persona
- `/forgotjuice`: clear DM chat history
- `/chat_history`: send DM chat history to the user
- `/memory_view`: view long-term memory
- `/memory_reset`: clear long-term memory
- `/imagine_quota`: view remaining image-generation quota
- `/imagine_quota_reset_all`: admin reset for all image-generation quotas

## Database And Runtime Files

These paths are ignored by git:

- `databases/`
- `AIHistory/`
- `tmp/`
- `imagine-tmp/`

You do not need to create databases manually. The program creates them when needed:

- `databases/user_memories.db`
- `databases/image_context_cache.db`
- `databases/imagine_rate_limits.db`
- `databases/AI_user_choice.pickle`
- `AIHistory/user_history.pickle`

It is normal for an open-source checkout to contain no database files.

## Development

Run tests:

```bash
python -m unittest
```

Compile check:

```bash
python -m compileall main.py utils extensions
```
