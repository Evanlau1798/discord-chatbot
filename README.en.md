# Discord Chatbot

[繁體中文](README.md)

A personal AI chatbot for Discord. It supports personas, memory, web search, URL reading, image/GIF/video understanding, YouTube transcripts, and optional local OpenAI-compatible image generation.

The main usage is simple:

- Mention the bot in a server channel
- Chat directly in DM
- Switch personas with `/persona`
- Let the bot search, read pages, inspect media, or remember long-term preferences when needed

## Features

- **Persona chat**
  - Default persona: `persona/example.json`
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

- **Web search and page reading**
  - Search always goes through SearXNG
  - User-provided URLs are read directly, not searched first
  - Normal pages use HTTP extraction first, then Patchright/Chromium fallback
  - YouTube videos use `yt-dlp` for transcripts
  - X/Twitter posts are read from public metadata when possible

- **Image, GIF, and video understanding**
  - Supports Discord image attachments, image URLs, Discord GIF picker, Tenor/Giphy URLs
  - GIFs are split into up to 60 chronological frames
  - Discord video attachments are sampled into up to 60 JPEG frames with ffmpeg/ffprobe
  - Full videos are not uploaded to GenAI

- **Image generation**
  - Supports a local OpenAI-compatible image API
  - Text replies are still sent if image generation fails
  - By default, image prompts avoid visible text unless the user explicitly asks for it

## Requirements

- Python 3.9+
- Discord Bot token
- Google GenAI API key
- SearXNG search service
- `yt-dlp` for YouTube transcripts
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

## Database And Runtime Files

These paths are ignored by git:

- `databases/`
- `AIHistory/`
- `tmp/`
- `imagine-tmp/`

You do not need to create databases manually. The program creates them when needed:

- `databases/user_memories.db`
- `databases/image_context_cache.db`
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
