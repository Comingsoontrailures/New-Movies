import os, re, json, subprocess, html
from pathlib import Path

import feedparser
from google import genai

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "output"
DATA.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)

SEEN_FILE = DATA / "seen.json"
seen = set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()

# Public YouTube RSS feeds. These are established movie-trailer publishers/studios.
CHANNELS = {
    "Rotten Tomatoes Trailers": "UCi8e0iOVk1fEOogdfu4YgfA",
    "IGN Movie Trailers": "UCWJ5MfdQZ6jXbF5gYuSAf5Q",
    "Paramount Pictures": "UCF9imwPMSGz4Vq1NiTWCC7g",
    "Warner Bros. Pictures": "UCjmJDM5pRKbUlVIzDYYWb6g",
}

def clean(s):
    return html.unescape(re.sub(r"\s+", " ", s or "")).strip()

def candidates():
    found = []
    for source, channel_id in CHANNELS.items():
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        feed = feedparser.parse(url)
        for e in feed.entries[:15]:
            vid = e.get("yt_videoid") or e.get("id", "")
            title = clean(e.get("title", ""))
            link = e.get("link", "")
            published = e.get("published", "")
            summary = clean(e.get("summary", ""))
            if vid and title and vid not in seen:
                found.append({
                    "id": vid, "title": title, "link": link,
                    "published": published, "summary": summary, "source": source
                })
    found.sort(key=lambda x: x["published"], reverse=True)
    return found

def ai_package(item):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = f"""
You are writing an original 45-60 second faceless YouTube Short for the channel "widely awaken".

A new movie-trailer video was published by: {item['source']}
Video title: {item['title']}
Published: {item['published']}
Official video URL: {item['link']}
Publisher description: {item['summary']}

Create ORIGINAL commentary about the trailer. Do not copy trailer dialogue, reviews,
or article text. Do not invent cast, plot, release date, studio, or other facts.
If a fact cannot be established from the supplied information, phrase it as something
viewers should check in the official trailer.

Return JSON with exactly:
hook
narration
youtube_title
description
hashtags

The narration should be 100-140 spoken words, energetic but factual.
"""
    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    return json.loads(response.text)

def tts(text, wav_path):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model="gemini-3.8-flash-lite-tts",
        contents=[{
            "role": "user",
            "parts": [{
                "text": text,
                "speech_metadata": {
                    "style": "confident, conversational movie-news presenter; energetic but natural"
                }
            }]
        }],
        config={
            "response_modalities": ["AUDIO"],
            "speech_config": {"voice_config": {"voice": "Kore"}},
        },
    )
    data = response.candidates[0].content.parts[0].inline_data.data
    wav_path.write_bytes(data)

def make_cards(work, title, hook, narration, source):
    # Original graphics only. No full trailer footage is downloaded or republished.
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1080, 1920
    fonts = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    big = ImageFont.truetype(fonts[0], 76)
    small = ImageFont.truetype(fonts[1], 42)

    texts = [
        (hook, "NEW MOVIE TRAILER"),
        (title, "TRAILER UPDATE"),
        (narration[:180], "WHAT THE TRAILER SHOWS"),
        ("Watch the official trailer", source),
        ("widely awaken", "Follow for new movie-trailer updates"),
    ]

    paths = []
    for i, (main, sub) in enumerate(texts, 1):
        im = Image.new("RGB", (W, H), (12 + i*8, 12 + i*5, 18 + i*4))
        d = ImageDraw.Draw(im)
        y = 560
        words = main.split()
        lines, line = [], ""
        for word in words:
            test = (line + " " + word).strip()
            if d.textbbox((0, 0), test, font=big)[2] > W - 140:
                lines.append(line)
                line = word
            else:
                line = test
        if line:
            lines.append(line)
        for line in lines[:7]:
            d.text((70, y), line, font=big, fill="white")
            y += 100
        d.text((70, 1330), sub[:110], font=small, fill="white")
        p = work / f"{i:02d}.png"
        im.save(p)
        paths.append(p)
    return paths

def make_srt(narration, duration, path):
    words = narration.split()
    chunk_size = 7
    chunks = [" ".join(words[i:i+chunk_size]) for i in range(0, len(words), chunk_size)]
    if not chunks:
        return
    step = max(1.5, duration / len(chunks))
    def ts(sec):
        h = int(sec // 3600); sec -= h*3600
        m = int(sec // 60); sec -= m*60
        s = int(sec); ms = int((sec-s)*1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    lines = []
    for i, chunk in enumerate(chunks, 1):
        a = i-1
        b = min(duration, i*step)
        lines += [str(i), f"{ts(a)} --> {ts(b)}", chunk, ""]
    path.write_text("\n".join(lines), encoding="utf-8")

def main():
    if not os.environ.get("GEMINI_API_KEY"):
        raise SystemExit("Missing GEMINI_API_KEY GitHub secret.")

    items = candidates()
    if not items:
        print("No unseen trailer found.")
        return

    item = items[0]
    pkg = ai_package(item)
    slug = re.sub(r"[^a-z0-9]+", "-", item["title"].lower()).strip("-")[:70]
    work = OUT / slug
    work.mkdir(parents=True, exist_ok=True)

    wav = work / "voice.wav"
    tts(pkg["narration"], wav)

    cards = make_cards(work, item["title"], pkg["hook"], pkg["narration"], item["link"])

    concat = work / "concat.txt"
    concat.write_text(
        "\n".join([f"file '{p.name}'\nduration 9" for p in cards] + [f"file '{cards[-1].name}'"]),
        encoding="utf-8"
    )

    duration = float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(wav)
    ]).decode().strip())

    srt = work / "captions.srt"
    make_srt(pkg["narration"], duration, srt)

    out = OUT / f"{slug}.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat),
        "-i", str(wav),
        "-vf", f"subtitles={srt}:force_style='FontName=DejaVu Sans,FontSize=22,PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&,BorderStyle=1,Outline=2,Alignment=2,MarginV=120'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", "-movflags", "+faststart",
        str(out)
    ], check=True)

    (work / "metadata.json").write_text(json.dumps({
        **pkg,
        "source": item,
        "copyright_note": "Original commentary and graphics; no full trailer footage is republished."
    }, indent=2), encoding="utf-8")

    seen.add(item["id"])
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")
    print(f"Created: {out}")

if __name__ == "__main__":
    main()
