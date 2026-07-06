# Sermon Explainer

Turns a sermon (YouTube link or uploaded audio/video) into a short,
illustrated, narrated explainer video:
- Longer than 1 hour of source → ~10 minute explainer
- 1 hour or less → ~6 minute explainer

## What you need before starting
- **VS Code** (you already have this)
- **Python 3.10+** installed on your computer
- **ffmpeg** installed on your computer as a system program (not just the
  Python package) - this does the actual video editing.
  - Mac: `brew install ffmpeg`
  - Windows: download from ffmpeg.org and add it to your PATH, or `winget install ffmpeg`
  - Linux: `sudo apt install ffmpeg`
  - Check it worked by running `ffmpeg -version` in a terminal.
- Your 4 API keys ready (OpenAI, Anthropic, ElevenLabs, Recraft)

## Setup steps (do these once)

1. **Open the folder in VS Code**
   `File -> Open Folder...` and select this `sermon-explainer` folder.

2. **Open a terminal inside VS Code**
   `Terminal -> New Terminal` (or `` Ctrl+` ``).

3. **Create a virtual environment** (keeps this project's packages separate
   from everything else on your computer):
   ```
   python -m venv venv
   ```
   Then activate it:
   - Mac/Linux: `source venv/bin/activate`
   - Windows: `venv\Scripts\activate`

   You'll know it worked because your terminal line will start with `(venv)`.
   VS Code may also pop up a notification asking if you want to use this
   environment as your Python interpreter - click **Yes**.

4. **Install the required packages:**
   ```
   pip install -r requirements.txt
   ```

5. **Set up your keys:**
   - Make a copy of `.env.example` and rename the copy to exactly `.env`
   - Open `.env` and paste your 4 real keys in (replacing the placeholder text)
   - Save the file. Never share this file or paste its contents anywhere.

## Running it

With your virtual environment still active, from the terminal:

```
python main.py "https://www.youtube.com/watch?v=SOME_SERMON_LINK"
```

or, for a file you've already got locally:

```
python main.py "input/my_sermon.mp3"

# Optional: customize intro/outro cards (each capped at 10 seconds)
python main.py "input/my_sermon.mp3" \
  --title "The Power of Faith" \
  --outro-text "Thanks for watching - follow us for more" \
  --intro-seconds 8 \
  --outro-seconds 8 \
  --facebook "facebook.com/yourpage" \
  --youtube "youtube.com/@yourchannel" \
  --x "x.com/yourhandle" \
  --instagram "instagram.com/yourhandle" \
  --tiktok "tiktok.com/@yourhandle"
```

You'll see progress printed for each station as it runs. When it finishes,
your video and subtitle file will be in the `output/` folder:
- `output/explainer.mp4`
- `output/explainer.srt`
- `output/explainer_final.mp4` (intro + body + outro)

## API mode (for frontend integration)

Run an HTTP API server instead of CLI mode:

```
uvicorn api:app --host 0.0.0.0 --port 8000
```

Core endpoints:

- `GET /health`
- `POST /uploads` (multipart file upload; returns a server path to use as `source`)
- `POST /jobs`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/result`
- `GET /jobs/{job_id}/artifacts/final_video`
- `GET /jobs/{job_id}/artifacts/base_video`
- `GET /jobs/{job_id}/artifacts/subtitles`

`POST /jobs` request body example:

```json
{
  "source": "input/my_sermon.mp3",
  "title": "The Power of Faith",
  "outro_text": "Thanks for watching - follow us for more",
  "intro_seconds": 8,
  "outro_seconds": 8,
  "confirm_rights": false,
  "socials": {
    "facebook": "facebook.com/yourpage",
    "youtube": "youtube.com/@yourchannel",
    "x": "x.com/yourhandle",
    "instagram": "instagram.com/yourhandle",
    "tiktok": "tiktok.com/@yourhandle"
  }
}
```

`POST /uploads` example (from browser/frontend):

1. Send a multipart form with field name `file` (for example, an `.mp3`).
2. API responds with:

```json
{
  "source": "input/uploads/abc123_my_sermon.mp3",
  "filename": "my_sermon.mp3",
  "size_bytes": 12345678
}
```

3. Use the returned `source` value as `POST /jobs` -> `source`.

Notes for frontend:

- Jobs are asynchronous; poll `GET /jobs/{job_id}` for status updates.
- Browser uploads are limited to supported audio/video extensions and max 500 MB.
- Download artifacts from the artifact routes after status is `completed`.
- For YouTube sources, set `confirm_rights: true` or the API returns `403`.

## What happens at each step (in plain terms)

| Step | What it does | Needs internet/API? |
|---|---|---|
| 1. Fetch source | Downloads the YouTube video or reads your uploaded file, measures its length | Only for YouTube links |
| 2. Transcribe | Writes down everything said, with timestamps | Yes - OpenAI |
| 3. Summarize | Condenses it into a short scene-by-scene script | Yes - Anthropic (Claude) |
| 4. Narrate | Turns each scene's script into spoken audio | Yes - ElevenLabs |
| 5. Illustrate | Generates one consistent-style image per scene | Yes - Recraft |
| 6. Assemble | Combines everything into one video + subtitles | No - runs locally via ffmpeg |

## Things worth knowing (edge cases)

- **YouTube permission check:** if you give a YouTube link, the program will
  pause and ask you to confirm (type "yes") that you have the right to use
  that recording. This is a manual honesty check - the software can't verify
  legal permission on its own.
- **Long sermons:** audio is automatically split into 10-minute chunks before
  transcription, since OpenAI's API has a file-size limit. This is automatic -
  you don't need to do anything.
- **Low-confidence transcript segments:** if a stretch of audio was unclear
  (background noise, mumbling, silence), the terminal will tell you how many
  segments were flagged - worth a quick look at those moments in the final
  video before publishing.
- **Recraft credits are prepaid and non-refundable.** If a run fails partway
  through image generation, check your Recraft account balance before
  re-running, since retries consume more credits.
- **Costs add up per video**, mainly from transcription minutes, narration
  characters, and number of illustrated scenes. Longer sermons = more scenes
  = more cost. Test with a short recording first.

## If something goes wrong

The program is designed to stop with a clear message naming which station
failed and why, instead of crashing silently. Common fixes:
- "`OPENAI_API_KEY is missing`" (or similar) → check your `.env` file exists
  and is filled in correctly (not still named `.env.example`).
- YouTube download fails → try updating yt-dlp: `pip install -U yt-dlp`
- ffmpeg-related errors → confirm `ffmpeg -version` works in your terminal
  outside of VS Code too - if not, ffmpeg isn't installed correctly as a
  system program.
