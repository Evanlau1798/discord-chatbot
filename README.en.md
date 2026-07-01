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
  - Search always goes through SearXNG, with Google and Bing enabled by default
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
- Google GenAI API key
- SearXNG search service
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
GEMINI_API_KEY=...
MEMORY_ENCRYPTION_KEY=...
```

Generate `MEMORY_ENCRYPTION_KEY`:

```bash
python - <<'PY'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
PY
```

See [.env.example](.env.example) for all available variables.

Discord message handling runs 3 requests concurrently by default. This is async concurrency, not OS thread count:

```env
AI_CHAT_MAX_PARALLEL_REQUESTS=3
```

Default search-related settings:

```env
SEARXNG_ENGINES=google,bing
SEARXNG_MERGE_QUERIES=0
SEARXNG_MAX_QUERIES_PER_TURN=3
SEARXNG_QUERY_COOLDOWN_SECONDS=1
SEARXNG_OUTGOING_PROXIES=
SEARXNG_EXTRA_PROXY_TIMEOUT=10
YTDLP_REQUEST_COOLDOWN_SECONDS=1
YOUTUBE_SEARCH_LIMIT=5
YOUTUBE_SEARCH_MAX_QUERIES_PER_TURN=1
YOUTUBE_SEARCH_QUERY_COOLDOWN_SECONDS=1
YTDLP_SEARCH_SLEEP_REQUESTS=1
```

By default, queries are not merged. The bot executes up to the first 3 model-provided search queries with a 1-second delay between each search. Set `SEARXNG_MERGE_QUERIES=1` only when you explicitly want multiple queries merged.
SearXNG and `yt-dlp` each use a single-worker queue. YouTube video search runs only 1 query per turn by default, with a 1-second delay between `yt-dlp` requests. In practice, one precise query with the top 5 YouTube results is usually enough.

If Google still hits CAPTCHA frequently, set `SEARXNG_OUTGOING_PROXIES` in `.env`. Separate multiple proxies with commas or newlines. `./start_searxng.sh` generates SearXNG `settings.yml` with `outgoing.proxies`. SearXNG supports multiple proxies for the same protocol and distributes requests round-robin; when proxies are used, search engines see the proxy-side outbound IP.

```env
SEARXNG_OUTGOING_PROXIES=http://proxy1:8080,socks5h://proxy2:1080
```

To enable image generation, set `AI_IMAGINE_ENABLED=1`, `AI_IMAGINE_BASE_URL`, `AI_IMAGINE_API_KEY`, and `AI_IMAGINE_MODEL` in `.env`. When disabled, the bot does not ask the model to output `imageGeneration`.
Image quota is disabled by default in the open-source repo. Set `AI_IMAGINE_RATE_LIMIT_ENABLED=1` to enable it. Each account then starts its own 24-hour window after a successful image generation, with 3 uses by default. Configure the limit and whitelist with `AI_IMAGINE_DAILY_LIMIT` and `AI_IMAGINE_RATE_LIMIT_WHITELIST`; set `IMAGINE_QUOTA_ADMIN_USER_ID` to the Discord user id allowed to reset all image quotas.

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
