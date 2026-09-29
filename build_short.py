import os, json, re, html, textwrap, subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus

import feedparser
from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "output"
DATA.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)

SEEN_FILE = DATA / "seen.json"
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# Free, public Google News RSS searches. These are deliberately broad so the
# channel covers trailers across all movie genres instead of one niche.
QUERIES = [
    '"official trailer" movie when:2d',
    '"official teaser" movie when:2d',
    '"trailer" "movie" when:2d',
    '"teaser trailer" movie when:2d',
]

def clean_html(value):
    value = html.unescape(value or "")
    return re.sub(r"<[^>]+>", " ", value).replace("\xa0", " ").strip()

def load_seen():
    if not SEEN_FILE.exists():
        return []
    try:
        return json.loads(SEEN_FILE.read_text())
    except Exception:
        return []

def save_seen(items):
    SEEN_FILE.write_text(json.dumps(items[-500:], indent=2))

def fetch_candidates():
    found = {}
    for q in QUERIES:
        url = (
            "https://news.google.com/rss/search?q="
            + quote_plus(q)
            + "&hl=en-US&gl=US&ceid=US:en"
        )
        feed = feedparser.parse(url)
        for e in feed.entries:
            title = clean_html(getattr(e, "title", ""))
            link = getattr(e, "link", "")
            if not title or not link:
                continue

            # Keep items that look like actual movie-trailer announcements.
            low = title.lower()
            if not any(k in low for k in ("trailer", "teaser", "first look")):
                continue

            published = getattr(e, "published", "") or getattr(e, "updated", "")
            summary = clean_html(getattr(e, "summary", ""))
            key = getattr(e, "id", "") or link
            found[key] = {
                "id": key,
                "title": title,
                "link": link,
                "summary": summary[:2500],
                "published": published,
                "source": clean_html(getattr(getattr(e, "source", None), "title", "")),
            }

    # Newest first.
    return sorted(found.values(), key=lambda x: x.get("published", ""), reverse=True)

def choose_unseen(candidates, seen):
    seen_set = set(seen)
    for item in candidates:
        if item["id"] not in seen_set:
            return item
    return None

def make_package(item):
    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""
You are producing a 30-60 second faceless YouTube Short about a newly reported movie trailer.

SOURCE HEADLINE: {item['title']}
SOURCE: {item['source']}
SOURCE SUMMARY: {item['summary']}
SOURCE LINK: {item['link']}

Use Google Search to verify the movie title, genre, release timing, studio/distributor,
main cast, and what is actually known about this trailer. Do not invent details.
Write punchy but factual commentary for a general movie audience.

Return ONLY valid JSON with these keys:
movie_title, genre, release_info, hook, narration, youtube_title, description, hashtags

Requirements:
- narration: about 80-110 words, natural spoken English, no stage directions.
- hook: one short sentence.
- youtube_title: compelling but not misleading.
- description: 2-4 sentences and include the source link.
- hashtags: 5-8 hashtags.
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.7,
        ),
    )
    text = response.text.strip()
    text = re.sub(r"^```json\s*|\s*```$", "", text, flags=re.I)
    return json.loads(text)

def tts(text, out_wav):
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model="gemini-3.8-flash-lite-tts",
        contents=[{
            "role": "user",
            "parts": [{
                "text": text,
                "speech_metadata": {"style": "energetic, clear movie-news narrator"},
            }],
        }],
        config={
            "response_modalities": ["AUDIO"],
            "speech_config": {"voice_config": {"voice": "Kore"}},
        },
    )
    data = response.candidates[0].content.parts[0].inline_data.data
    out_wav.write_bytes(data)

def get_font(size):
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

def make_cards(pkg, card_dir):
    card_dir.mkdir(parents=True, exist_ok=True)
    title_font = get_font(92)
    body_font = get_font(54)
    small_font = get_font(36)

    cards = [
        ("NEW TRAILER", pkg["movie_title"], 4.0),
        (pkg["genre"].upper(), pkg["release_info"], 4.0),
        ("WHAT TO KNOW", pkg["hook"], 5.0),
        ("MORE MOVIE NEWS", "Follow for the next trailer drop.", 4.0),
    ]

    paths = []
    for i, (top, main, duration) in enumerate(cards):
        img = Image.new("RGB", (1080, 1920), (10, 12, 18))
        draw = ImageDraw.Draw(img)
        draw.text((70, 130), top, font=small_font, fill=(220, 220, 220))
        y = 600
        for line in textwrap.wrap(main, width=20):
            draw.text((70, y), line, font=title_font if i == 0 else body_font, fill=(255,255,255))
            y += 120 if i == 0 else 82
        path = card_dir / f"card_{i}.png"
        img.save(path)
        paths.append((path, duration))
    return paths

def render_video(pkg, narration, out_mp4):
    work = OUT / "work"
    cards = make_cards(pkg, work / "cards")
    wav = work / "voice.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    tts(narration, wav)

    concat = work / "concat.txt"
    with concat.open("w", encoding="utf-8") as f:
        for path, duration in cards:
            f.write(f"file '{path.as_posix()}'\n")
            f.write(f"duration {duration}\n")
        # ffmpeg concat demuxer requires the last file to be repeated.
        f.write(f"file '{cards[-1][0].as_posix()}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat),
        "-i", str(wav),
        "-vf", "scale=1080:1920,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest", str(out_mp4),
    ]
    subprocess.run(cmd, check=True)

def main():
    seen = load_seen()
    candidates = fetch_candidates()
    item = choose_unseen(candidates, seen)

    if not item:
        print(f"No unseen trailer found. Checked {len(candidates)} recent candidates.")
        return

    print(f"Selected trailer candidate: {item['title']}")
    pkg = make_package(item)

    narration = pkg["narration"]
    video = OUT / "movie_trailer_short.mp4"
    render_video(pkg, narration, video)

    metadata = {
        **item,
        **pkg,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "video_file": video.name,
    }
    (OUT / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    seen.append(item["id"])
    save_seen(seen)
    print(f"Created: {video}")
    print(f"Movie: {pkg['movie_title']}")

if __name__ == "__main__":
    main()
