#!/usr/bin/env python3
"""Build a premium editorial reel (HyperFrames) from a JSON payload.

Pipeline:
  payload -> voiceover (ElevenLabs, fallback edge-tts/gTTS)
          -> per-scene timing (proportional to each scene's narration)
          -> generate HyperFrames composition (project/index.html)
          -> npx hyperframes render -> MP4 (+ muxed voiceover)

Payload comes from $REEL_PAYLOAD (JSON string) or --payload <file>.
Design system: warm ivory, charcoal ink, Playfair Display (editorial),
Inter (numbers/body), IBM Plex Mono (labels). Accent chosen per reel.
"""
import os, sys, json, subprocess, argparse, tempfile, shutil, html as _html

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PROJECT = os.path.join(REPO, "project")
HF = ["npx", "--yes", "hyperframes@0.9.81"]
FPS = 30


def log(*a): print(*a, flush=True)


# ----------------------------------------------------------------------------- payload
def load_payload(args):
    if args.payload:
        with open(args.payload) as f:
            return json.load(f)
    env = os.environ.get("REEL_PAYLOAD")
    if env:
        return json.loads(env)
    raise SystemExit("No payload: set $REEL_PAYLOAD or pass --payload file.json")


# ----------------------------------------------------------------------------- voice
def synth_elevenlabs(text, out_mp3):
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        return False
    voice = os.environ.get("ELEVENLABS_VOICE_ID", "").strip() or "pNInz6obpgDQGcFmaJgB"
    model = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2").strip()
    try:
        import requests
        r = requests.post(
            "https://api.elevenlabs.io/v1/text-to-speech/" + voice,
            headers={"xi-api-key": key, "accept": "audio/mpeg", "content-type": "application/json"},
            json={"text": text, "model_id": model,
                  "voice_settings": {"stability": 0.42, "similarity_boost": 0.8,
                                     "style": 0.0, "use_speaker_boost": True}},
            timeout=120)
        if r.status_code == 200 and len(r.content) > 2000:
            with open(out_mp3, "wb") as f:
                f.write(r.content)
            log("voice: ElevenLabs", voice, model)
            return True
        log("ElevenLabs failed:", r.status_code, r.text[:160])
    except Exception as e:
        log("ElevenLabs error:", repr(e)[:160])
    return False


def synth_fallback(text, out_mp3, voice):
    try:  # edge-tts: neural, free, works on clean networks (GitHub runners)
        import asyncio, edge_tts
        async def _run():
            c = edge_tts.Communicate(text, voice or "en-US-AndrewNeural", rate="+5%")
            await c.save(out_mp3)
        asyncio.get_event_loop().run_until_complete(_run())
        if os.path.getsize(out_mp3) > 2000:
            log("voice: edge-tts (fallback)", voice or "en-US-AndrewNeural")
            return True
    except Exception as e:
        log("edge-tts failed:", repr(e)[:160])
    try:
        from gtts import gTTS
        gTTS(text=text, lang="en", tld="com").save(out_mp3)
        log("voice: gTTS (last-resort fallback)")
        return True
    except Exception as e:
        log("gTTS failed:", repr(e)[:160])
    return False


def synth_voice(text, out_mp3, voice):
    return synth_elevenlabs(text, out_mp3) or synth_fallback(text, out_mp3, voice)


def ffprobe_dur(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "default=nk=1:nw=1", path]).decode().strip()
    return float(out)


# ----------------------------------------------------------------------------- composition
CSS = """
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --paper:#F3EFE6;--ink:#22242A;--soft:#4B4D55;--grey:#8C887E;--line:#DBD4C6;
  --accent:#3F6E95;--amber:#C58A3B;
  --serif:'Playfair Display',Georgia,serif;--sans:'Inter',system-ui,sans-serif;--mono:'IBM Plex Mono',monospace;
}
html,body{width:1080px;height:1920px;overflow:hidden;background:var(--paper);
  font-family:var(--sans);color:var(--ink);-webkit-font-smoothing:antialiased}
#root{position:absolute;inset:0;overflow:hidden;background:var(--paper)}
.grain{position:absolute;inset:-40%;pointer-events:none;opacity:.05;mix-blend-mode:multiply;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='2'/></filter><rect width='180' height='180' filter='url(%23n)'/></svg>")}
.vig{position:absolute;inset:0;pointer-events:none;background:radial-gradient(135% 95% at 50% 32%,transparent 60%,rgba(80,70,50,.10) 100%)}
#tag{position:absolute;top:150px;left:150px;font-family:var(--mono);font-size:27px;letter-spacing:.42em;color:var(--grey);text-transform:uppercase}
#tag b{color:var(--accent);font-weight:500}
#prog{position:absolute;left:150px;right:150px;bottom:158px;height:2px;background:var(--line)}
#progfill{position:absolute;left:0;top:0;height:100%;width:0;background:var(--accent)}
.scene{position:absolute;left:150px;right:172px;top:360px;bottom:470px;opacity:0;display:flex;flex-direction:column;justify-content:center}
.inner{will-change:transform}
.kick{font-family:var(--mono);font-size:30px;letter-spacing:.34em;color:var(--accent);text-transform:uppercase;margin-bottom:44px;line-height:1.3}
.mask{overflow:hidden;display:block}
.ln{display:block;will-change:transform}
.num{font-family:var(--sans);font-weight:800;font-size:252px;line-height:.9;letter-spacing:-.04em;color:var(--ink)}
.num.sm{font-size:212px}
.serif{font-family:var(--serif);font-size:98px;line-height:1.1;letter-spacing:-.01em;font-weight:500;color:var(--ink)}
.serif.it{font-style:italic}
.serif .em{color:var(--accent)}
.h{font-family:var(--sans);font-weight:700;font-size:82px;line-height:1.08;letter-spacing:-.02em}
.h .em{color:var(--accent)}
.sub{font-family:var(--sans);font-size:42px;font-weight:450;line-height:1.3;color:var(--soft);margin-top:32px}
.rule{height:4px;width:150px;background:var(--accent);margin:38px 0;border-radius:2px}
.bars{display:flex;align-items:flex-end;gap:44px;height:340px;margin-top:64px}
.bcol{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end}
.bval{font-family:var(--sans);font-weight:800;font-size:58px;margin-bottom:18px;letter-spacing:-.02em}
.bshape{width:92px;border-radius:6px 6px 0 0;height:0}
.blabs{display:flex;gap:44px;margin-top:22px}
.blab{flex:1;font-family:var(--mono);font-size:25px;letter-spacing:.05em;color:var(--grey);text-align:center;line-height:1.3;text-transform:uppercase}
.items{margin-top:36px}
.item{display:flex;align-items:baseline;gap:30px;margin:26px 0;opacity:0;will-change:transform}
.inum{font-family:var(--mono);font-size:38px;color:var(--accent);width:60px;flex:none}
.itext{font-family:var(--sans);font-weight:700;font-size:62px;line-height:1.05;letter-spacing:-.02em}
#cap{position:absolute;left:170px;right:170px;bottom:248px;font-family:var(--sans);font-size:40px;font-weight:600;text-align:center;color:var(--ink);opacity:0}
.wordmark{font-family:var(--mono);font-size:40px;letter-spacing:.5em;color:var(--grey);text-transform:uppercase;margin-top:52px}
.wordmark b{color:var(--accent);font-weight:500}
"""

BARCOLORS = {"accent": "var(--accent)", "amber": "var(--amber)", "grey": "#B8B0A0", "ink": "var(--ink)"}


def esc(s):
    return _html.escape(str(s if s is not None else ""), quote=False)


def emph(s):
    """*word* -> accent span, _word_ -> muted. Escapes the rest."""
    s = esc(s)
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "*":
            j = s.find("*", i + 1)
            if j > i:
                out.append('<span class="em">' + s[i+1:j] + "</span>"); i = j + 1; continue
        if c == "_":
            j = s.find("_", i + 1)
            if j > i:
                out.append('<span style="color:var(--grey)">' + s[i+1:j] + "</span>"); i = j + 1; continue
        out.append(c); i += 1
    return "".join(out)


def ln(cls, text):
    return '<div class="mask"><span class="ln"><span class="%s">%s</span></span></div>' % (cls, emph(text))


def render_scene(i, sc):
    """Return (html, js_lines) for one timed scene. sc has start,end,dur baked in."""
    sid = "s%d" % i
    t0, t1, dur = sc["start"], sc["end"], sc["end"] - sc["start"]
    typ = sc.get("type", "statement")
    body, js = [], []

    def reveal(at, stag=0.11):
        js.append("rev('#%s .ln',%.3f,%.3f);" % (sid, at, stag))

    if sc.get("kicker"):
        body.append('<div class="kick"><span class="ln">%s</span></div>' % emph(sc["kicker"]))

    if typ == "stat":
        n = sc.get("number", {})
        pre, suf = n.get("prefix", ""), n.get("suffix", "")
        dec = int(n.get("decimals", 0))
        frm, to = float(n.get("from", 0)), float(n.get("to", 0))
        cls = "num sm" if n.get("small") else "num"
        body.append('<div class="mask"><span class="ln"><span class="%s" id="%snum">%s</span></span></div>'
                    % (cls, sid, esc(pre + ("%.*f" % (dec, frm)) + suf)))
        if sc.get("rule", True):
            body.append('<div class="rule" id="%srule"></div>' % sid)
        for L in sc.get("serif", []):
            body.append(ln("serif it", L))
        if sc.get("sub"):
            body.append(ln("sub", sc["sub"]))
        reveal(t0 + 0.25, 0.12)
        cdur = min(1.9, max(1.0, dur * 0.32))
        js.append("cnt('#%snum',%g,%g,%d,%s,%s,%.3f,%.3f);"
                  % (sid, frm, to, dec, json.dumps(pre), json.dumps(suf), t0 + 0.5, cdur))
        if sc.get("rule", True):
            js.append("tl.fromTo('#%srule',{scaleX:0,transformOrigin:'left center'},{scaleX:1,duration:0.8,ease:'power4.inOut'},%.3f);" % (sid, t0 + 0.7))

    elif typ == "statement":
        for L in sc.get("serif", []):
            body.append(ln("serif", L))
        if sc.get("sub"):
            body.append(ln("sub", sc["sub"]))
        reveal(t0 + 0.28, 0.13)

    elif typ == "bars":
        if sc.get("headline"):
            body.append(ln("h", sc["headline"]))
        bars = sc.get("bars", [])[:3]
        maxv = max([abs(float(b.get("value", 0))) for b in bars] + [1])
        body.append('<div class="bars">')
        for k, b in enumerate(bars):
            col = BARCOLORS.get(b.get("color", "accent"), "var(--accent)")
            vcol = col
            body.append('<div class="bcol"><div class="bval" style="color:%s" id="%sb%dv">0</div>'
                        '<div class="bshape" id="%sb%d" style="background:%s"></div></div>' % (vcol, sid, k, sid, k, col))
        body.append('</div><div class="blabs">')
        for b in bars:
            body.append('<div class="blab">%s</div>' % esc(b.get("label", "")))
        body.append('</div>')
        reveal(t0 + 0.25, 0.1)
        for k, b in enumerate(bars):
            v = float(b.get("value", 0)); suf = b.get("suffix", "%")
            at = t0 + 0.9 + k * 0.2
            js.append("tl.fromTo('#%sb%d',{height:0},{height:%.1f,duration:1.0,ease:'power3.out'},%.3f);"
                      % (sid, k, (abs(v) / maxv) * 300.0, at))
            js.append("barcount('#%sb%dv',%g,%s,%.3f);" % (sid, k, v, json.dumps(suf), at))

    elif typ == "list":
        if sc.get("headline"):
            body.append(ln("h", sc["headline"]))
        body.append('<div class="items">')
        items = sc.get("items", [])[:4]
        for k, it in enumerate(items):
            body.append('<div class="item" id="%si%d"><span class="inum">%02d</span><span class="itext">%s</span></div>'
                        % (sid, k, k + 1, emph(it)))
        body.append('</div>')
        reveal(t0 + 0.25, 0.1)
        for k in range(len(items)):
            js.append("tl.fromTo('#%si%d',{opacity:0,x:-36},{opacity:1,x:0,duration:0.6},%.3f);" % (sid, k, t0 + 0.9 + k * 0.5))

    elif typ == "outro":
        for L in sc.get("serif", ["Thanks for watching."]):
            body.append(ln("serif", L))
        body.append('<div class="wordmark">%s</div>' % _brand_html(sc.get("brand")))
        reveal(t0 + 0.3, 0.14)
        js.append("tl.fromTo('#%s .wordmark',{opacity:0,y:20},{opacity:1,y:0,duration:0.7},%.3f);" % (sid, t0 + 1.0))

    section = ('<section id="%s" class="scene clip" data-start="%.3f" data-duration="%.3f" '
               'data-track-index="0" style="z-index:%d"><div class="inner">%s</div></section>'
               % (sid, t0, dur, i + 1, "".join(body)))
    return section, js


def _brand_html(brand):
    brand = brand or "AI Newsletter"
    parts = brand.split(" ", 1)
    if len(parts) == 2:
        return esc(parts[0]) + "&nbsp;<b>" + esc(parts[1]) + "</b>"
    return "<b>" + esc(brand) + "</b>"


def build_composition(payload, timed):
    accent = payload.get("accent", "#3F6E95")
    amber = payload.get("accent2", "#C58A3B")
    brand = payload.get("brand", "AI Newsletter")
    total = round(timed[-1]["end"], 3)

    sections, alljs = [], []
    windows = []
    for i, sc in enumerate(timed):
        h, js = render_scene(i, sc)
        sections.append(h); alljs += js
        windows.append([i, round(sc["start"], 3), round(sc["end"], 3)])

    caps = [[round(sc["start"] + 0.4, 3), round(sc["end"] - 0.3, 3), esc(sc.get("caption", ""))] for sc in timed]

    runtime = """
gsap.defaults({ease:'power3.out'});
var tl=gsap.timeline({paused:true});
var q=function(s){return document.querySelector(s);};
var W=%s;
W.forEach(function(s){
  tl.fromTo('#s'+s[0],{opacity:0},{opacity:1,duration:0.34,ease:'power2.out'},s[1]);
  tl.to('#s'+s[0],{opacity:0,duration:0.28,ease:'power2.in'},s[2]-0.28);
  tl.fromTo('#s'+s[0]+' .inner',{scale:1.0,y:14},{scale:1.035,y:0,duration:(s[2]-s[1]),ease:'none'},s[1]);
});
function rev(sel,at,stag){gsap.utils.toArray(sel).forEach(function(n,i){
  tl.fromTo(n,{yPercent:118},{yPercent:0,duration:0.78,ease:'power4.out'},at+i*(stag||0.1));});}
function cnt(sel,from,to,dec,pre,suf,at,dur){var el=q(sel);var o={v:from};el.textContent=pre+from.toFixed(dec)+suf;
  tl.fromTo(o,{v:from},{v:to,duration:dur,ease:'power2.out',onUpdate:function(){el.textContent=pre+o.v.toFixed(dec)+suf;}},at);}
function barcount(sel,to,suf,at){var el=q(sel);var o={v:0};
  tl.fromTo(o,{v:0},{v:to,duration:1.0,ease:'power3.out',onUpdate:function(){el.textContent=Math.round(o.v)+suf;}},at);}
%s
var CAPS=%s;
tl.to({},{duration:%g,onUpdate:function(){var t=tl.time();
  q('#progfill').style.width=(t/%g*100)+'%%';
  var ct='';for(var i=0;i<CAPS.length;i++){if(t>=CAPS[i][0]&&t<CAPS[i][1])ct=CAPS[i][2];}
  var c=q('#cap');if(ct){c.textContent=ct;c.style.opacity=1;}else{c.style.opacity=0;}
}},0);
window.__timelines=window.__timelines||{};window.__timelines['main']=tl;tl.seek(0);
""" % (json.dumps(windows), "\n".join(alljs), json.dumps(caps), total, total)

    doc = (
        "<!doctype html><html lang='en'><head><meta charset='UTF-8'>"
        "<meta name='viewport' content='width=1080, height=1920'>"
        "<script src='https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/gsap.min.js'></script>"
        "<style>" + CSS + ("\n:root{--accent:%s;--amber:%s}" % (accent, amber)) + "</style></head>"
        "<body><div id='root' data-composition-id='main' data-start='0' data-duration='" + str(total) + "' "
        "data-width='1080' data-height='1920' data-fps='30'>"
        "<div class='grain'></div><div class='vig'></div>"
        "<div id='tag'>" + _brand_html(brand) + "</div>"
        "<div id='prog'><div id='progfill'></div></div>"
        + "".join(sections) +
        "<div id='cap'></div></div>"
        "<script>" + runtime + "</script></body></html>"
    )
    return doc


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload")
    ap.add_argument("--out", default=os.path.join(REPO, "out.mp4"))
    args = ap.parse_args()

    payload = load_payload(args)
    scenes = payload["scenes"]

    work = tempfile.mkdtemp(prefix="reel_")
    narration = " ".join((s.get("vo") or "").strip() for s in scenes if s.get("vo")).strip() or "Here is what matters."
    vo_mp3 = os.path.join(work, "vo.mp3")
    if not synth_voice(narration, vo_mp3, payload.get("voice")):
        raise SystemExit("Voiceover synthesis failed (all engines)")
    vdur = ffprobe_dur(vo_mp3)
    tail = float(payload.get("tail", 0.6))
    total = vdur + tail
    log("voice duration:", round(vdur, 2), "s ; reel:", round(total, 2), "s")

    # per-scene timing proportional to each scene's narration length
    weights = [max(len((s.get("vo") or "").strip()), 8) for s in scenes]
    wsum = sum(weights)
    t = 0.0
    timed = []
    for s, w in zip(scenes, weights):
        span = (w / wsum) * total
        st = dict(s); st["start"] = round(t, 3); st["end"] = round(t + span, 3)
        timed.append(st); t += span
    timed[-1]["end"] = round(total, 3)

    # write composition into the HyperFrames project
    os.makedirs(PROJECT, exist_ok=True)
    html = build_composition(payload, timed)
    with open(os.path.join(PROJECT, "index.html"), "w") as f:
        f.write(html)
    log("composition written:", len(html), "bytes,", len(scenes), "scenes")

    # ensure Chrome, then render silent video
    subprocess.run(HF + ["browser", "ensure"], cwd=PROJECT, check=False)
    silent = os.path.join(work, "silent.mp4")
    subprocess.check_call(HF + ["render", ".", "--output", silent, "--quality", "looks", "--quiet"], cwd=PROJECT)

    # fit VO to video length and mux
    out = os.path.abspath(args.out)
    vdur2 = ffprobe_dur(silent)
    af = []
    ratio = vdur / vdur2 if vdur2 > 0 else 1.0
    if ratio > 1.02 or ratio < 0.98:  # nudge VO tempo to match the rendered length
        tempo = max(0.85, min(1.25, ratio))
        af = ["-filter:a", "atempo=%.3f" % tempo]
    vo_fit = os.path.join(work, "vo_fit.mp3")
    subprocess.check_call(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", vo_mp3] + af + ["-y", vo_fit])
    subprocess.check_call(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", silent, "-i", vo_fit,
                           "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                           "-shortest", "-movflags", "+faststart", "-y", out])
    log("OK ->", out, round(ffprobe_dur(out), 2), "s")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
