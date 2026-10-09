"""Publish the next queued image to Instagram (runs in GitHub Actions, daily around 08:30 Paris time).

The queue/ batches are pushed here by tools/ig_post.py in Kristian's private albanianstats_agent
repo. Every item is an image of a post already live on X (@albanianstats), with the caption
written by tools/ig_export.py; nothing else is ever published from this repo.

Env: IG_TOKEN (Instagram User access token, secret), IG_USER_ID, optional FORCE=1 (ignore the
time window and the one-a-day guard), DRY_RUN=1.
"""
import json, os, sys, time
import urllib.parse, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
POSTED = ROOT / "posted.jsonl"
API = "https://graph.instagram.com/v23.0"
TZ = ZoneInfo("Europe/Paris")
REPO = os.environ.get("GITHUB_REPOSITORY", "namesurname95/albanianstats-ig")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")


def call(method, path, **params):
    data = urllib.parse.urlencode(params).encode()
    url = f"{API}/{path}"
    req = urllib.request.Request(url if method == "POST" else f"{url}?{data.decode()}",
                                 data=data if method == "POST" else None, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode()[:400]}") from None


def posted():
    if not POSTED.exists():
        return []
    return [json.loads(l) for l in POSTED.read_text(encoding="utf-8").splitlines() if l.strip()]


def next_item(done):
    for m in sorted(ROOT.glob("queue/*/manifest.json")):
        for it in sorted(json.loads(m.read_text(encoding="utf-8")), key=lambda x: x["order"]):
            if it["post_id"] not in done:
                it["rel"] = f"queue/{m.parent.name}/{it['image']}"
                it["batch"] = m.parent.name
                return it
    return None


def main():
    now = datetime.now(TZ)
    force = os.environ.get("FORCE") == "1"
    log = posted()
    if not force:
        if not (8 <= now.hour < 12):                       # two UTC crons cover summer and winter time
            print(f"{now:%H:%M} Paris: outside the 08:00-12:00 window, nothing to do")
            return
        if any(p["published"][:10] == now.date().isoformat() for p in log):
            print("already published today")
            return
    it = next_item({p["post_id"] for p in log})
    if not it:
        print("queue empty: nothing to publish")
        return
    url = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{it['rel']}"
    print(f"publishing {it['rel']} (X {it['x_date']})\n{it['caption']}")
    if os.environ.get("DRY_RUN") == "1":
        return
    tok, uid = os.environ["IG_TOKEN"], os.environ["IG_USER_ID"]
    cid = call("POST", f"{uid}/media", image_url=url, caption=it["caption"], access_token=tok)["id"]
    for _ in range(30):
        st = call("GET", cid, fields="status_code", access_token=tok).get("status_code")
        if st == "FINISHED":
            break
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {cid}: {st}")
        time.sleep(3)
    mid = call("POST", f"{uid}/media_publish", creation_id=cid, access_token=tok)["id"]
    link = call("GET", mid, fields="permalink", access_token=tok).get("permalink", "")
    with open(POSTED, "a", encoding="utf-8") as f:
        f.write(json.dumps({"post_id": it["post_id"], "x_date": it["x_date"], "batch": it["batch"],
                            "image": it["image"], "ig_media_id": mid, "permalink": link,
                            "published": now.isoformat(timespec="seconds")}) + "\n")
    print(f"published -> {link or mid}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
