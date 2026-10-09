"""Detect coordinated bot / astroturf comments on a YouTube video.

Typical campaign this catches: many fresh accounts post varied hype about the
same unknown name ("X has been the answer this whole time", "Please help us
oh X!") within minutes of each other, then like each other's comments so they
rise to the top of the "Top" sort.

Usage:
    export YOUTUBE_API_KEY=...            # YouTube Data API v3 key
    python bot_comments.py VIDEO_ID_OR_URL [--check-accounts] [--json]
    python bot_comments.py --file sample_comments.json

Only uses the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import median
from typing import Dict, Iterable, List, Optional

API_BASE = "https://www.googleapis.com/youtube/v3"

# Signal weights. A single weak signal (e.g. an auto-generated handle, which
# plenty of real people have) never reaches "likely bot" on its own.
WEIGHTS = {
    "campaign_term": 0.30,
    "burst": 0.20,
    "template": 0.25,
    "auto_handle": 0.10,
    "like_velocity": 0.15,
    "hype": 0.15,
    "new_account": 0.20,
}
LIKELY_BOT = 0.60
SUSPICIOUS = 0.35

# YouTube's auto-assigned handles look like "@Sameer-v2p1g": a name, a dash
# and a short random suffix mixing letters and digits.
AUTO_HANDLE_RE = re.compile(r"^@?[A-Za-z][\w.]*-([a-z0-9]{4,6})$")

HYPE_PATTERNS = [
    r"\bhelp us\b",
    r"\b(has been|is) the answer\b",
    r"\bcatch(ing)? on\b",
    r"\bworld order\b",
    r"\bonly trust\b",
    r"\bchanged my life\b",
    r"\bgame[- ]?changer\b",
    r"\bmajor changes\b",
    r"\bbehind all\b",
    r"\bdon'?t sleep on\b",
    r"\bwake up\b",
    r"\bto the moon\b",
    r"\b\d{2,}x\b",
    r"\bpassive income\b",
    r"\bfinancial freedom\b",
    r"\b(thank you|thanks|grateful)\b.{0,40}\b(mr|mrs|ms|sir|coach|expert)\b",
    r"\b(whatsapp|telegram)\b",
]
HYPE_RE = re.compile("|".join(HYPE_PATTERNS), re.IGNORECASE)

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’]+")

# Words that are capitalised often enough in comments (sentence starts,
# common nouns) to never count as a promoted "campaign term".
COMMON_WORDS = set("""
about above after again against all also always amazing and another answer
anyone anything are around awesome back bad because been before being best
better between big both bro but can cannot could day days did does doing done
dont down during each easy even ever every everyone everything example first
from full future gonna good great guys had has have having help here how however
idea important into its just keep know last learn learned learning let life like
little long look looks love made make makes making man many maybe might more
most much must need never new next nice nobody nothing now off once only other
our out over people please point pretty probably problem really right said same
says see seems should show since some someone something still stuff such sure
take than thank thanks that thats the their them then there these they thing
things think this those though through time times today together tool tools too
true trust try tutorial under until use used useful using very video videos
want was watch watching way well were what when where which while who why will
wild with without wonderful work works world would wow yeah year years yes yet
you your youre youtube hello great awesome agree finally actually literally
absolutely totally truly honestly seriously simply already almost enough whole
start started starting changes change major order catch distractions behind
""".split())


@dataclass
class Comment:
    id: str
    author: str
    text: str
    published_at: datetime
    like_count: int = 0
    author_channel_id: Optional[str] = None
    account_created_at: Optional[datetime] = None
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.score >= LIKELY_BOT:
            return "likely bot"
        if self.score >= SUSPICIOUS:
            return "suspicious"
        return "ok"


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_auto_handle(author: str) -> bool:
    m = AUTO_HANDLE_RE.match(author.strip())
    if not m:
        return False
    suffix = m.group(1)
    return any(c.isdigit() for c in suffix) and any(c.isalpha() for c in suffix)


def shingles(text: str, n: int = 3) -> set:
    norm = re.sub(r"[^a-z0-9 ]", "", text.lower())
    norm = re.sub(r"\s+", " ", norm).strip()
    return {norm[i:i + n] for i in range(max(len(norm) - n + 1, 1))}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def find_campaign_terms(comments: List[Comment], context: str = "",
                        min_authors: int = 3, min_share: float = 0.03) -> Dict[str, List[Comment]]:
    """Find uncommon, consistently capitalised names pushed by many accounts.

    Terms that appear in the video's own title/description/channel (passed in
    ``context``) are ignored: viewers naturally talk about what the video is about.
    """
    context_words = {w.lower() for w in WORD_RE.findall(context)}
    occurrences: Dict[str, List[str]] = defaultdict(list)
    by_term: Dict[str, Dict[str, Comment]] = defaultdict(dict)

    for c in comments:
        for word in WORD_RE.findall(c.text):
            key = re.sub(r"['’]s$", "", word.lower())
            if len(key) < 4 or key in COMMON_WORDS or key in context_words:
                continue
            occurrences[key].append(word)
            by_term[key].setdefault(c.author, c)

    authors = {c.author for c in comments}
    threshold = max(min_authors, int(len(authors) * min_share + 0.999))
    terms = {}
    for key, forms in occurrences.items():
        capitalised = sum(1 for f in forms if f[0].isupper()) / len(forms)
        if capitalised >= 0.8 and len(by_term[key]) >= threshold:
            terms[forms[0]] = list(by_term[key].values())
    return terms


def detect(comments: List[Comment], context: str = "", now: Optional[datetime] = None,
           burst_minutes: int = 30, manual_terms: Iterable[str] = ()) -> Dict[str, List[Comment]]:
    """Score every comment in place. Returns the detected campaign terms."""
    now = now or datetime.now(timezone.utc)

    window = burst_minutes * 60

    def burst_members(hits: List[Comment]) -> Dict[str, int]:
        """Comment id -> accounts posting about the term within the burst window."""
        members = {}
        for c in hits:
            near = [o for o in hits if abs((o.published_at - c.published_at).total_seconds()) <= window]
            if len(near) >= 3:
                members[c.id] = len(near)
        return members

    # A name many people mention over hours or days is just a popular topic
    # (e.g. "Raspberry Pi"); a campaign shows up as a burst of accounts.
    terms = {}
    for t, hits in find_campaign_terms(comments, context).items():
        if burst_members(hits):
            terms[t] = hits
    for t in manual_terms:
        hits = {c.author: c for c in comments if re.search(rf"\b{re.escape(t)}\b", c.text, re.I)}
        if hits:
            terms[t] = list(hits.values())

    def add(c: Comment, signal: str, reason: str) -> None:
        if reason not in c.reasons:
            c.score += WEIGHTS[signal]
            c.reasons.append(reason)

    # 1. Campaign terms, and bursts of them from different accounts.
    for term, hits in terms.items():
        bursts = burst_members(hits)
        for c in hits:
            add(c, "campaign_term", f'pushes "{term}" ({len(hits)} accounts mention it)')
            if c.id in bursts:
                add(c, "burst", f'{bursts[c.id]} accounts posted about "{term}" within {burst_minutes} min')

    # 2. Template comments: near-identical text from different accounts.
    sh = [shingles(c.text) for c in comments]
    for i, a in enumerate(comments):
        if len(a.text) < 20:
            continue
        for j in range(i + 1, len(comments)):
            b = comments[j]
            if a.author != b.author and len(b.text) >= 20 and jaccard(sh[i], sh[j]) >= 0.6:
                add(a, "template", "near-duplicate of another account's comment")
                add(b, "template", "near-duplicate of another account's comment")

    # 3. Likes arriving far faster than for the other comments.
    def rate(c: Comment) -> float:
        hours = max((now - c.published_at).total_seconds() / 3600, 0.25)
        return c.like_count / hours

    typical = median(rate(c) for c in comments) if comments else 0
    for c in comments:
        r = rate(c)
        if c.like_count >= 20 and r >= 10 * max(typical, 1.0):
            add(c, "like_velocity", f"{c.like_count} likes at {r:.0f}/hour (typical {typical:.1f}/hour)")

    # 4. Per-account and per-text signals.
    for c in comments:
        if is_auto_handle(c.author):
            add(c, "auto_handle", "auto-generated handle (name + random suffix)")
        if HYPE_RE.search(c.text):
            add(c, "hype", "scam-style hype phrasing")
        if c.account_created_at:
            age_days = (now - c.account_created_at).days
            if age_days < 30:
                add(c, "new_account", f"account is {age_days} days old")

    for c in comments:
        c.score = round(min(c.score, 1.0), 2)
    return terms


# --------------------------------------------------------------------------
# YouTube Data API v3
# --------------------------------------------------------------------------

def extract_video_id(value: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([\w-]{11})", value)
    return m.group(1) if m else value


def api_get(endpoint: str, params: dict, api_key: str) -> dict:
    params = {**params, "key": api_key}
    url = f"{API_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def _comment_from_snippet(cid: str, s: dict) -> Comment:
    return Comment(
        id=cid,
        author=s.get("authorDisplayName", ""),
        text=s.get("textOriginal") or s.get("textDisplay", ""),
        published_at=parse_time(s["publishedAt"]),
        like_count=int(s.get("likeCount", 0)),
        author_channel_id=(s.get("authorChannelId") or {}).get("value"),
    )


def fetch_comments(video_id: str, api_key: str, limit: int = 1000) -> List[Comment]:
    comments: List[Comment] = []
    token = None
    while len(comments) < limit:
        params = {"part": "snippet,replies", "videoId": video_id, "maxResults": 100,
                  "order": "time", "textFormat": "plainText"}
        if token:
            params["pageToken"] = token
        data = api_get("commentThreads", params, api_key)
        for item in data.get("items", []):
            top = item["snippet"]["topLevelComment"]
            comments.append(_comment_from_snippet(top["id"], top["snippet"]))
            for reply in (item.get("replies") or {}).get("comments", []):
                comments.append(_comment_from_snippet(reply["id"], reply["snippet"]))
        token = data.get("nextPageToken")
        if not token:
            break
    return comments[:limit]


def fetch_video_context(video_id: str, api_key: str) -> str:
    items = api_get("videos", {"part": "snippet", "id": video_id}, api_key).get("items", [])
    if not items:
        return ""
    s = items[0]["snippet"]
    return " ".join([s.get("title", ""), s.get("description", ""), s.get("channelTitle", ""),
                     " ".join(s.get("tags", []))])


def fetch_account_ages(comments: List[Comment], api_key: str) -> None:
    ids = sorted({c.author_channel_id for c in comments if c.author_channel_id})
    created = {}
    for i in range(0, len(ids), 50):
        data = api_get("channels", {"part": "snippet", "id": ",".join(ids[i:i + 50])}, api_key)
        for item in data.get("items", []):
            created[item["id"]] = parse_time(item["snippet"]["publishedAt"])
    for c in comments:
        c.account_created_at = created.get(c.author_channel_id)


def load_file(path: str):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    comments = [Comment(
        id=d.get("id", str(i)),
        author=d["author"],
        text=d["text"],
        published_at=parse_time(d["published_at"]),
        like_count=int(d.get("like_count", 0)),
        author_channel_id=d.get("author_channel_id"),
        account_created_at=parse_time(d["account_created_at"]) if d.get("account_created_at") else None,
    ) for i, d in enumerate(data["comments"])]
    now = parse_time(data["fetched_at"]) if data.get("fetched_at") else None
    return comments, data.get("video_context", ""), now


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Flag coordinated bot comments on a YouTube video.")
    p.add_argument("video", nargs="?", help="video ID or URL")
    p.add_argument("--file", help="analyse a saved JSON file instead of calling the API")
    p.add_argument("--limit", type=int, default=1000, help="max comments to fetch (default 1000)")
    p.add_argument("--check-accounts", action="store_true",
                   help="also look up account creation dates (1 extra API unit per 50 accounts)")
    p.add_argument("--term", action="append", default=[], help="name to always treat as promoted")
    p.add_argument("--burst-minutes", type=int, default=30)
    p.add_argument("--all", action="store_true", help="list ok comments too")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = p.parse_args(argv)

    if args.file:
        comments, context, now = load_file(args.file)
    elif args.video:
        api_key = os.environ.get("YOUTUBE_API_KEY")
        if not api_key:
            p.error("set YOUTUBE_API_KEY (or use --file)")
        vid = extract_video_id(args.video)
        comments = fetch_comments(vid, api_key, args.limit)
        context = fetch_video_context(vid, api_key)
        if args.check_accounts:
            fetch_account_ages(comments, api_key)
        now = None
    else:
        p.error("give a video ID/URL or --file")

    terms = detect(comments, context, now, args.burst_minutes, args.term)
    ranked = sorted(comments, key=lambda c: c.score, reverse=True)
    shown = ranked if args.all else [c for c in ranked if c.label != "ok"]

    if args.json:
        print(json.dumps({
            "total_comments": len(comments),
            "campaign_terms": {t: len(h) for t, h in terms.items()},
            "comments": [{"id": c.id, "author": c.author, "text": c.text, "likes": c.like_count,
                          "published_at": c.published_at.isoformat(), "score": c.score,
                          "label": c.label, "reasons": c.reasons} for c in shown],
        }, indent=2, ensure_ascii=False))
        return 0

    likely = sum(c.label == "likely bot" for c in comments)
    sus = sum(c.label == "suspicious" for c in comments)
    print(f"Analysed {len(comments)} comments: {likely} likely bot, {sus} suspicious.")
    if terms:
        print("Promoted names:", ", ".join(f'"{t}" ({len(h)} accounts)' for t, h in terms.items()))
    print()
    for c in shown:
        print(f"[{c.label.upper()} {c.score:.2f}] {c.author} · {c.like_count} likes")
        print(f"  {c.text[:160]}")
        for r in c.reasons:
            print(f"    - {r}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
