#!/usr/bin/env python3
"""Build an AI-art-directed vertical reel (HyperFrames) from a JSON payload.

The AI (in n8n) is the art director: it chooses how many scenes, a LAYOUT and
MOTION for each, a THEME (light/dark), the accent, and the narration — so no two
reels share the same structure. This renderer is a generic, overflow-safe
interpreter: it renders whatever layout the AI picked, auto-fits text, keeps the
hook and the TikTok-style captions in separate zones, and syncs captions to the
ElevenLabs voice.

Payload from $REEL_PAYLOAD (JSON) or --payload file.
"""
import os, sys, json, base64, subprocess, argparse, tempfile, shutil, hashlib, html as _html

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PROJECT = os.path.join(REPO, "project")
HF = ["npx", "--yes", "hyperframes@0.9.81"]
FPS = 30


def log(*a): print(*a, flush=True)


def load_payload(args):
    if args.payload:
        with open(args.payload) as f:
            return json.load(f)
    env = os.environ.get("REEL_PAYLOAD")
    if env:
        return json.loads(env)
    raise SystemExit("No payload: set $REEL_PAYLOAD or pass --payload file.json")


# ---------------------------------------------------------------- voice + timings
def _words_from_alignment(al):
    chars = al.get("characters", [])
    st = al.get("character_start_times_seconds", [])
    en = al.get("character_end_times_seconds", [])
    words, cur, cs, ce = [], "", None, None
    for i, c in enumerate(chars):
        c = c or ""
        if c.strip() == "":
            if cur:
                words.append({"w": cur, "s": cs, "e": ce}); cur = ""
            continue
        if cur == "":
            cs = st[i] if i < len(st) else (ce or 0)
        cur += c
        ce = en[i] if i < len(en) else cs
    if cur:
        words.append({"w": cur, "s": cs, "e": ce})
    return words


def _clean_voice_id(v):
    v = (v or "").strip().strip('"').strip("'").strip()
    if v.lower().startswith("http"):
        v = v.rstrip("/").split("/")[-1]
    if "=" in v:
        v = v.split("=")[-1]
    return v.strip()


def synth_elevenlabs(text, out_mp3):
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        log("ElevenLabs: no ELEVENLABS_API_KEY — fallback voice")
        return None
    import requests
    voice = _clean_voice_id(os.environ.get("ELEVENLABS_VOICE_ID", ""))
    model = (os.environ.get("ELEVENLABS_MODEL") or "eleven_multilingual_v2").strip() or "eleven_multilingual_v2"
    DEFAULT = "pNInz6obpgDQGcFmaJgB"
    cands = ([(voice, "your voice")] if voice else []) + ([(DEFAULT, "default — fix ELEVENLABS_VOICE_ID")] if voice != DEFAULT else [])
    for vid, tag in cands:
        try:
            r = requests.post(
                "https://api.elevenlabs.io/v1/text-to-speech/" + vid + "/with-timestamps",
                headers={"xi-api-key": key, "accept": "application/json", "content-type": "application/json"},
                json={"text": text, "model_id": model,
                      "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.0, "use_speaker_boost": True}},
                timeout=120)
            if r.status_code == 200:
                data = r.json()
                audio = base64.b64decode(data["audio_base64"])
                if len(audio) > 2000:
                    with open(out_mp3, "wb") as f:
                        f.write(audio)
                    words = _words_from_alignment(data.get("alignment") or data.get("normalized_alignment") or {})
                    log("voice: ElevenLabs", vid, model, "(" + tag + ") | words", len(words))
                    return words
            prev = (vid[:4] + ".." + vid[-3:]) if len(vid) > 9 else vid
            log("ElevenLabs rejected (%s): HTTP %s %s [voice_id len=%d %s]" % (tag, r.status_code, r.text[:140], len(vid), prev))
        except Exception as e:
            log("ElevenLabs error:", repr(e)[:160])
    return None


def ffprobe_dur(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "default=nk=1:nw=1", path]).decode().strip()
    return float(out)


def synth_fallback(text, out_mp3, voice):
    ok = False
    try:
        import asyncio, edge_tts
        async def _run():
            c = edge_tts.Communicate(text, voice or "en-US-AndrewNeural", rate="+8%")
            await c.save(out_mp3)
        asyncio.get_event_loop().run_until_complete(_run())
        ok = os.path.getsize(out_mp3) > 2000
        if ok:
            log("voice: edge-tts (fallback)")
    except Exception as e:
        log("edge-tts failed:", repr(e)[:150])
    if not ok:
        try:
            from gtts import gTTS
            gTTS(text=text, lang="en", tld="com").save(out_mp3)
            ok = True
            log("voice: gTTS (fallback)")
        except Exception as e:
            log("gTTS failed:", repr(e)[:150])
    if not ok:
        return None
    dur = ffprobe_dur(out_mp3)
    toks = text.split()
    wl = [len(t) + 1 for t in toks]
    tot = sum(wl) or 1
    t, words = 0.0, []
    for tok, w in zip(toks, wl):
        span = (w / tot) * dur
        words.append({"w": tok, "s": round(t, 3), "e": round(t + span, 3)})
        t += span
    return words


# ---------------------------------------------------------------- composition
def esc(s):
    return _html.escape(str(s if s is not None else ""), quote=False)


def emph(s):
    s = esc(s); out, i = [], 0
    while i < len(s):
        if s[i] == "*":
            j = s.find("*", i + 1)
            if j > i:
                out.append('<span class="em">' + s[i+1:j] + "</span>"); i = j + 1; continue
        out.append(s[i]); i += 1
    return "".join(out)


def _brand_html(brand):
    brand = brand or "AI Newsletter"
    p = brand.split(" ", 1)
    return (esc(p[0]) + "&nbsp;<b>" + esc(p[1]) + "</b>") if len(p) == 2 else "<b>" + esc(brand) + "</b>"


LIGHT = {"paper": "#F3EFE6", "ink": "#20222A", "soft": "#4B4D55", "grey": "#8C887E", "line": "#DBD4C6", "vig": "rgba(80,70,50,.12)", "gblend": "multiply"}
DARK = {"paper": "#15171D", "ink": "#F2EEE4", "soft": "#B8B4AA", "grey": "#827E74", "line": "#2B2E36", "vig": "rgba(0,0,0,.45)", "gblend": "screen"}

MOTIONS = ["maskup", "pop", "slide", "blur", "rise"]


def _motion(sel, style, at, dur=0.7):
    if style == "pop":
        return "tl.fromTo('%s',{scale:0.72,opacity:0},{scale:1,opacity:1,duration:%.2f,ease:'back.out(1.5)'},%.3f);" % (sel, dur * .95, at)
    if style == "slide":
        return "tl.fromTo('%s',{xPercent:-22,opacity:0},{xPercent:0,opacity:1,duration:%.2f,ease:'power4.out'},%.3f);" % (sel, dur * .9, at)
    if style == "blur":
        return "tl.fromTo('%s',{opacity:0,filter:'blur(18px)'},{opacity:1,filter:'blur(0px)',duration:%.2f,ease:'power3.out'},%.3f);" % (sel, dur, at)
    if style == "rise":
        return "tl.fromTo('%s',{y:60,opacity:0},{y:0,opacity:1,duration:%.2f,ease:'power3.out'},%.3f);" % (sel, dur, at)
    return "tl.fromTo('%s',{yPercent:120},{yPercent:0,duration:%.2f,ease:'power4.out'},%.3f);" % (sel, dur, at)  # maskup


def _lines_html(sid, cls, lines, maskable=True):
    out, ids = [], []
    for k, L in enumerate(lines):
        wid = "%sL%d" % (sid, k)
        if maskable:
            out.append('<div class="mask"><span class="%s ink fit" id="%s">%s</span></div>' % (cls, wid, emph(L)))
        else:
            out.append('<div class="%s ink fit" id="%s">%s</div>' % (cls, wid, emph(L)))
        ids.append(wid)
    return "".join(out), ids


def render_scene(i, sc, motion, align):
    """Generic layout interpreter. sc has start/end baked in and a 'layout'."""
    sid = "s%d" % i
    t0 = sc["start"]
    layout = (sc.get("layout") or sc.get("type") or "hook").lower()
    body, js, fits = [], [], []

    def anim(sel, style=None, at=None, dur=0.7):
        js.append(_motion(sel, style or motion, t0 + (0.22 if at is None else at), dur))

    if sc.get("kicker"):
        body.append('<div class="kick ink" id="%sk">%s</div>' % (sid, emph(sc["kicker"])))
        anim("#%sk" % sid, "slide" if motion != "slide" else "rise", 0.12)

    if layout in ("number", "bignum"):
        n = sc.get("number", {})
        pre, suf, dec = n.get("prefix", ""), n.get("suffix", ""), int(n.get("decimals", 0))
        frm, to = float(n.get("from", 0)), float(n.get("to", 0))
        big = "numXL" if layout == "bignum" else "num"
        body.append('<div class="mask"><span class="%s ink fit" id="%snum">%s</span></div>' % (big, sid, esc(pre + ("%.*f" % (dec, frm)) + suf)))
        fits.append("#%snum" % sid)
        anim("#%snum" % sid, motion, 0.22)
        cdur = min(1.7, max(0.9, (sc["end"] - t0) * 0.5))
        js.append("cnt('#%snum',%g,%g,%d,%s,%s,%.3f,%.3f);" % (sid, frm, to, dec, json.dumps(pre), json.dumps(suf), t0 + 0.3, cdur))
        if sc.get("label"):
            body.append('<div class="label ink" id="%slb">%s</div>' % (sid, emph(sc["label"])))
            anim("#%slb" % sid, "maskup", 0.5)

    elif layout in ("statement", "quote", "phrase"):
        lines = sc.get("lines") or ([sc["text"]] if sc.get("text") else ["..."])
        if layout == "quote":
            body.append('<div class="qmark ink" id="%sq">&ldquo;</div>' % sid)
            anim("#%sq" % sid, "pop", 0.1)
        h, ids = _lines_html(sid, "serif", lines[:3])
        body.append(h)
        for k, wid in enumerate(ids):
            fits.append("#" + wid); anim("#" + wid, motion, 0.2 + k * 0.13)
        if sc.get("source"):
            body.append('<div class="src ink" id="%ss">%s</div>' % (sid, emph(sc["source"])))
            anim("#%ss" % sid, "maskup", 0.5)
        if sc.get("rule"):
            body.append('<div class="rule" id="%sr"></div>' % sid)
            js.append("tl.fromTo('#%sr',{scaleX:0},{scaleX:1,duration:0.7,ease:'power4.inOut'},%.3f);" % (sid, t0 + 0.5))

    elif layout == "stack":
        ws = sc.get("words") or (sc.get("text", "").split())
        acc = sc.get("accentIndex", 1 if len(ws) > 1 else 0)
        for k, w in enumerate(ws[:3]):
            wid = "%sw%d" % (sid, k)
            cls = "stackw" + (" em" if k == acc else "")
            body.append('<div class="mask"><span class="%s ink fit" id="%s">%s</span></div>' % (cls, wid, esc(w)))
            fits.append("#" + wid); anim("#" + wid, motion, 0.18 + k * 0.12)

    elif layout == "cta":
        lines = sc.get("lines") or [sc.get("text", "Read the full story")]
        h, ids = _lines_html(sid, "serif", lines[:2])
        body.append(h)
        for k, wid in enumerate(ids):
            fits.append("#" + wid); anim("#" + wid, motion, 0.2 + k * 0.13)
        body.append('<div class="wordmark ink" id="%swm">%s</div>' % (sid, _brand_html(sc.get("brand"))))
        js.append("tl.fromTo('#%swm',{opacity:0,y:18},{opacity:1,y:0,duration:0.6},%.3f);" % (sid, t0 + 0.7))

    else:  # hook (default): one or two big sans words
        txt = sc.get("hero") or sc.get("text") or "..."
        body.append('<div class="mask"><span class="word ink fit" id="%sh">%s</span></div>' % (sid, emph(txt)))
        fits.append("#%sh" % sid); anim("#%sh" % sid, motion, 0.2)
        if sc.get("label"):
            body.append('<div class="label ink" id="%slb">%s</div>' % (sid, emph(sc["label"])))
            anim("#%slb" % sid, "maskup", 0.45)

    section = ('<section id="%s" class="scene clip al-%s" data-start="%.3f" data-duration="%.3f" data-track-index="0" style="z-index:%d">'
               '<div class="inner">%s</div></section>' % (sid, align, t0, sc["end"] - t0, i + 1, "".join(body)))
    return section, js, fits


CSS_TMPL = """
*{margin:0;padding:0;box-sizing:border-box}
:root{--paper:$paper;--ink:$ink;--soft:$soft;--grey:$grey;--line:$line;--accent:$accent;--amber:$amber;
  --serif:'Playfair Display',Georgia,serif;--sans:'Inter',system-ui,sans-serif;--mono:'IBM Plex Mono',monospace;}
html,body{width:1080px;height:1920px;overflow:hidden;background:var(--paper);font-family:var(--sans);color:var(--ink);-webkit-font-smoothing:antialiased}
#root{position:absolute;inset:0;overflow:hidden;background:var(--paper)}
.grain{position:absolute;inset:-40%;pointer-events:none;opacity:.05;mix-blend-mode:$gblend;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='2'/></filter><rect width='180' height='180' filter='url(%23n)'/></svg>")}
.vig{position:absolute;inset:0;pointer-events:none;background:radial-gradient(135% 95% at 50% 30%,transparent 58%,$vig 100%)}
#tag{position:absolute;top:150px;left:0;right:0;text-align:center;font-family:var(--mono);font-size:26px;letter-spacing:.44em;color:var(--grey);text-transform:uppercase}
#tag b{color:var(--accent);font-weight:500}
#prog{position:absolute;left:150px;right:150px;bottom:150px;height:2px;background:var(--line)}
#progfill{position:absolute;left:0;top:0;height:100%;width:0;background:var(--accent)}
.scene{position:absolute;left:110px;right:110px;top:320px;height:730px;opacity:0;display:flex;flex-direction:column;justify-content:center}
.scene.al-left{align-items:flex-start;text-align:left}
.scene.al-center{align-items:center;text-align:center}
.inner{width:100%;will-change:transform,opacity,filter}
.kick{font-family:var(--mono);font-size:28px;letter-spacing:.36em;color:var(--accent);text-transform:uppercase;margin-bottom:34px}
.mask{overflow:hidden;display:block}
.ink{display:inline-block;will-change:transform}
.word{font-family:var(--sans);font-weight:800;font-size:196px;line-height:.98;letter-spacing:-.03em;white-space:nowrap}
.num{font-family:var(--sans);font-weight:800;font-size:300px;line-height:.9;letter-spacing:-.04em;white-space:nowrap}
.numXL{font-family:var(--sans);font-weight:800;font-size:460px;line-height:.82;letter-spacing:-.05em;white-space:nowrap}
.serif{font-family:var(--serif);font-weight:600;font-size:128px;line-height:1.05;letter-spacing:-.01em}
.stackw{font-family:var(--sans);font-weight:800;font-size:150px;line-height:1.0;letter-spacing:-.03em;white-space:nowrap}
.qmark{font-family:var(--serif);font-size:200px;line-height:.5;color:var(--accent);height:90px}
.label{font-family:var(--sans);font-size:46px;font-weight:600;color:var(--soft);margin-top:30px}
.src{font-family:var(--mono);font-size:30px;letter-spacing:.14em;color:var(--grey);text-transform:uppercase;margin-top:34px}
.em{color:var(--accent)}
.rule{height:5px;width:140px;background:var(--accent);margin-top:34px;border-radius:3px;transform-origin:left center}
.scene.al-center .rule{margin-left:auto;margin-right:auto}
.wordmark{font-family:var(--mono);font-size:34px;letter-spacing:.5em;color:var(--grey);text-transform:uppercase;margin-top:46px}
.wordmark b{color:var(--accent);font-weight:500}
#cap{position:absolute;left:150px;right:150px;bottom:470px;text-align:center;min-height:170px;
  display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:0 22px;
  font-family:var(--sans);font-weight:800;font-size:58px;line-height:1.12;letter-spacing:-.01em}
#cap .cw{color:var(--soft);opacity:.5}
#cap .cw.on{color:var(--ink);opacity:1}
#cap .cw.hot{color:var(--accent)}
"""


def build_composition(payload, timed, words):
    accent = payload.get("accent", "#3F6E95")
    amber = payload.get("accent2", "#C58A3B")
    brand = payload.get("brand", "AI Newsletter")
    theme = DARK if str(payload.get("theme", "light")).lower() == "dark" else LIGHT
    total = round(timed[-1]["end"], 3)
    seed = int(hashlib.md5((payload.get("meta", {}).get("title", "") or "x").encode()).hexdigest(), 16)

    sections, alljs, allfits, windows = [], [], [], []
    for i, sc in enumerate(timed):
        motion = (sc.get("motion") if sc.get("motion") in MOTIONS else MOTIONS[(seed + i * 7) % len(MOTIONS)])
        align = sc.get("align") if sc.get("align") in ("left", "center") else ("center" if ((seed >> 3) + i) % 2 == 0 else "left")
        if (sc.get("layout") or sc.get("type")) in ("number", "bignum", "stack", "cta"):
            align = "center"
        h, js, fits = render_scene(i, sc, motion, align)
        sections.append(h); alljs += js; allfits += fits
        windows.append([i, round(sc["start"], 3), round(sc["end"], 3)])

    cwords, pidx, since = [], 0, 0
    for w in words:
        cwords.append({"w": w["w"], "s": round(w["s"], 3), "e": round(w["e"], 3), "p": pidx})
        since += 1
        if w["w"][-1:] in ".!?,:" or since >= 3:
            pidx += 1; since = 0

    css = (CSS_TMPL.replace("$paper", theme["paper"]).replace("$ink", theme["ink"]).replace("$soft", theme["soft"])
           .replace("$grey", theme["grey"]).replace("$line", theme["line"]).replace("$vig", theme["vig"])
           .replace("$gblend", theme["gblend"]).replace("$accent", accent).replace("$amber", amber))

    runtime = """
gsap.defaults({ease:'power3.out'});
var tl=gsap.timeline({paused:true});var q=function(s){return document.querySelector(s);};
var MAXW=%d;%s.forEach(function(sel){var el=q(sel);if(!el)return;var fs=parseFloat(getComputedStyle(el).fontSize);var g=0;
  while(el.scrollWidth>MAXW&&fs>30&&g<260){fs-=5;el.style.fontSize=fs+'px';g++;}});
var W=%s;W.forEach(function(s){tl.fromTo('#s'+s[0],{opacity:0},{opacity:1,duration:0.3,ease:'power2.out'},s[1]);tl.to('#s'+s[0],{opacity:0,duration:0.26,ease:'power2.in'},s[2]-0.26);});
function cnt(sel,from,to,dec,pre,suf,at,dur){var el=q(sel);var o={v:from};el.textContent=pre+from.toFixed(dec)+suf;
  tl.fromTo(o,{v:from},{v:to,duration:dur,ease:'power2.out',onUpdate:function(){el.textContent=pre+o.v.toFixed(dec)+suf;}},at);}
%s
var CW=%s;var cap=q('#cap');
(function(){var h='';for(var i=0;i<CW.length;i++){h+='<span class="cw" data-i="'+i+'" data-p="'+CW[i].p+'">'+CW[i].w+'</span>';}cap.innerHTML=h;})();
var spans=cap.querySelectorAll('.cw');
tl.to({},{duration:%g,onUpdate:function(){var t=tl.time();q('#progfill').style.width=(t/%g*100)+'%%';
  var ai=-1;for(var i=0;i<CW.length;i++){if(t>=CW[i].s-0.05)ai=i;else break;}var ap=ai>=0?CW[ai].p:-1;
  for(var j=0;j<spans.length;j++){var sp=spans[j];var p=+sp.getAttribute('data-p');var idx=+sp.getAttribute('data-i');
    sp.style.display=(p===ap)?'inline-block':'none';sp.className='cw'+(p===ap?' on':'')+(idx===ai?' hot':'');}}},0);
window.__timelines=window.__timelines||{};window.__timelines['main']=tl;tl.seek(0);
""" % (820, json.dumps(allfits), json.dumps(windows), "\n".join(alljs), json.dumps(cwords), total, total)

    return ("<!doctype html><html lang='en'><head><meta charset='UTF-8'>"
            "<meta name='viewport' content='width=1080, height=1920'>"
            "<script src='https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/gsap.min.js'></script>"
            "<style>" + css + "</style></head>"
            "<body><div id='root' data-composition-id='main' data-start='0' data-duration='" + str(total) + "' "
            "data-width='1080' data-height='1920' data-fps='30'>"
            "<div class='grain'></div><div class='vig'></div><div id='tag'>" + _brand_html(brand) + "</div>"
            "<div id='prog'><div id='progfill'></div></div>" + "".join(sections) +
            "<div id='cap'></div></div><script>" + runtime + "</script></body></html>")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload")
    ap.add_argument("--out", default=os.path.join(REPO, "out.mp4"))
    args = ap.parse_args()
    payload = load_payload(args)
    scenes = payload["scenes"]

    work = tempfile.mkdtemp(prefix="reel_")
    narration = " ".join((s.get("vo") or "").strip() for s in scenes if s.get("vo")).strip() or "Read the full story."
    vo_mp3 = os.path.join(work, "vo.mp3")
    words = synth_elevenlabs(narration, vo_mp3) or synth_fallback(narration, vo_mp3, payload.get("voice"))
    if words is None:
        raise SystemExit("Voiceover failed (all engines)")
    vdur = ffprobe_dur(vo_mp3)
    total = vdur + float(payload.get("tail", 0.7))

    counts = [max(len((s.get("vo") or "").split()), 1) for s in scenes]
    idx, timed = 0, []
    for s, c in zip(scenes, counts):
        start = words[idx]["s"] if idx < len(words) else total
        idx = min(idx + c, len(words))
        end = words[idx]["s"] if idx < len(words) else total
        st = dict(s); st["start"] = round(max(0.0, start), 3); st["end"] = round(end, 3)
        timed.append(st)
    timed[-1]["end"] = round(total, 3)
    for k in range(1, len(timed)):
        if timed[k]["start"] < timed[k-1]["start"] + 0.4:
            timed[k]["start"] = round(timed[k-1]["start"] + 0.4, 3)
        if timed[k-1]["end"] > timed[k]["start"]:
            timed[k-1]["end"] = timed[k]["start"]
    timed[-1]["end"] = round(total, 3)

    os.makedirs(PROJECT, exist_ok=True)
    html = build_composition(payload, timed, words)
    with open(os.path.join(PROJECT, "index.html"), "w") as f:
        f.write(html)
    log("composition:", len(html), "bytes,", len(scenes), "scenes, reel", round(total, 2), "s,",
        "theme", payload.get("theme", "light"))

    subprocess.run(HF + ["browser", "ensure"], cwd=PROJECT, check=False)
    silent = os.path.join(work, "silent.mp4")
    subprocess.check_call(HF + ["render", ".", "--output", silent, "--quality", "looks", "--quiet"], cwd=PROJECT)

    out = os.path.abspath(args.out)
    subprocess.check_call(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", silent, "-i", vo_mp3,
                           "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                           "-shortest", "-movflags", "+faststart", "-y", out])
    log("OK ->", out, round(ffprobe_dur(out), 2), "s")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
