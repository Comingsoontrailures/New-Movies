# New-Movies automation

This is a $0-oriented GitHub Actions starter for **widely awaken**.

It:
1. Checks public YouTube RSS feeds from movie-trailer publishers/studios.
2. Skips trailer videos already recorded in `data/seen.json`.
3. Uses Gemini for original commentary and YouTube metadata.
4. Uses Gemini 3.8 Flash-Lite TTS for narration.
5. Creates an original vertical MP4 with FFmpeg and burned-in captions.
6. Uploads the finished MP4 as a GitHub Actions artifact for your approval.

## One required secret

Add a GitHub Actions secret named:

`GEMINI_API_KEY`

The Gemini API currently lists free-tier pricing for Gemini 3.8 Flash and
Gemini 3.8 Flash-Lite TTS. See Google's current pricing documentation.

## Copyright approach

This starter does NOT download or republish full movie trailers. It makes
original commentary and graphics and links viewers to the official trailer.
Using copyrighted trailer excerpts would require a separate rights/fair-use
analysis and is not made automatically safe by an AI voice.

## Important limitation

The first version monitors a small set of public trailer feeds. More official
studio/trailer feeds can be added after the first run is working.
