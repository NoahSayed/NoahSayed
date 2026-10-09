# YouTube bot comment detector

Flags coordinated bot / astroturf comments on a YouTube video: the kind where a
batch of fresh accounts all hype the same unknown name ("Claudixum has been the
answer this whole time", "Please help us oh Claudixum!") within minutes, then
like each other's comments up to the top of the **Top** sort.

Standard library only (Python 3.9+).

## Usage

```bash
# Get a free key: Google Cloud Console → enable "YouTube Data API v3" → Credentials → API key
export YOUTUBE_API_KEY=your-key

python bot_comments.py "https://www.youtube.com/watch?v=VIDEO_ID"
python bot_comments.py VIDEO_ID --check-accounts      # also check account ages
python bot_comments.py VIDEO_ID --json > report.json  # machine-readable
python bot_comments.py VIDEO_ID --term "Claudixum"    # always treat a name as promoted
python bot_comments.py --file sample_comments.json    # offline demo, no key needed
```

Example output (from `sample_comments.json`, modelled on a real campaign):

```
Analysed 12 comments: 5 likely bot, 0 suspicious.
Promoted names: "Claudixum" (5 accounts)

[LIKELY BOT 0.90] @Sameer-v2p1g · 84 likes
  I can only trust Claudixum at times like this. Please help us oh Claudixum!
    - pushes "Claudixum" (5 accounts mention it)
    - 5 accounts posted about "Claudixum" within 30 min
    - 84 likes at 252/hour (typical 2.8/hour)
    - auto-generated handle (name + random suffix)
    - scam-style hype phrasing
```

## Signals

| Signal | Weight | What it looks for |
|---|---|---|
| Promoted name | 0.30 | An uncommon, consistently capitalised name that 3+ accounts (and ≥3% of commenters) mention, that isn't in the video's title/description/tags, **and** shows up in a burst |
| Burst | 0.20 | 3+ accounts posting about that name within 30 min (`--burst-minutes`) |
| Template text | 0.25 | Near-identical comments from different accounts (character-trigram Jaccard ≥ 0.6) |
| Like velocity | 0.15 | ≥20 likes arriving 10× faster per hour than the video's typical comment |
| Hype phrasing | 0.15 | "help us", "has been the answer", "catching on", "world order", "don't sleep on", "passive income", Telegram/WhatsApp… |
| New account | 0.20 | Channel created < 30 days ago (only with `--check-accounts`) |
| Auto handle | 0.10 | YouTube's auto-assigned `@Name-x7k2m` style handle |

Score ≥ 0.60 → **likely bot**, ≥ 0.35 → **suspicious**. No single weak signal
reaches "likely bot" alone: many real people have auto-generated handles or
mention popular names.

## API quota

The default quota is 10,000 units/day. Each page of 100 comment threads costs 1 unit,
the video lookup costs 1 unit, and `--check-accounts` costs 1 unit per 50 accounts. A
1,000-comment video costs about 12 units.

## Limits

This is a heuristic triage tool. Treat its output as a list of comments to look at, not proof.
The API only returns up to 5 replies per thread, and YouTube may already have
hidden some spam. To act on the results, report the comments through YouTube
(**⋮ → Report → Spam or misleading**). If it's your own channel, remove them
in YouTube Studio or add the promoted name to **Settings → Community → Blocked words**.

## Tests

```bash
python -m unittest -v test_bot_comments
```
