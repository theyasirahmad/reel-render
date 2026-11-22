#!/usr/bin/env python3
"""Build a punchy, varied editorial reel (HyperFrames) from a JSON payload.

Design: one bold HOOK element per scene (keyword / number / short phrase) in the
upper area, TikTok-style word-synced CAPTIONS in the lower area, motion + layout
that VARY per scene and per reel (seeded), auto-fit text so nothing overflows,
and a short "read the full story" close. Not an explainer — a tease.

Pipeline: payload -> voice + word timings (ElevenLabs timestamps, fallback approx)
          -> scene timing aligned to the spoken words
          -> generate composition -> npx hyperframes render -> MP4 (+ voice).

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
        if c is None:
            c = ""
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
    """Return word-timing list on success, else None (and leave out_mp3 unwritten)."""
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
                    log("voice: ElevenLabs", vid, model, "(" + tag + ")", "| words:", len(words))
                    return words
            prev = (vid[:4] + ".." + vid[-3:]) if len(vid) > 9 else vid
            log("ElevenLabs rejected (%s): HTTP %s %s [voice_id len=%d %s]" % (tag, r.status_code, r.text[:140], len(vid), prev))
        except Exception as e:
            log("ElevenLabs error:", repr(e)[:160])
    return None


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
            log("voice: edge-tts (fallback)", voice or "en-US-AndrewNeural")
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
    # approximate word timings from audio duration, proportional to word length
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


def ffprobe_dur(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "default=nk=1:nw=1", path]).decode().strip()
    return float(out)


# ---------------------------------------------------------------- composition
CSS = """
*{margin:0;padding:0;box-sizing:border-box}
:root{--paper:#F3EFE6;--ink:#20222A;--soft:#4B4D55;--grey:#8C887E;--line:#DBD4C6;--accent:#3F6E95;--amber:#C58A3B;
  --serif:'Playfair Display',Georgia,serif;--sans:'Inter',system-ui,sans-serif;--mono:'IBM Plex Mono',monospace;}
html,body{width:1080px;height:1920px;overflow:hidden;background:var(--paper);font-family:var(--sans);color:var(--ink);-webkit-font-smoothing:antialiased}
#root{position:absolute;inset:0;overflow:hidden;background:var(--paper)}
.grain{position:absolute;inset:-40%;pointer-events:none;opacity:.05;mix-blend-mode:multiply;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='2'/></filter><rect width='180' height='180' filter='url(%23n)'/></svg>")}
.vig{position:absolute;inset:0;pointer-events:none;background:radial-gradient(135% 95% at 50% 30%,transparent 58%,rgba(80,70,50,.12) 100%)}
#tag{position:absolute;top:150px;left:0;right:0;text-align:center;font-family:var(--mono);font-size:26px;letter-spacing:.44em;color:var(--grey);text-transform:uppercase}
#tag b{color:var(--accent);font-weight:500}
#prog{position:absolute;left:150px;right:150px;bottom:150px;height:2px;background:var(--line)}
#progfill{position:absolute;left:0;top:0;height:100%;width:0;background:var(--accent)}
/* HERO zone (upper-centre) */
.scene{position:absolute;left:110px;right:110px;top:330px;height:720px;opacity:0;display:flex;flex-direction:column;justify-content:center}
.scene.al-left{align-items:flex-start;text-align:left}
.scene.al-center{align-items:center;text-align:center}
.inner{width:100%;will-change:transform,opacity,filter}
.kick{font-family:var(--mono);font-size:28px;letter-spacing:.36em;color:var(--accent);text-transform:uppercase;margin-bottom:34px}
.mask{overflow:hidden;display:block}
.ink{display:inline-block;will-change:transform}
.num{font-family:var(--sans);font-weight:800;font-size:300px;line-height:.9;letter-spacing:-.04em;color:var(--ink);white-space:nowrap}
.word{font-family:var(--sans);font-weight:800;font-size:200px;line-height:.98;letter-spacing:-.03em;white-space:nowrap}
.phrase{font-family:var(--serif);font-weight:600;font-size:132px;line-height:1.04;letter-spacing:-.01em}
.label{font-family:var(--sans);font-size:46px;font-weight:600;color:var(--soft);margin-top:30px}
.em{color:var(--accent)}
.rule{height:5px;width:140px;background:var(--accent);margin-top:34px;border-radius:3px;transform-origin:left center}
.scene.al-center .rule{margin-left:auto;margin-right:auto}
.wordmark{font-family:var(--mono);font-size:34px;letter-spacing:.5em;color:var(--grey);text-transform:uppercase;margin-top:46px}
.wordmark b{color:var(--accent);font-weight:500}
/* CAPTION zone (lower) */
#cap{position:absolute;left:150px;right:150px;bottom:470px;text-align:center;min-height:180px;
  display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:0 22px;
  font-family:var(--sans);font-weight:800;font-size:60px;line-height:1.12;letter-spacing:-.01em}
#cap .cw{color:var(--soft);opacity:.55;transition:none}
#cap .cw.on{color:var(--ink);opacity:1}
#cap .cw.on b,#cap .cw.hot{color:var(--accent)}
"""


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


# motion styles applied to a hero .inner / .ink
MOTIONS = ["maskup", "pop", "slide", "blur"]


def _motion_js(sel, style, at):
    if style == "pop":
        return "tl.fromTo('%s',{scale:0.74,opacity:0},{scale:1,opacity:1,duration:0.66,ease:'back.out(1.5)'},%.3f);" % (sel, at)
    if style == "slide":
        return "tl.fromTo('%s',{xPercent:-22,opacity:0},{xPercent:0,opacity:1,duration:0.6,ease:'power4.out'},%.3f);" % (sel, at)
    if style == "blur":
        return "tl.fromTo('%s',{opacity:0,filter:'blur(18px)'},{opacity:1,filter:'blur(0px)',duration:0.7,ease:'power3.out'},%.3f);" % (sel, at)
    return "tl.fromTo('%s',{yPercent:120},{yPercent:0,duration:0.72,ease:'power4.out'},%.3f);" % (sel, at)


def render_scene(i, sc, motion, align):
    sid = "s%d" % i
    t0, t1 = sc["start"], sc["end"]
    typ = sc.get("type", "word")
    body, js, fits = [], [], []
    if sc.get("kicker"):
        body.append('<div class="kick ink" id="%sk">%s</div>' % (sid, emph(sc["kicker"])))
        js.append(_motion_js("#%sk" % sid, "slide" if motion != "slide" else "maskup", t0 + 0.12))

    if typ == "number":
        n = sc.get("number", {})
        pre, suf, dec = n.get("prefix", ""), n.get("suffix", ""), int(n.get("decimals", 0))
        frm, to = float(n.get("from", 0)), float(n.get("to", 0))
        body.append('<div class="mask"><span class="num ink fit" id="%snum">%s</span></div>'
                    % (sid, esc(pre + ("%.*f" % (dec, frm)) + suf)))
        fits.append("#%snum" % sid)
        if sc.get("label"):
            body.append('<div class="label ink" id="%slb">%s</div>' % (sid, emph(sc["label"])))
        js.append(_motion_js("#%snum" % sid, motion, t0 + 0.22))
        cdur = min(1.7, max(0.9, (t1 - t0) * 0.5))
        js.append("cnt('#%snum',%g,%g,%d,%s,%s,%.3f,%.3f);" % (sid, frm, to, dec, json.dumps(pre), json.dumps(suf), t0 + 0.3, cdur))
        if sc.get("label"):
            js.append(_motion_js("#%slb" % sid, "maskup", t0 + 0.5))

    elif typ == "phrase":
        for k, L in enumerate(sc.get("lines", ["..."])[:3]):
            body.append('<div class="mask"><span class="phrase ink fit" id="%sp%d">%s</span></div>' % (sid, k, emph(L)))
            fits.append("#%sp%d" % (sid, k))
            js.append(_motion_js("#%sp%d" % (sid, k), motion, t0 + 0.2 + k * 0.14))
        if sc.get("rule"):
            body.append('<div class="rule" id="%sr"></div>' % sid)
            js.append("tl.fromTo('#%sr',{scaleX:0},{scaleX:1,duration:0.7,ease:'power4.inOut'},%.3f);" % (sid, t0 + 0.5))

    elif typ == "cta":
        for k, L in enumerate(sc.get("lines", ["Read the full story"])[:2]):
            body.append('<div class="mask"><span class="phrase ink fit" id="%sc%d">%s</span></div>' % (sid, k, emph(L)))
            fits.append("#%sc%d" % (sid, k))
            js.append(_motion_js("#%sc%d" % (sid, k), motion, t0 + 0.2 + k * 0.14))
        body.append('<div class="wordmark ink" id="%sw">%s</div>' % (sid, _brand_html(sc.get("brand"))))
        js.append("tl.fromTo('#%sw',{opacity:0,y:18},{opacity:1,y:0,duration:0.6},%.3f);" % (sid, t0 + 0.7))

    else:  # word
        body.append('<div class="mask"><span class="word ink fit" id="%sw">%s</span></div>' % (sid, emph(sc.get("hero", "..."))))
        fits.append("#%sw" % sid)
        js.append(_motion_js("#%sw" % sid, motion, t0 + 0.2))
        if sc.get("label"):
            body.append('<div class="label ink" id="%slb">%s</div>' % (sid, emph(sc["label"])))
            js.append(_motion_js("#%slb" % sid, "maskup", t0 + 0.45))

    section = ('<section id="%s" class="scene clip al-%s" data-start="%.3f" data-duration="%.3f" data-track-index="0" style="z-index:%d">'
               '<div class="inner">%s</div></section>' % (sid, align, t0, t1 - t0, i + 1, "".join(body)))
    return section, js, fits


def build_composition(payload, timed, words):
    accent = payload.get("accent", "#3F6E95")
    amber = payload.get("accent2", "#C58A3B")
    brand = payload.get("brand", "AI Newsletter")
    total = round(timed[-1]["end"], 3)
    seed = int(hashlib.md5((payload.get("meta", {}).get("title", "") or "x").encode()).hexdigest(), 16)

    sections, alljs, allfits, windows = [], [], [], []
    for i, sc in enumerate(timed):
        motion = MOTIONS[(seed + i * 7) % len(MOTIONS)]
        align = "center" if ((seed >> 3) + i) % 2 == 0 else "left"
        if sc.get("type") == "number":
            align = "center"  # numbers read best centred
        h, js, fits = render_scene(i, sc, motion, align)
        sections.append(h); alljs += js; allfits += fits
        windows.append([i, round(sc["start"], 3), round(sc["end"], 3)])

    # caption words -> phrase groups (break on sentence punctuation, else every 3)
    cwords = []
    pidx, since = 0, 0
    for w in words:
        cwords.append({"w": w["w"], "s": round(w["s"], 3), "e": round(w["e"], 3), "p": pidx})
        since += 1
        if w["w"][-1:] in ".!?,:" or since >= 3:
            pidx += 1; since = 0

    runtime = """
gsap.defaults({ease:'power3.out'});
var tl=gsap.timeline({paused:true});
var q=function(s){return document.querySelector(s);};
// auto-fit: shrink hero text until it fits its zone width
var MAXW=%d;
%s.forEach(function(sel){var el=q(sel);if(!el)return;var fs=parseFloat(getComputedStyle(el).fontSize);var g=0;
  while(el.scrollWidth>MAXW&&fs>34&&g<220){fs-=5;el.style.fontSize=fs+'px';g++;}});
var W=%s;
W.forEach(function(s){
  tl.fromTo('#s'+s[0],{opacity:0},{opacity:1,duration:0.3,ease:'power2.out'},s[1]);
  tl.to('#s'+s[0],{opacity:0,duration:0.26,ease:'power2.in'},s[2]-0.26);
});
function cnt(sel,from,to,dec,pre,suf,at,dur){var el=q(sel);var o={v:from};el.textContent=pre+from.toFixed(dec)+suf;
  tl.fromTo(o,{v:from},{v:to,duration:dur,ease:'power2.out',onUpdate:function(){el.textContent=pre+o.v.toFixed(dec)+suf;}},at);}
%s
// TikTok captions: build spans once, toggle per frame
var CW=%s;
var cap=q('#cap');
(function(){var h='';for(var i=0;i<CW.length;i++){h+='<span class="cw" data-i="'+i+'" data-p="'+CW[i].p+'">'+CW[i].w+'</span>';}cap.innerHTML=h;})();
var spans=cap.querySelectorAll('.cw');
tl.to({},{duration:%g,onUpdate:function(){
  var t=tl.time();q('#progfill').style.width=(t/%g*100)+'%%';
  var ai=-1;for(var i=0;i<CW.length;i++){if(t>=CW[i].s-0.05)ai=i;else break;}
  var ap=ai>=0?CW[ai].p:-1;
  for(var j=0;j<spans.length;j++){var sp=spans[j];var p=+sp.getAttribute('data-p');var idx=+sp.getAttribute('data-i');
    sp.style.display=(p===ap)?'inline-block':'none';
    sp.className='cw'+(p===ap?' on':'')+(idx===ai?' hot':'');}
}},0);
window.__timelines=window.__timelines||{};window.__timelines['main']=tl;tl.seek(0);
""" % (820, json.dumps(allfits), json.dumps(windows), "\n".join(alljs), json.dumps(cwords), total, total)

    doc = ("<!doctype html><html lang='en'><head><meta charset='UTF-8'>"
           "<meta name='viewport' content='width=1080, height=1920'>"
           "<script src='https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/gsap.min.js'></script>"
           "<style>" + CSS + ("\n:root{--accent:%s;--amber:%s}" % (accent, amber)) + "</style></head>"
           "<body><div id='root' data-composition-id='main' data-start='0' data-duration='" + str(total) + "' "
           "data-width='1080' data-height='1920' data-fps='30'>"
           "<div class='grain'></div><div class='vig'></div><div id='tag'>" + _brand_html(brand) + "</div>"
           "<div id='prog'><div id='progfill'></div></div>" + "".join(sections) +
           "<div id='cap'></div></div><script>" + runtime + "</script></body></html>")
    return doc


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
    words = synth_elevenlabs(narration, vo_mp3)
    if words is None:
        words = synth_fallback(narration, vo_mp3, payload.get("voice"))
    if words is None:
        raise SystemExit("Voiceover failed (all engines)")
    vdur = ffprobe_dur(vo_mp3)
    total = vdur + float(payload.get("tail", 0.7))

    # scene timing aligned to spoken words (hero changes land on the narration)
    counts = [max(len((s.get("vo") or "").split()), 1) for s in scenes]
    idx = 0
    timed = []
    for s, c in zip(scenes, counts):
        start = words[idx]["s"] if idx < len(words) else total
        idx = min(idx + c, len(words))
        end = words[idx]["s"] if idx < len(words) else total
        st = dict(s); st["start"] = round(max(0.0, start), 3); st["end"] = round(end, 3)
        timed.append(st)
    timed[-1]["end"] = round(total, 3)
    for k in range(1, len(timed)):  # no zero/negative spans
        if timed[k]["start"] < timed[k-1]["start"] + 0.4:
            timed[k]["start"] = round(timed[k-1]["start"] + 0.4, 3)
        if timed[k-1]["end"] > timed[k]["start"]:
            timed[k-1]["end"] = timed[k]["start"]
    timed[-1]["end"] = round(total, 3)

    os.makedirs(PROJECT, exist_ok=True)
    html = build_composition(payload, timed, words)
    with open(os.path.join(PROJECT, "index.html"), "w") as f:
        f.write(html)
    log("composition:", len(html), "bytes,", len(scenes), "scenes, reel", round(total, 2), "s")

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
