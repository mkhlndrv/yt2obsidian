#!/usr/bin/env python3
"""
yt2obsidian — a personal Telegram bot that turns YouTube videos into Obsidian notes.

Pipeline:  YouTube URL -> yt-dlp (audio) -> faster-whisper (transcript)
           -> Claude (structured note) -> <vault>/<title>.md

Everything lives in this one file. Configuration comes from .env (see .env.example).
The note template and the Claude prompts are the constants right below the imports.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import anthropic
import av  # bundled with faster-whisper; used to read frame thumbnails
import httpx  # bundled with python-telegram-bot; used for the optional transcription API
import numpy as np
import yt_dlp
from dotenv import load_dotenv
from faster_whisper import WhisperModel
from pydantic import BaseModel
from telegram import Update
from telegram.error import NetworkError, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

# =============================================================================
# EDIT ME: note template
# -----------------------------------------------------------------------------
# The bot fills these placeholders itself from video metadata (never invented):
#   {title} {url} {channel} {published} {date} {duration}
# Every other {placeholder} is filled in by Claude. {timestamp link} placeholders
# become Markdown links to the moment in the video (copied from the transcript).
# Keep frontmatter keys you rely on in Obsidian (Dataview, Properties) stable here.
# The full timestamped transcript is appended in a folded section by the bot
# itself (INCLUDE_TRANSCRIPT), so the template does not need it.
# =============================================================================

NOTE_TEMPLATE = """\
---
title: "{title}"
source: {url}
channel: "{channel}"
published: {published}
date: {date}
duration: "{duration}"
type: video-note
tags:
  - youtube
  - {topic-tag}
  - {topic-tag}
  - {topic-tag}
---

# {title}

> [!summary]
> {2-4 sentences: what the video is about, who it is for, and its central claim or takeaway.}

## Key Takeaways
- {3-8 bullets: the ideas worth remembering if you never rewatch the video. Where the video gives advice, phrase the takeaway as the concrete thing to do. Each bullet is a complete, specific statement.}

## Notes
### {Topic of this section: one concept, step, component, or argument} ({timestamp link copied from the transcript where this topic is best explained, e.g. [3:12](https://youtu.be/ID?t=192)})
- **{Key point in a few words}** — {what it means or why it matters, one or two lines}
  - {Supporting detail, example, number, definition, or on-screen content (code in a fenced block)}
- **{Next key point}** — {...}

### {Next topic} ({timestamp link})
- **{Key point}** — {...}

## Mentioned
- {[[Tool, person, book, site, or company]] worth following up on, with a few words on why it came up. One per line. Sponsors excluded.}

## Quotes
> "{Verbatim quote from the transcript.}" ({timestamp link})

## Related
- [[{Broader topic or concept note this connects to, not already linked above}]]
- [[{Another one}]]

## Source
- Video: {url}
- Channel: [[{channel}]]
"""

# =============================================================================
# EDIT ME: Claude prompts
# =============================================================================

SYSTEM_PROMPT = """\
You turn raw YouTube transcripts into clean, well-structured Obsidian notes for a personal \
knowledge vault.

Rules:
- Output ONLY the finished Markdown note. No preamble, no commentary, no code fences.
- Follow the NOTE TEMPLATE exactly: same frontmatter keys, same section order and headings. \
The frontmatter values already filled in must stay unchanged; only replace the {placeholders} \
and remove the braces.
- Tags: replace the {topic-tag} placeholders with 3-6 lowercase kebab-case tags specific to \
the content (e.g. machine-learning, personal-finance, rust, interview). Keep the `youtube` tag.
- Wikilinks: wrap in [[double brackets]], with their usual capitalization (e.g. \
[[Andrej Karpathy]], [[Neal.fun]]), the entities a reader would plausibly want a note about: \
people central to the topic, tools, products, books, papers, and named concepts. Link each \
the first time it appears in the body, using the plain everyday name as the link target \
([[Rust]], not [[Rust (programming language)|Rust]]); no disambiguation suffixes or aliases. \
Do not link generic words (AI, code, project, \
bootcamp), companies or brands mentioned only as examples, things named in passing or in a \
joke, or ubiquitous tools (GitHub, Google) unless the video is about them. Related lists 2-5 \
broader topic notes \
a personal vault would plausibly have (e.g. [[Learning to Code]], [[Technical Interviews]]) \
that are NOT already linked elsewhere in the note.
- Timestamps: the transcript is split into paragraphs, each starting with a Markdown \
timestamp link like [3:12](https://youtu.be/ID?t=192). Wherever the template shows \
{timestamp link}, copy the nearest such link exactly, as a Markdown link including the URL. \
Never invent or recompute a timestamp. If the uploader provided chapters, prefer their \
boundaries and names for the Notes sections.
- Sponsors and self-promotion: leave out sponsor segments, ads, merch, giveaways, and \
"like and subscribe" entirely, whether they come from the speech or from the screenshots. \
Do not mention, quote, or link the sponsor anywhere in the note, not even to note that a \
sponsor exists, and do not turn sponsor claims into takeaways or sections.
- The transcript is speech-recognition output: fix obviously mis-heard names and terms when \
context makes the intended word clear (e.g. "O of n squared" -> "O(n²)"), including inside \
quotes; drop filler words.
- Faithfulness: never invent facts, numbers, or quotes that are not in the transcript, and \
do not pad thin sections with outside knowledge.
- On-screen content: use the FRAMES (when provided) to capture code, slides, diagrams, \
charts, tables, and UI that carry the point; transcribe short code and key slide text into \
the note (code in fenced blocks) and summarise longer material. When the speaker refers to \
something on screen ("as you can see", "this code", "this chart") that neither the frames \
nor the speech capture, say so explicitly with the timestamp link instead of glossing over it.
- Speakers: for interviews, panels, podcasts, and inserted clips, attribute claims to whoever \
is speaking when the transcript makes it clear (by name, or as host, guest, or interviewer), \
and note when the video cuts to a clip of someone else.
- Specifics: keep every specific number, figure, date, and named example the video states; \
those are what a reader cannot recover without watching.
- Key Takeaways are conclusions: the insights, claims, and advice worth remembering, with \
advice phrased as the concrete thing to do. Notes are the detail behind them: the reasoning, \
examples, numbers, and steps. Do not copy a takeaway sentence into Notes; there, write what \
supports or elaborates it.
- Notes are organised by topic, not by time: gather everything the video says about one \
concept, step, component, or argument under one heading named for that topic, even when it \
was scattered across the video. Pick the structure that fits the content: steps for a how-to, \
concepts for an explainer, claims with their evidence for an argument, themes for a \
conversation. Aim for 3-8 sections; merge thin ones.
- Inside a section, lead each bullet with the key point in bold, then a short explanation; \
nest supporting details, examples, numbers, and definitions under the point they support. \
One idea per bullet, no walls of text, no chronological play-by-play.
- Timestamp links belong on section headings (the moment the topic is best explained) and on \
quotes. Do not prefix ordinary bullets with timestamps; add one to a bullet only when it \
points at something specific on screen (a demo, a chart, code).
- Mentioned lists only things worth following up on later (a tool to try, a person to look \
up, a book to read), with a few words on why. Skip incidental name-drops.
- Quotes: 1-3 lines that are memorable or capture the argument, verbatim apart from \
speech-recognition fixes.
- Length follows content density, not video length: keep every idea, example, number, and \
step you would want to find again; leave out repetition, filler, and anything you would never \
look up. A dense hour-long lecture yields a long note; a short vlog yields a short one.
- Write in English, concise and information-dense, bullets over prose.
"""

USER_PROMPT = """\
NOTE TEMPLATE (fill every {{placeholder}}, keep everything else exactly as-is):

{template}

VIDEO METADATA:
- Title: {title}
- Channel: {channel}
- URL: {url}
- Published: {published}
- Duration: {duration}
- Chapters from the uploader:
{chapters}
- Description (truncated): {description}

TRANSCRIPT (paragraphs prefixed with timestamp links):
{transcript}

Write the complete note now.\
"""

# =============================================================================
# EDIT ME: screenshot selection prompt
# -----------------------------------------------------------------------------
# Before the note is written, Claude reads the timestamped transcript once and
# decides which moments are worth a screenshot. This is that call's prompt.
# =============================================================================

CUE_PROMPT = """\
You pick the moments of a YouTube video worth screenshotting so that someone who reads the \
notes instead of watching still sees what was on screen.

You get the transcript as segments, each prefixed with its start time in seconds. Return the \
moments where the speaker refers to, reads from, demonstrates, or walks through something shown \
on screen and seeing it would add information beyond the speech: code, terminal output, slides, \
diagrams, charts, tables, formulas, a website or app UI, a document, a product, a whiteboard.

Rules:
- Only moments where the screen carries information the words do not: readable text, numbers, \
code, a table, a chart with values, a diagram whose structure matters, a UI or document being \
walked through. Illustrations of what the narrator is saying (icons, logos, product shots, \
stock imagery, animated metaphors, a person talking) do not count, however relevant they look.
- Skip talking-head passages, jokes, memes, B-roll, stock footage, intros, outros, and sponsor \
or ad segments.
- Pick the second when the item is most likely fully visible: usually 1-3 seconds after the \
speaker starts referring to it. Estimate from the segment start times.
- One moment per distinct item. If the speaker walks through one screen for a while, pick at \
most one moment per 30 seconds of that walkthrough.
- At most {max_moments} moments in total; fewer is better than padding. If nothing on screen \
carries information, return an empty list.
- For each moment give a short reason (under 12 words) naming what is on screen.
"""

# =============================================================================
# Configuration (from .env / environment)
# =============================================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

# Every note is sent to you in Telegram as a .md file AND kept in this folder.
# Point it inside your Obsidian vault if the bot runs on a machine that has the vault;
# otherwise it is just a local archive (default: ./notes next to this file).
VAULT_PATH = Path(
    os.environ.get("OBSIDIAN_VAULT_PATH", "").strip() or Path(__file__).resolve().parent / "notes"
).expanduser()

# Optional: restrict the bot to these Telegram user ids (comma-separated). Empty = anyone.
ALLOWED_USER_IDS = {
    int(x) for x in re.split(r"[,\s;]+", os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")) if x
}

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
CLAUDE_MAX_TOKENS = 16000

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "distil-large-v3")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_CPU_THREADS = int(os.environ.get("WHISPER_CPU_THREADS", "0") or 0)  # 0 = library default
WHISPER_BEAM_SIZE = int(os.environ.get("WHISPER_BEAM_SIZE", "5") or 5)  # 1 = faster, a bit less accurate

# Transcription backend. "local" runs faster-whisper on this machine. "api" uploads the audio to an
# OpenAI-compatible speech-to-text endpoint (OpenAI whisper-1, or Groq whisper-large-v3-turbo), which
# transcribes a 10-minute video in well under a minute and lets the bot run on a tiny server.
TRANSCRIBER = os.environ.get("TRANSCRIBER", "local").strip().lower()
STT_API_KEY = os.environ.get("STT_API_KEY", "").strip()
STT_BASE_URL = os.environ.get("STT_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
STT_MODEL = os.environ.get("STT_MODEL", "whisper-1").strip()
STT_CHUNK_MINUTES = 20  # uploads are capped at 25 MB; 20 min of 32 kbps mono mp3 is about 5 MB

def _env_flag(name: str, default: str = "true") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


# Attach the finished .md file to the Telegram chat. Turn off when the vault syncs by itself.
SEND_NOTE_FILE = _env_flag("SEND_NOTE_FILE")

# Shell command to run after each note is written, with {path} replaced by the note's path.
# Typical use: upload the note to storage your vault syncs from, e.g.
#   aws s3 cp {path} s3://my-vault/YouTube/      (Remotely Save plugin on your devices, S3 backend)
#   rclone copy {path} dropbox:Vault/YouTube     (any rclone remote)
AFTER_NOTE_COMMAND = os.environ.get("AFTER_NOTE_COMMAND", "").strip()

# Append the full timestamped transcript to the note in a folded section (searchable in Obsidian).
INCLUDE_TRANSCRIPT = _env_flag("INCLUDE_TRANSCRIPT")
TRANSCRIPT_PARAGRAPH_WORDS = 90  # roughly how often a timestamp link appears in the transcript

# Screenshots: Claude reads the transcript once (CUE_PROMPT) and picks the moments where the
# screen carries information; frames from those moments go into the note-writing call.
INCLUDE_FRAMES = _env_flag("INCLUDE_FRAMES")
CUE_MODEL = os.environ.get("CUE_MODEL", CLAUDE_MODEL)  # model for the screenshot-selection pass
FRAMES_MAX = int(os.environ.get("FRAMES_MAX", "30") or 30)  # most screenshots sent to Claude per video
FRAMES_VIDEO_HEIGHT = int(os.environ.get("FRAMES_VIDEO_HEIGHT", "720") or 720)  # video stream to download
FRAME_MIN_GAP_SECONDS = 4.0  # cues closer together than this share one frame
FRAME_CHANGE_THRESHOLD = 12.0  # mean pixel difference (0-255) vs the last kept frame; lower = more frames

# Optional: refuse videos longer than this (0 = no limit) and a cookies file for yt-dlp.
MAX_VIDEO_MINUTES = int(os.environ.get("MAX_VIDEO_MINUTES", "0") or 0)
YTDLP_COOKIES_FILE = os.environ.get("YTDLP_COOKIES_FILE", "").strip()

log = logging.getLogger("yt2obsidian")


# =============================================================================
# Shared helpers
# =============================================================================


class PipelineError(Exception):
    """An expected failure with a message that is safe to show to the user."""


def format_timestamp(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def timestamp_link(video_id: str, seconds: float) -> str:
    """Markdown link that opens the video at that moment, e.g. [3:12](https://youtu.be/ID?t=192)."""
    return f"[{format_timestamp(seconds)}](https://youtu.be/{video_id}?t={int(seconds)})"


@dataclass
class VideoMeta:
    video_id: str
    url: str
    title: str
    channel: str
    published: str  # YYYY-MM-DD or "unknown"
    duration_seconds: int
    description: str
    chapters: list[tuple[float, str]] = field(default_factory=list)  # (start seconds, title)

    @property
    def duration_str(self) -> str:
        return format_timestamp(self.duration_seconds)


_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PATH_ID_RE = re.compile(r"^/(?:embed|shorts|live|v)/([A-Za-z0-9_-]{11})")


def extract_video_id(text: str) -> str | None:
    """Return the YouTube video id from the first YouTube link in *text*, else None."""
    for token in text.split():
        candidate = token.strip("<>()[]\"'.,")
        if "://" not in candidate:
            candidate = "https://" + candidate
        try:
            parsed = urlparse(candidate)
        except ValueError:
            continue
        host = (parsed.hostname or "").lower()
        host = re.sub(r"^(www|m)\.", "", host)

        vid = None
        if host in {"youtube.com", "music.youtube.com", "youtube-nocookie.com"}:
            if parsed.path == "/watch":
                vid = parse_qs(parsed.query).get("v", [None])[0]
            else:
                m = _PATH_ID_RE.match(parsed.path)
                vid = m.group(1) if m else None
        elif host == "youtu.be":
            vid = parsed.path.lstrip("/").split("/")[0]

        if vid and _VIDEO_ID_RE.match(vid):
            return vid
    return None


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


_FILENAME_BAD_RE = re.compile(r'[<>:"/\\|?*\[\]#^\x00-\x1f]')


def safe_filename(title: str, max_len: int = 120) -> str:
    """Turn a video title into a filename that is valid on Linux/macOS/Windows and in Obsidian."""
    name = _FILENAME_BAD_RE.sub("", title)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .")
    return name or "Untitled video"


def _yaml_safe(value: str) -> str:
    """Make a value safe to place inside a double-quoted YAML scalar."""
    return value.replace("\\", "/").replace('"', "'").replace("\n", " ").strip()


def _wikilink_safe(value: str) -> str:
    return re.sub(r"[\[\]|#^]", "", value).strip()


def render_template(meta: VideoMeta) -> str:
    """Fill the metadata placeholders in NOTE_TEMPLATE; Claude fills the rest."""
    fills = {
        "{title}": _yaml_safe(meta.title),
        "{url}": meta.url,
        "{channel}": _wikilink_safe(_yaml_safe(meta.channel)),
        "{published}": meta.published,
        "{date}": date.today().isoformat(),
        "{duration}": meta.duration_str,
    }
    text = NOTE_TEMPLATE
    for placeholder, value in fills.items():
        text = text.replace(placeholder, value)
    return text


# =============================================================================
# Step 1: download audio with yt-dlp
# =============================================================================


class _YtdlpLogger:
    """Route yt-dlp output into the standard logging module."""

    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug] "):
            log.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.error("yt-dlp: %s", msg)


def _clean_ytdlp_error(exc: Exception) -> str:
    msg = str(exc).strip()
    msg = re.sub(r"^ERROR:\s*", "", msg)
    msg = re.sub(r"^\[\w+\]\s*[\w-]+:\s*", "", msg)  # drop "[youtube] <id>: "
    return msg or exc.__class__.__name__


def download_audio(video_id: str, out_dir: Path) -> tuple[VideoMeta, Path]:
    """Blocking. Downloads the best audio-only stream into *out_dir* and returns (meta, path)."""
    url = canonical_url(video_id)
    rejected: dict[str, str] = {}

    def match_filter(info: dict, *_args, **_kwargs) -> str | None:
        if info.get("is_live"):
            reason = "This is a live stream; only finished videos are supported."
        elif MAX_VIDEO_MINUTES and (info.get("duration") or 0) > MAX_VIDEO_MINUTES * 60:
            reason = (
                f"Video is longer than the configured limit of {MAX_VIDEO_MINUTES} minutes "
                f"(MAX_VIDEO_MINUTES)."
            )
        else:
            return None
        rejected["reason"] = reason
        return reason

    opts: dict = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": str(out_dir / "audio.%(ext)s"),
        "noplaylist": True,
        "match_filter": match_filter,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "logger": _YtdlpLogger(),
    }
    if YTDLP_COOKIES_FILE:
        opts["cookiefile"] = YTDLP_COOKIES_FILE

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise PipelineError(f"Could not download the video: {_clean_ytdlp_error(exc)}") from exc
    except Exception as exc:  # yt-dlp raises a wide variety of things
        raise PipelineError(
            f"yt-dlp failed unexpectedly ({exc.__class__.__name__}): {exc}"
        ) from exc

    if "reason" in rejected:
        raise PipelineError(rejected["reason"])
    if not info:
        raise PipelineError("yt-dlp returned no information for this video.")

    downloads = info.get("requested_downloads") or []
    audio_path = Path(downloads[0]["filepath"]) if downloads and downloads[0].get("filepath") else None
    if audio_path is None or not audio_path.exists():
        candidates = sorted(p for p in out_dir.iterdir() if p.name.startswith("audio."))
        if not candidates:
            raise PipelineError("Download finished but no audio file was produced.")
        audio_path = candidates[0]

    upload_date = info.get("upload_date") or ""
    published = (
        f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
        if re.fullmatch(r"\d{8}", upload_date)
        else "unknown"
    )
    chapters = [
        (float(c["start_time"]), str(c["title"]).strip())
        for c in (info.get("chapters") or [])
        if c.get("start_time") is not None and c.get("title")
    ]
    meta = VideoMeta(
        video_id=video_id,
        url=info.get("webpage_url") or url,
        title=(info.get("title") or f"YouTube video {video_id}").strip(),
        channel=(info.get("channel") or info.get("uploader") or "Unknown channel").strip(),
        published=published,
        duration_seconds=int(info.get("duration") or 0),
        description=(info.get("description") or "").strip()[:500],
        chapters=chapters,
    )
    log.info("Downloaded %s (%s) -> %s", meta.title, meta.duration_str, audio_path.name)
    return meta, audio_path


def download_video_stream(video_id: str, out_dir: Path) -> Path | None:
    """Blocking. Downloads a video-only stream (<= FRAMES_VIDEO_HEIGHT) for screenshots; None on failure."""
    opts: dict = {
        "format": (
            f"bv*[height<={FRAMES_VIDEO_HEIGHT}][ext=mp4]/bv*[height<={FRAMES_VIDEO_HEIGHT}]/bv*"
        ),
        "outtmpl": str(out_dir / "video.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "logger": _YtdlpLogger(),
    }
    if YTDLP_COOKIES_FILE:
        opts["cookiefile"] = YTDLP_COOKIES_FILE
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(canonical_url(video_id), download=True)
        downloads = (info or {}).get("requested_downloads") or []
        path = Path(downloads[0]["filepath"]) if downloads and downloads[0].get("filepath") else None
        if path is None or not path.exists():
            path = next((p for p in sorted(out_dir.iterdir()) if p.name.startswith("video.")), None)
    except Exception as exc:  # screenshots are optional: never fail the note because of them
        log.warning("Video stream download failed; continuing without screenshots: %s", exc)
        return None
    if path is None:
        log.warning("Video stream download produced no file; continuing without screenshots.")
        return None
    log.info("Downloaded video stream for screenshots -> %s (%.0f MB)", path.name, path.stat().st_size / 1e6)
    return path


# =============================================================================
# Step 1b: sample screenshots from the video (code, slides, charts)
# =============================================================================


def _gray_thumbnail(image_path: Path) -> np.ndarray:
    """32x18 grayscale version of an image, used to tell whether the picture changed."""
    with av.open(str(image_path)) as container:
        frame = next(container.decode(video=0))
        return frame.reformat(width=32, height=18, format="gray").to_ndarray().astype(np.float32)


class ScreenMoment(BaseModel):
    seconds: float
    why: str


class ScreenMoments(BaseModel):
    moments: list[ScreenMoment]


Cue = tuple[float, str]  # (seconds, why it is worth a screenshot)
Frame = tuple[float, str, bytes]  # (seconds, why, jpeg bytes)


def normalize_cues(moments: list[Cue], duration_seconds: int) -> list[Cue]:
    """Sort, drop moments outside the video, merge ones closer than FRAME_MIN_GAP_SECONDS, cap at FRAMES_MAX."""
    cleaned = sorted(
        (
            (float(seconds), " ".join(why.split()))
            for seconds, why in moments
            if seconds is not None and 0 <= float(seconds) <= max(duration_seconds, 0)
        ),
        key=lambda cue: cue[0],  # stable: equal times keep the model's order
    )
    merged: list[Cue] = []
    for seconds, why in cleaned:
        if merged and seconds - merged[-1][0] < FRAME_MIN_GAP_SECONDS:
            continue
        merged.append((seconds, why))
    return merged[:FRAMES_MAX]


async def find_screen_cues(segments: list[Segment], meta: VideoMeta) -> list[Cue]:
    """Ask Claude which moments of the transcript are worth a screenshot. Empty list on any failure."""
    lines = "\n".join(f"{int(start)}: {text}" for start, _end, text in segments)
    prompt = (
        f"VIDEO: {meta.title} ({meta.duration_str}) by {meta.channel}\n"
        f"Description (truncated): {meta.description or '(none)'}\n\n"
        f"TRANSCRIPT SEGMENTS (start seconds: text):\n{lines}"
    )
    try:
        response = await get_anthropic().messages.parse(
            model=CUE_MODEL,
            max_tokens=4000,
            system=CUE_PROMPT.format(max_moments=FRAMES_MAX),
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}],
            output_format=ScreenMoments,
        )
        moments = response.parsed_output.moments if response.parsed_output else []
    except (anthropic.APIError, ValueError) as exc:  # screenshots are optional
        log.warning("Screenshot selection failed; continuing without screenshots: %s", exc)
        return []
    cues = normalize_cues([(m.seconds, m.why) for m in moments], meta.duration_seconds)
    log.info("Claude picked %d screenshot moments (%d proposed).", len(cues), len(moments))
    return cues


def pick_frames(candidates: list[tuple[float, str, np.ndarray, bytes]]) -> list[Frame]:
    """Keep frames that differ from the last kept one, then cap at FRAMES_MAX (most-changed first)."""
    kept: list[tuple[float, float, str, bytes]] = []
    previous: np.ndarray | None = None
    for seconds, why, thumb, data in candidates:
        score = float(np.abs(thumb - previous).mean()) if previous is not None else 255.0
        if score >= FRAME_CHANGE_THRESHOLD:
            kept.append((seconds, score, why, data))
            previous = thumb
    if len(kept) > FRAMES_MAX:
        kept = sorted(sorted(kept, key=lambda k: -k[1])[:FRAMES_MAX])
    return [(seconds, why, data) for seconds, _score, why, data in kept]


def extract_frames(video_path: Path, meta: VideoMeta, out_dir: Path, cues: list[Cue]) -> list[Frame]:
    """Blocking. Returns (seconds, why, jpeg bytes) for the screenshots at the given moments, deduplicated."""
    candidates: list[tuple[float, str, np.ndarray, bytes]] = []
    for seconds, why in cues:
        jpg = out_dir / f"frame_{int(seconds)}.jpg"
        result = subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-y", "-ss", f"{seconds:.2f}", "-i", str(video_path),
                "-frames:v", "1", "-vf", "scale='min(1024,iw)':-2", "-q:v", "4", str(jpg),
            ],
            capture_output=True, timeout=120,
        )
        if result.returncode != 0 or not jpg.exists():
            log.debug("ffmpeg could not extract a frame at %.0fs: %s", seconds, result.stderr.decode(errors="replace"))
            continue
        try:
            candidates.append((seconds, why, _gray_thumbnail(jpg), jpg.read_bytes()))
        except Exception as exc:  # unreadable frame: skip it
            log.debug("Skipping frame at %.0fs: %s", seconds, exc)
    frames = pick_frames(candidates)
    log.info("Extracted %d frames at the chosen moments, kept %d screenshots.", len(candidates), len(frames))
    return frames


# =============================================================================
# Step 2: transcribe with faster-whisper
# =============================================================================

_whisper_model: WhisperModel | None = None
_whisper_load_lock = threading.Lock()

# CPU transcription of two videos at once just makes both slower: run them one at a time.
TRANSCRIBE_LOCK = asyncio.Lock()


def get_whisper_model() -> WhisperModel:
    """Load the model once (downloads it on first use) and reuse it for every job."""
    global _whisper_model
    with _whisper_load_lock:
        if _whisper_model is None:
            log.info(
                "Loading faster-whisper model %r (device=%s, compute_type=%s, cpu_threads=%s)...",
                WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE, WHISPER_CPU_THREADS or "default",
            )
            _whisper_model = WhisperModel(
                WHISPER_MODEL,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
                cpu_threads=WHISPER_CPU_THREADS,
            )
            log.info("Whisper model ready.")
        return _whisper_model


_TIMESTAMP_LINK_RE = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?\]\(https?://\S+?\)")


Segment = tuple[float, float, str]  # (start seconds, end seconds, text)


def format_transcript(segments: list[Segment], video_id: str) -> str:
    """Group segments into ~90-word paragraphs, each prefixed with a timestamp link."""
    paragraphs: list[str] = []
    current: list[str] = []
    start: float | None = None
    words = 0
    for seg_start, _seg_end, text in segments:
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if start is None:
            start = seg_start
        current.append(text)
        words += len(text.split())
        if words >= TRANSCRIPT_PARAGRAPH_WORDS and text[-1] in ".!?":
            paragraphs.append(f"{timestamp_link(video_id, start)} {' '.join(current)}")
            current, start, words = [], None, 0
    if current:
        paragraphs.append(f"{timestamp_link(video_id, start or 0)} {' '.join(current)}")
    return "\n\n".join(paragraphs)


def transcript_word_count(transcript: str) -> int:
    return len(_TIMESTAMP_LINK_RE.sub("", transcript).split())


def transcribe_audio_local(audio_path: Path) -> list[Segment]:
    """Blocking. faster-whisper on this machine."""
    model = get_whisper_model()
    try:
        raw_segments, _info = model.transcribe(
            str(audio_path),
            language="en",  # distil-large-v3 is English-only
            beam_size=WHISPER_BEAM_SIZE,
            vad_filter=True,  # skip silence; faster and fewer hallucinated segments
            condition_on_previous_text=False,  # avoids repetition loops on long audio
        )
        # `raw_segments` is a lazy generator: transcription happens as it is consumed.
        return [(seg.start, seg.end, seg.text.strip()) for seg in raw_segments if seg.text.strip()]
    except Exception as exc:
        raise PipelineError(f"Transcription failed ({exc.__class__.__name__}): {exc}") from exc


def _split_audio_for_api(audio_path: Path, out_dir: Path) -> list[Path]:
    """Re-encode the audio into small mono mp3 chunks that fit the API upload limit."""
    out_dir.mkdir(exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y", "-i", str(audio_path), "-vn", "-ac", "1", "-ar", "16000",
            "-b:a", "32k", "-f", "segment", "-segment_time", str(STT_CHUNK_MINUTES * 60),
            "-reset_timestamps", "1", str(out_dir / "chunk_%03d.mp3"),
        ],
        capture_output=True, timeout=1800,
    )
    if result.returncode != 0:
        raise PipelineError(
            "ffmpeg could not prepare the audio for the transcription API: "
            + result.stderr.decode(errors="replace").strip()[:300]
        )
    return sorted(out_dir.glob("chunk_*.mp3"))


def _post_transcription(chunk: Path) -> dict:
    """One request to the OpenAI-compatible /audio/transcriptions endpoint; returns the verbose JSON."""
    with chunk.open("rb") as fh:
        response = httpx.post(
            f"{STT_BASE_URL}/audio/transcriptions",
            headers={"Authorization": f"Bearer {STT_API_KEY}"},
            files={"file": (chunk.name, fh, "audio/mpeg")},
            data={"model": STT_MODEL, "response_format": "verbose_json", "language": "en"},
            timeout=600,
        )
    if response.status_code != 200:
        raise PipelineError(f"Transcription API error {response.status_code}: {response.text[:300]}")
    return response.json()


def transcribe_audio_api(audio_path: Path) -> list[Segment]:
    """Blocking. Uploads the audio chunk by chunk to the transcription API and stitches the timestamps."""
    segments: list[Segment] = []
    offset = 0.0
    for chunk in _split_audio_for_api(audio_path, audio_path.parent / "chunks"):
        try:
            payload = _post_transcription(chunk)
        except httpx.HTTPError as exc:
            raise PipelineError(f"Could not reach the transcription API: {exc}") from exc
        chunk_segments = payload.get("segments") or []
        duration = float(payload.get("duration") or STT_CHUNK_MINUTES * 60)
        if not chunk_segments and payload.get("text", "").strip():  # provider returned plain text only
            chunk_segments = [{"start": 0.0, "end": duration, "text": payload["text"]}]
        for seg in chunk_segments:
            text = str(seg.get("text", "")).strip()
            if text:
                segments.append((offset + float(seg["start"]), offset + float(seg["end"]), text))
        offset += duration
    return segments


def transcribe_audio(audio_path: Path, video_id: str) -> list[Segment]:
    """Blocking. Returns the English transcript as (start, end, text) segments, via the configured backend."""
    segments = transcribe_audio_api(audio_path) if TRANSCRIBER == "api" else transcribe_audio_local(audio_path)
    words = sum(len(text.split()) for _, _, text in segments)
    if not words:
        raise PipelineError("Transcription produced no text. Is there English speech in the video?")
    log.info("Transcribed %.0fs of audio into %d words (%s).", segments[-1][1], words, TRANSCRIBER)
    return segments


# =============================================================================
# Step 3: structure the transcript into a note with Claude
# =============================================================================

_anthropic_client: anthropic.AsyncAnthropic | None = None


def get_anthropic() -> anthropic.AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _anthropic_client


# A bare "5:15" / "1:02:05" that is not already the text of a Markdown link.
_BARE_TIMESTAMP_RE = re.compile(r"(?<![\[\w/:.])(\d{1,2}:\d{2}(?::\d{2})?)(?![\]\w/:.])")


def _link_bare_timestamps(body: str, meta: VideoMeta) -> str:
    """Claude occasionally writes '(5:15)' instead of the link; turn those into links deterministically."""

    def repl(match: re.Match) -> str:
        parts = [int(p) for p in match.group(1).split(":")]
        seconds = parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]
        if meta.duration_seconds and seconds > meta.duration_seconds + 5:
            return match.group(0)  # not a timestamp of this video (e.g. a "16:9" ratio)
        return timestamp_link(meta.video_id, seconds)

    return _BARE_TIMESTAMP_RE.sub(repl, body)


def _clean_note(text: str, meta: VideoMeta) -> str:
    """Strip an accidental code fence, link bare timestamps, and make sure the note starts with frontmatter."""
    text = text.strip()
    fenced = re.fullmatch(r"```[a-zA-Z]*\n(.*?)\n```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    # Claude occasionally drops the opening frontmatter fence ("title: ..." first, "---" later).
    if not text.startswith("---") and re.match(r"^\w+:", text) and "\n---\n" in text:
        text = "---\n" + text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            head, body = text[: end + 4], text[end + 4 :]
            text = head + _link_bare_timestamps(body, meta)
    else:
        text = _link_bare_timestamps(text, meta)
    if not text.startswith("---"):
        log.warning("Claude's note had no frontmatter; prepending a minimal one.")
        frontmatter = "\n".join([
            "---",
            f'title: "{_yaml_safe(meta.title)}"',
            f"source: {meta.url}",
            f"date: {date.today().isoformat()}",
            "tags:",
            "  - youtube",
            "---",
            "",
        ])
        text = frontmatter + text
    return text.rstrip() + "\n"


def append_transcript(note: str, transcript: str) -> str:
    """Add the full timestamped transcript as a folded callout at the end of the note."""
    quoted = "\n>\n".join("> " + paragraph for paragraph in transcript.split("\n\n"))
    return (
        note.rstrip()
        + "\n\n## Transcript\n> [!quote]- Full transcript (speech recognition, may contain errors)\n"
        + quoted
        + "\n"
    )


FRAMES_INTRO = (
    "FRAMES: screenshots of the moments a first pass judged to show something informative on "
    "screen, each labelled with the timestamp link of that moment and the reason it was picked. "
    "Use them only for informative on-screen content (code, slides, diagrams, charts, tables, UI, "
    "on-screen text) that the speech does not already convey. Ignore the presenter, backgrounds, "
    "memes, stock footage, B-roll, and any sponsor or ad screens; those never belong in the note."
)


async def generate_note(meta: VideoMeta, transcript: str, frames: list[Frame] = ()) -> str:
    """Ask Claude to turn the transcript (plus optional screenshots) into a note following NOTE_TEMPLATE."""
    chapters = "\n".join(f"- {timestamp_link(meta.video_id, start)} {title}" for start, title in meta.chapters)
    prompt = USER_PROMPT.format(
        template=render_template(meta),
        title=meta.title,
        channel=meta.channel,
        url=meta.url,
        published=meta.published,
        duration=meta.duration_str,
        chapters=chapters or "(none provided)",
        description=meta.description or "(none)",
        transcript=transcript,
    )
    content: list[dict] = []
    if frames:
        content.append({"type": "text", "text": FRAMES_INTRO})
        for seconds, why, data in frames:
            content.append({"type": "text", "text": f"Frame at {timestamp_link(meta.video_id, seconds)} ({why}):"})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(data).decode()},
            })
    content.append({"type": "text", "text": prompt})

    client = get_anthropic()
    try:
        # Streaming keeps the HTTP connection alive for long transcripts / long notes.
        async with client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": content}],
        ) as stream:
            message = await stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise PipelineError("Anthropic rejected the API key. Check ANTHROPIC_API_KEY in .env.") from exc
    except anthropic.NotFoundError as exc:
        raise PipelineError(f"Anthropic model {CLAUDE_MODEL!r} was not found. Check CLAUDE_MODEL.") from exc
    except anthropic.RateLimitError as exc:
        raise PipelineError("Anthropic API rate limit hit. Wait a minute and send the link again.") from exc
    except anthropic.APIStatusError as exc:
        raise PipelineError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise PipelineError(f"Could not reach the Anthropic API: {exc}") from exc

    if message.stop_reason == "refusal":
        raise PipelineError("Claude declined to process this transcript (safety refusal).")

    text = "".join(block.text for block in message.content if block.type == "text")
    if not text.strip():
        raise PipelineError("Claude returned an empty response.")
    if message.stop_reason == "max_tokens":
        log.warning("Note was cut off at %d output tokens.", CLAUDE_MAX_TOKENS)
        text += "\n\n> [!warning] Note truncated: Claude hit the output token limit.\n"

    log.info(
        "Claude usage: %d input / %d output tokens (%s, %d screenshots).",
        message.usage.input_tokens, message.usage.output_tokens, CLAUDE_MODEL, len(frames),
    )
    return _clean_note(text, meta)


# =============================================================================
# Step 4: write the note into the vault
# =============================================================================


def write_note(note: str, meta: VideoMeta) -> Path:
    """Blocking. Writes the note under VAULT_PATH, never overwriting an existing file."""
    stem = safe_filename(meta.title)
    path = VAULT_PATH / f"{stem}.md"
    counter = 2
    while path.exists():
        path = VAULT_PATH / f"{stem} ({counter}).md"
        counter += 1
    try:
        path.write_text(note, encoding="utf-8")
    except OSError as exc:
        raise PipelineError(f"Could not write the note to {path}: {exc}") from exc
    log.info("Wrote note %s", path)
    return path


def run_after_note_command(path: Path) -> None:
    """Blocking. Runs AFTER_NOTE_COMMAND for a freshly written note; raises PipelineError on failure."""
    # shell=True is intentional: the command is the operator's own .env setting (pipes and redirects
    # allowed), and the only value substituted into it is the note path, quoted with shlex.quote so a
    # video title can never inject shell syntax.
    command = AFTER_NOTE_COMMAND.replace("{path}", shlex.quote(str(path)))
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(f"AFTER_NOTE_COMMAND timed out after 600s: {command}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:300]
        raise PipelineError(f"AFTER_NOTE_COMMAND failed (exit {result.returncode}): {detail}")
    log.info("AFTER_NOTE_COMMAND ran for %s", path.name)


# =============================================================================
# Telegram bot
# =============================================================================


def _is_allowed(update: Update) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return update.effective_user is not None and update.effective_user.id in ALLOWED_USER_IDS


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.effective_message.reply_text(
        "Send me a YouTube link and I'll transcribe it and send you back a structured "
        f"Obsidian note as a .md file.\n\nYour Telegram user id is {user.id if user else 'unknown'} "
        "(use it for TELEGRAM_ALLOWED_USER_IDS in .env)."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    if not _is_allowed(update):
        uid = update.effective_user.id if update.effective_user else "?"
        log.warning("Ignoring message from unauthorized user id %s", uid)
        await message.reply_text("Sorry, this bot is private.")
        return

    video_id = extract_video_id(message.text or "")
    if not video_id:
        await message.reply_text(
            "That doesn't look like a YouTube link. Send a youtube.com or youtu.be video URL."
        )
        return

    await message.reply_text(
        "Processing started: downloading audio, then transcribing. "
        "Long videos can take a while; I'll message you at each step."
    )
    # Hand the heavy work to a background task so the handler (and the bot) stays responsive.
    context.application.create_task(
        process_video(video_id, message.chat_id, context), update=update
    )


async def process_video(video_id: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    async def say(text: str) -> None:
        await context.bot.send_message(chat_id=chat_id, text=text)

    log.info("Job started for video %s", video_id)
    try:
        with tempfile.TemporaryDirectory(prefix="yt2obsidian-") as tmp:
            meta, audio_path = await asyncio.to_thread(download_audio, video_id, Path(tmp))
            await say(f"Downloaded “{meta.title}” ({meta.duration_str}). Transcribing…")

            if TRANSCRIBE_LOCK.locked():
                await say("Another video is still being transcribed; yours is queued behind it.")
            async with TRANSCRIBE_LOCK:
                segments = await asyncio.to_thread(transcribe_audio, audio_path, meta.video_id)
            transcript = format_transcript(segments, meta.video_id)

            # Screenshots only at the moments Claude picks; skip the video download when there are none.
            frames: list[Frame] = []
            cues = await find_screen_cues(segments, meta) if INCLUDE_FRAMES else []
            if cues:
                video_path = await asyncio.to_thread(download_video_stream, video_id, Path(tmp))
                if video_path is not None:
                    try:
                        frames = await asyncio.to_thread(extract_frames, video_path, meta, Path(tmp), cues)
                    except Exception as exc:  # screenshots are optional
                        log.warning("Screenshot extraction failed; continuing without: %s", exc)
        # Leaving the `with` block deletes the temp directory with the audio, video, and frames.

        shots = f" and {len(frames)} screenshot{'s' if len(frames) != 1 else ''}" if frames else ""
        await say(f"Transcribed {transcript_word_count(transcript):,} words{shots}. Asking Claude to write the note…")
        note = await generate_note(meta, transcript, frames)
        if INCLUDE_TRANSCRIPT:
            note = append_transcript(note, transcript)
        path = await asyncio.to_thread(write_note, note, meta)
        if AFTER_NOTE_COMMAND:
            try:
                await asyncio.to_thread(run_after_note_command, path)
            except PipelineError as exc:
                log.warning("%s", exc)
                await say(f"⚠️ {exc}")
        if not SEND_NOTE_FILE:
            await say(f"✅ Note saved: {path.stem}")
        else:
            try:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=path.read_bytes(),
                    filename=path.name,
                    caption=f"✅ {path.stem}\nSave this file into your Obsidian vault.",
                )
            except TelegramError as exc:
                log.warning("Could not send the note file to Telegram: %s", exc)
                await say(f"⚠️ The note was saved at {path}, but sending the file failed: {exc}")
        log.info("Job finished for video %s", video_id)

    except PipelineError as exc:
        log.warning("Job failed for video %s: %s", video_id, exc)
        await say(f"❌ {exc}")
    except Exception as exc:  # noqa: BLE001 - last resort: never fail silently
        log.exception("Unexpected error while processing video %s", video_id)
        await say(f"❌ Unexpected error ({exc.__class__.__name__}): {exc}")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Telegram transport hiccups get one log line; anything else gets a traceback and a reply."""
    err = context.error
    if isinstance(err, NetworkError):
        log.warning("Telegram network error (polling will retry): %s", err)
        return
    log.error("Unhandled error while processing an update", exc_info=err)
    message = getattr(update, "effective_message", None)
    if message is not None:
        try:
            await message.reply_text(f"❌ Unexpected error ({err.__class__.__name__}): {err}")
        except TelegramError:
            pass


def _check_config() -> None:
    problems = []
    if not TELEGRAM_BOT_TOKEN:
        problems.append("TELEGRAM_BOT_TOKEN is not set")
    if not ANTHROPIC_API_KEY:
        problems.append("ANTHROPIC_API_KEY is not set")
    if not VAULT_PATH.exists():
        if VAULT_PATH.parent.is_dir():
            VAULT_PATH.mkdir()
            log.info("Created notes folder %s", VAULT_PATH)
        else:
            problems.append(f"OBSIDIAN_VAULT_PATH does not exist: {VAULT_PATH}")
    elif not VAULT_PATH.is_dir():
        problems.append(f"OBSIDIAN_VAULT_PATH is not a directory: {VAULT_PATH}")
    if YTDLP_COOKIES_FILE and not Path(YTDLP_COOKIES_FILE).is_file():
        problems.append(f"YTDLP_COOKIES_FILE does not exist: {YTDLP_COOKIES_FILE}")
    if TRANSCRIBER not in {"local", "api"}:
        problems.append(f"TRANSCRIBER must be 'local' or 'api', not {TRANSCRIBER!r}")
    elif TRANSCRIBER == "api" and not STT_API_KEY:
        problems.append("TRANSCRIBER=api needs STT_API_KEY")
    if problems:
        for p in problems:
            log.error("Config error: %s", p)
        sys.exit("Fix the .env file (see .env.example) and start again.")
    if not ALLOWED_USER_IDS:
        log.warning("TELEGRAM_ALLOWED_USER_IDS is empty: anyone who finds the bot can use it.")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)

    _check_config()
    log.info("Notes will be written to %s", VAULT_PATH)
    if TRANSCRIBER == "local":
        get_whisper_model()  # load (and on first run download) the model before accepting jobs
    else:
        log.info("Transcription via API: %s at %s", STT_MODEL, STT_BASE_URL)

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        # Telegram's default 5s timeouts are tight on flaky networks; be patient.
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .build()
    )
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(on_error)

    log.info("Bot is running. Send it a YouTube link.")
    # bootstrap_retries=-1: keep retrying the first Telegram call instead of dying
    # when the network is briefly unavailable (e.g. right after boot).
    app.run_polling(allowed_updates=Update.ALL_TYPES, bootstrap_retries=-1)


if __name__ == "__main__":
    main()
