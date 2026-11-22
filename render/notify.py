#!/usr/bin/env python3
"""POST the finished MP4 back to an n8n webhook (multipart form).

n8n then uploads to Drive and sends it to WhatsApp — the delivery logic
stays in n8n, this repo only renders. No-ops (exit 0) if N8N_CALLBACK_URL
is unset, so the workflow still succeeds and leaves the artifact.
"""
import os, sys, json, argparse

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--payload", help="reel payload json; its 'meta' object is forwarded")
    args = ap.parse_args()

    url = os.environ.get("N8N_CALLBACK_URL", "").strip()
    if not url:
        print("N8N_CALLBACK_URL not set — skipping callback (artifact still uploaded).")
        return

    meta = {}
    if args.payload and os.path.exists(args.payload):
        try:
            meta = (json.load(open(args.payload)) or {}).get("meta", {}) or {}
        except Exception as e:
            print("could not read meta:", repr(e)[:120])

    import requests
    with open(args.file, "rb") as fh:
        files = {"file": (os.path.basename(args.file), fh, "video/mp4")}
        data = {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in meta.items()}
        r = requests.post(url, files=files, data=data, timeout=180)
    print("callback:", r.status_code, r.text[:200])
    r.raise_for_status()


if __name__ == "__main__":
    main()
