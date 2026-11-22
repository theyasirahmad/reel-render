# reel-render

Renders premium **editorial** vertical reels (1080×1920) for **AI Newsletter** on GitHub Actions —
free, no server. An article becomes a HyperFrames composition, rendered to MP4 with an ElevenLabs
voiceover, and delivered back to n8n (which saves it to Google Drive).

## How it runs

1. **n8n** turns an article into a reel **payload** (scene JSON) and fires a GitHub
   `repository_dispatch` (event type `render-reel`).
2. **GitHub Actions** (`.github/workflows/render.yml`):
   - installs HyperFrames + Chrome + ffmpeg + Python voice deps,
   - `render/build.py` synthesizes the voice (ElevenLabs → fallback), times the scenes to it,
     generates the HyperFrames composition in `project/index.html`, and renders `reel.mp4`,
   - `render/notify.py` POSTs the finished MP4 to the n8n callback.
3. **n8n** saves it to the Reels folder in Google Drive.

## Design system

Warm ivory paper, charcoal type. **Playfair Display** (editorial lines), **Inter** (numbers/body),
**IBM Plex Mono** (labels). One tasteful accent per reel, chosen by mood (dusty blue, sage, coral,
amber, lavender — never AI purple-blue). Branded with a quiet **AI Newsletter** tag. Scene types:
`stat`, `statement`, `bars`, `list`, `outro`. See `scripts/article_to_reel.prompt.md` for the schema
and `payload.example.json` for a working example.

## Secrets (repo → Settings → Secrets and variables → Actions)

| Secret | Purpose |
| --- | --- |
| `ELEVENLABS_API_KEY` | ElevenLabs voice (primary). If unset, falls back to a free neural voice. |
| `ELEVENLABS_VOICE_ID` | The ElevenLabs voice id. |
| `ELEVENLABS_MODEL` | Optional; defaults to `eleven_multilingual_v2`. |
| `N8N_CALLBACK_URL` | Where to POST the finished MP4, e.g. `https://n8n.decotechs.xyz/webhook/reel-done`. |

## Run it manually

- **Actions tab → Render reel → Run workflow** (uses `payload.example.json` if no payload given), or
- locally: `pip install -r requirements.txt && python render/build.py --payload payload.example.json --out out.mp4`

## Layout

```
project/        HyperFrames project (index.html is generated at render time)
render/build.py composition generator + voice + timing + render
render/notify.py posts the finished MP4 to the n8n callback
scripts/article_to_reel.prompt.md  the scripting prompt (mirrored in n8n)
.github/workflows/render.yml       CI render + deliver
payload.example.json               example reel payload
```
