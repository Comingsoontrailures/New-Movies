import os
import re
import json
import html
import textwrap
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import requests
from PIL import Image, ImageDraw, ImageFont
from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
DATA = ROOT / "data"
OUT.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)

SEEN_FILE = DATA / "seen.json"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is not set.")

client = genai.Client(api_key=GEMINI_API_KEY)

# Broad Google News searches. These are intentionally broad so a new trailer
# does not disappear just because a publisher used different wording.
QUERIES = [
    '"official trailer" movie when:3d',
    '"official teaser" movie when:3d',
    '"new trailer" movie when:3d',
    '"first trailer" movie when:3d',
    '"movie trailer" "2026" when:3d',
    '"movie trailer" "2027" when:3d',
]

def clean_text(value):
    value = html.unescape(value or "")
    return re.sub(r"\s+", " ", value).strip()

def google_news_url(query):
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )

def fetch_candidates():
    headers = {"User-Agent": "Mozilla/5.0 (New-Movies trailer bot)"}
    candidates = []
    seen_urls = set()

    for query in QUERIES:
        url = google_news_url(query)
        try:
            r = requests.get(url, headers=headers, timeout=20)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception as exc:
            print(f"RSS warning for {query}: {exc}")
            continue

        for item in root.findall(".//item"):
            title = clean_text(item.findtext("title"))
            link = clean_text(item.findtext("link"))
            pub = clean_text(item.findtext("pubDate"))
            desc = clean_text(item.findtext("description"))

            if not title or not link or link in seen_urls:
                continue

            # Ignore obvious TV/episode/news-only stories. Keep broad movie genres.
            low = title.lower()
            if any(x in low for x in [
                "tv series", "tv show", "episode", "season premiere",
                "series finale", "television"
            ]):
                continue

            seen_urls.add(link)
            candidates.append({
                "title": title,
                "link": link,
                "published": pub,
                "description": desc,
            })

    # Newest-looking items first. Google News feeds generally put newest first;
    # retaining feed order avoids depending on locale-specific date parsing.
    return candidates

def load_seen():
    if not SEEN_FILE.exists():
        return set()
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        return set(data if isinstance(data, list) else [])
    except Exception:
        return set()

def save_seen(seen):
    SEEN_FILE.write_text(
        json.dumps(sorted(seen), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

def pick_unseen(candidates, seen):
    for c in candidates:
        key = c["link"]
        if key not in seen:
            return c
    return None

def research_movie(candidate):
    prompt = f"""
You are researching a newly reported movie trailer for a faceless YouTube Shorts channel.

Candidate news item:
TITLE: {candidate['title']}
URL: {candidate['link']}
PUBLISHED: {candidate['published']}
DESCRIPTION: {candidate['description'][:2500]}

Use current web information to verify this is about a MOVIE trailer or teaser.
Return ONLY valid JSON with these keys:
movie_title, genre, release_info, studio_or_distributor, cast,
trailer_status, hook, narration, youtube_title, description, hashtags

Requirements:
- If it is not a movie trailer/teaser, set trailer_status to "REJECT".
- Do not invent facts.
- narration should be about 75-110 words, original commentary, not a transcript.
- hook should be 1 punchy sentence.
- youtube_title should be under 90 characters.
- description should briefly explain what the movie is and what the trailer reveals.
- hashtags should be an array of 4-8 strings.
"""
    # Current Google documentation lists Gemini 3.5 Flash-Lite among the current
    # low-cost/high-throughput models. Google Search grounding is used for fresh
    # verification when available.
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.4,
        ),
    )
    text = response.text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)

def get_font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def make_cards(info):
    W, H = 1080, 1920
    font_big = get_font(78, True)
    font_mid = get_font(48, True)
    font_small = get_font(34, False)

    cards = [
        ("NEW MOVIE TRAILER", info["movie_title"], info["genre"]),
        (info["hook"], info["movie_title"], info["release_info"]),
        ("WHAT TO KNOW", info["narration"], info["studio_or_distributor"]),
    ]

    paths = []
    for i, (top, main, bottom) in enumerate(cards, 1):
        im = Image.new("RGB", (W, H), (18, 18, 22))
        d = ImageDraw.Draw(im)

        d.text((70, 120), "WIDELY AWAKEN", font=font_small, fill=(235, 235, 235))

        y = 330
        for block, font in [(top, font_mid), (main, font_big)]:
            wrapped = textwrap.wrap(str(block), width=22 if font == font_big else 32)
            for line in wrapped:
                d.text((70, y), line, font=font, fill=(255, 255, 255))
                y += font.size + 18
            y += 30

        wrapped = textwrap.wrap(str(bottom), width=34)
        y = 1450
        for line in wrapped[:5]:
            d.text((70, y), line, font=font_small, fill=(210, 210, 210))
            y += 48

        path = OUT / f"card_{i}.png"
        im.save(path)
        paths.append(path)
    return paths

def make_tts(text):
    response = client.models.generate_content(
        model="gemini-3.8-flash-lite-tts",
        contents=[{
            "role": "user",
            "parts": [{
                "text": text,
                "speech_metadata": {
                    "style": "confident, energetic movie-news narrator; natural pace"
                },
            }],
        }],
        config={
            "response_modalities": ["AUDIO"],
            "speech_config": {"voice_config": {"voice": "Kore"}},
        },
    )
    data = response.candidates[0].content.parts[0].inline_data.data
    wav = OUT / "voice.wav"
    wav.write_bytes(data)
    return wav

def render_video(cards, wav):
    # Each card is shown for an equal slice of the narration length.
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(wav)],
        capture_output=True, text=True, check=True
    )
    duration = max(float(probe.stdout.strip()), 8.0)
    per = duration / len(cards)

    inputs = []
    filters = []
    for idx, card in enumerate(cards):
        inputs += ["-loop", "1", "-t", str(per), "-i", str(card)]
        filters.append(f"[{idx}:v]scale=1080:1920,format=yuv420p[v{idx}]")
    filters.append("".join(f"[v{i}]" for i in range(len(cards))) +
                   f"concat=n={len(cards)}:v=1:a=0[outv]")

    cmd = ["ffmpeg", "-y", *inputs, "-i", str(wav),
           "-filter_complex", ";".join(filters),
           "-map", "[outv]", "-map", f"{len(cards)}:a",
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", str(OUT / "movie_trailer_short.mp4")]
    subprocess.run(cmd, check=True)

def main():
    candidates = fetch_candidates()
    print(f"Found {len(candidates)} recent trailer-news candidates.")

    seen = load_seen()
    candidate = pick_unseen(candidates, seen)

    if not candidate:
        print("No unseen trailer found.")
        print("Candidate titles checked:")
        for c in candidates[:10]:
            print(" -", c["title"])
        return

    print("Candidate:", candidate["title"])
    info = research_movie(candidate)

    if str(info.get("trailer_status", "")).upper() == "REJECT":
        seen.add(candidate["link"])
        save_seen(seen)
        print("Candidate rejected as not a movie trailer.")
        return

    wav = make_tts(info["narration"])
    cards = make_cards(info)
    render_video(cards, wav)

    metadata = {
        **info,
        "source_news_url": candidate["link"],
        "source_news_title": candidate["title"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (OUT / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    seen.add(candidate["link"])
    save_seen(seen)

    print("Created Short for:", info["movie_title"])
    print("Output:", OUT / "movie_trailer_short.mp4")

if __name__ == "__main__":
    main()
