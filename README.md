# Reel Render — serverless vertical explainer reels

Turns a JSON payload into a ~45–55s **1080×1920 motion-graphics reel** (AI voiceover +
burned-in captions) and renders it **on GitHub Actions** — no server to run or maintain.

- **Public repo → unlimited free Actions minutes** on standard runners. For on-request
  volume (a few to a few dozen reels a day) it's effectively free.
- The render code is plain HTML + Playwright + ffmpeg. Each reel is driven by a JSON
  **payload** (see `payload.example.json`), so every article can produce a different reel.
- Delivery stays in **n8n**: the workflow POSTs the finished MP4 back to your n8n webhook,
  which uploads it to Drive and sends it on WhatsApp — the same path your articles use.

> ⚠️ Public repo = public code. That's fine for the render code. **Never commit secrets**
> (TTS/API keys, WhatsApp token, callback URL) — put them in **Settings → Secrets and
> variables → Actions**. Article content is passed in at trigger time, never committed.
> Also: Actions is for normal project automation, not a mass render farm — keep volume
> reasonable or move heavy volume to a serverless function (AWS Lambda, etc.).

## Layout

```
render/reel.html   generic renderer — builds the animation from window.REEL (data.js)
render/build.py    payload → voiceover (edge-tts/gTTS) → scene timings → frames → mp4
render/capture.py  Playwright frame capture (deterministic seek, 30fps)
render/notify.py   POST the finished mp4 back to your n8n webhook (optional)
payload.example.json   sample reel (reproduces the prototype)
scripts/article_to_reel.prompt.md   LLM prompt: article markdown → reel payload JSON
.github/workflows/render.yml        the Actions pipeline
```

## Put it on GitHub

```bash
cd reel-render
git init && git add . && git commit -m "Reel render pipeline"
gh repo create reel-render --public --source=. --push   # or create the repo in the UI and push
```

Then in the repo: **Settings → Secrets and variables → Actions → New repository secret**
- `N8N_CALLBACK_URL` — your n8n webhook that receives the finished MP4 (optional; without
  it the reel is still produced as a downloadable workflow artifact).

## Try it immediately

- **Actions tab → Render reel → Run workflow** (leave payload blank → renders the example).
- Or just push — the first push renders `payload.example.json` and the MP4 appears as a
  workflow artifact. Download it to confirm quality end-to-end.

## Wire it to n8n (automatic, on request)

When you want a reel for an article, n8n does three things:

1. **Script it** — an LLM node turns the finished article into the reel payload JSON using
   `scripts/article_to_reel.prompt.md`.
2. **Fire the render** — an HTTP Request node calls the GitHub API:
   ```
   POST https://api.github.com/repos/<you>/reel-render/dispatches
   Headers: Authorization: Bearer <GH_TOKEN with 'repo' scope>
            Accept: application/vnd.github+json
   Body:    { "event_type": "render-reel", "client_payload": <the reel payload> }
   ```
   (`client_payload` must be the payload object itself — same shape as `payload.example.json`,
   optionally with a `meta` object carrying the WhatsApp `to` id / title you want echoed back.)
3. **Deliver** — the workflow renders (~2–4 min), then POSTs the MP4 (multipart, field
   `file`, plus any `meta` fields) to your `N8N_CALLBACK_URL`. A second n8n webhook receives
   it, uploads to Drive, and sends the WhatsApp message. Render stays off n8n; delivery stays on it.

## Voice

Defaults to **edge-tts** (free neural voices, good quality, works on GitHub runners). Falls
back to **gTTS** if edge-tts is unavailable. Change the voice per reel with the payload's
`"voice"` (e.g. `en-US-AndrewNeural`, `en-US-AriaNeural`, `en-GB-RyanNeural`). To use a
premium provider (ElevenLabs/OpenAI), add its key as a secret and extend `synth_voice()`
in `render/build.py`.

## Local test

```bash
pip install -r requirements.txt && python -m playwright install --with-deps chromium
python render/build.py --payload payload.example.json --out reel.mp4
```
