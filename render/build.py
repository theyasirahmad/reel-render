#!/usr/bin/env python3
"""Build a reel MP4 from a JSON payload.

Pipeline: payload -> voiceover (TTS) -> per-scene timings -> data.js -> frames -> mp4.
Payload is read from $REEL_PAYLOAD (JSON string) or --payload <file>.
Output: out.mp4 in the working dir (override with --out).
"""
import os, sys, json, subprocess, argparse, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
FPS = 30


def log(*a):
    print(*a, flush=True)


def load_payload(args):
    if args.payload:
        with open(args.payload) as f:
            return json.load(f)
    env = os.environ.get("REEL_PAYLOAD")
    if env:
        return json.loads(env)
    raise SystemExit("No payload: set $REEL_PAYLOAD or pass --payload file.json")


def synth_voice(text, out_mp3, voice):
    """Try edge-tts (neural, free) first, then gTTS. Returns True on success."""
    # 1) edge-tts — high quality neural, works on clean networks (e.g. GitHub runners)
    try:
        import asyncio, edge_tts
        async def _run():
            c = edge_tts.Communicate(text, voice or "en-US-AndrewNeural", rate="+6%")
            await c.save(out_mp3)
        asyncio.get_event_loop().run_until_complete(_run())
        if os.path.getsize(out_mp3) > 2000:
            log("voice: edge-tts", voice or "en-US-AndrewNeural")
            return True
    except Exception as e:
        log("edge-tts failed:", repr(e)[:160])
    # 2) gTTS fallback — no key, decent
    try:
        from gtts import gTTS
        gTTS(text=text, lang="en", tld="com").save(out_mp3)
        log("voice: gTTS fallback")
        return True
    except Exception as e:
        log("gTTS failed:", repr(e)[:160])
    return False


def duration(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nk=1:nw=1", path]).decode().strip()
    return float(out)


def scene_vo_len(sc):
    return max(len((sc.get("vo") or "").strip()), 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload")
    ap.add_argument("--out", default="out.mp4")
    ap.add_argument("--workdir", default=None)
    args = ap.parse_args()

    payload = load_payload(args)
    scenes = payload["scenes"]
    work = args.workdir or tempfile.mkdtemp(prefix="reel_")
    os.makedirs(work, exist_ok=True)
    log("workdir:", work)

    # 1) Voiceover from the concatenated scene VO lines
    narration = " ".join((sc.get("vo") or "").strip() for sc in scenes if sc.get("vo")).strip()
    vo_mp3 = os.path.join(work, "vo.mp3")
    if not synth_voice(narration, vo_mp3, payload.get("voice")):
        raise SystemExit("Voiceover synthesis failed")
    dur = duration(vo_mp3)
    tail = float(payload.get("tail", 0.4))  # small hold after last word
    dur += tail
    log("voice duration:", round(dur, 2), "s")

    # 2) Per-scene timings, proportional to each scene's VO length
    total = sum(scene_vo_len(sc) for sc in scenes)
    t = 0.0
    for sc in scenes:
        span = (scene_vo_len(sc) / total) * dur
        sc["start"] = round(t, 3)
        sc["end"] = round(t + span, 3)
        t += span
    scenes[-1]["end"] = round(dur, 3)

    data = {
        "dur": round(dur, 3),
        "accent": payload.get("accent", "#34E3C8"),
        "warn": payload.get("warn", "#F4746B"),
        "brand": payload.get("brand", "YASIR"),
        "scenes": scenes,
    }
    with open(os.path.join(HERE, "data.js"), "w") as f:
        f.write("window.REEL=" + json.dumps(data) + ";")
    log("scenes:", len(scenes), "timed")

    # 3) Render frames
    frames = os.path.join(work, "frames")
    subprocess.check_call([sys.executable, os.path.join(HERE, "capture.py"),
                           "--html", os.path.join(HERE, "reel.html"),
                           "--out", frames, "--dur", str(dur), "--fps", str(FPS)])

    # 4) Encode frames + voice -> mp4
    out = os.path.abspath(args.out)
    subprocess.check_call([
        "ffmpeg", "-y", "-framerate", str(FPS), "-i", os.path.join(frames, "f_%05d.jpg"),
        "-i", vo_mp3, "-c:v", "libx264", "-crf", "19", "-preset", "medium",
        "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "160k",
        "-shortest", "-movflags", "+faststart", out])
    log("OK ->", out)
    if not args.workdir:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
