"""Run several videos of different genres through the real pipeline and collect metrics.

Caches transcript segments and screenshots per video under cache/<tag>/ so that prompt
iterations only re-run the Claude note call:  python eval_batch.py            (full, uses cache)
                                              python eval_batch.py --notes-only tag1 tag2
"""
import os, sys, asyncio, tempfile, logging, time, json, re, shutil
from dataclasses import asdict
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
HERE = Path(__file__).parent
vault = HERE / "vault"; vault.mkdir(exist_ok=True)
cache_root = HERE / "cache"; cache_root.mkdir(exist_ok=True)
os.environ["OBSIDIAN_VAULT_PATH"] = str(vault)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING); logging.getLogger("httpx2").setLevel(logging.WARNING)
import bot

VIDEOS = [
    ("tech-code", "5C_HPTJg5ek"), ("crypto", "rYQgy8QDEBI"), ("economics", "R8VBRCs2jTU"),
    ("interview", "xzPuGf89vpI"), ("science", "5iPH-br_eJQ"), ("finance", "fvGLnthJDsg"),
    ("business", "vDXkpJw16os"), ("health", "Se151brgGSM"),
]
args = sys.argv[1:]
notes_only = "--notes-only" in args
only_tags = [a for a in args if not a.startswith("--")]
results = []


async def prepare(tag, vid, m):
    """Download, transcribe, pick cues, extract frames; cache everything under cache/<tag>/."""
    cdir = cache_root / tag
    if (cdir / "segments.json").exists() and (cdir / "frames.json").exists():
        return  # cached
    cdir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="eval-") as tmp:
        meta, audio = await asyncio.to_thread(bot.download_audio, vid, Path(tmp))
        t = time.time()
        segments = await asyncio.to_thread(bot.transcribe_audio, audio, meta.video_id)
        m["transcribe_s"] = round(time.time() - t)
        t = time.time()
        cues = await bot.find_screen_cues(segments, meta)
        frames = []
        if cues:
            video = await asyncio.to_thread(bot.download_video_stream, vid, Path(tmp))
            if video:
                frames = await asyncio.to_thread(bot.extract_frames, video, meta, Path(tmp), cues)
        m.update(cues=len(cues), frames_s=round(time.time() - t))
    (cdir / "meta.json").write_text(json.dumps(asdict(meta)))
    (cdir / "segments.json").write_text(json.dumps(segments))
    (cdir / "frames").mkdir(exist_ok=True)
    index = []
    for i, (seconds, why, data) in enumerate(frames):
        (cdir / "frames" / f"{i:02d}.jpg").write_bytes(data)
        index.append({"seconds": seconds, "why": why, "file": f"{i:02d}.jpg"})
    (cdir / "frames.json").write_text(json.dumps(index))


def load(tag):
    cdir = cache_root / tag
    meta = bot.VideoMeta(**json.loads((cdir / "meta.json").read_text()))
    meta.chapters = [tuple(c) for c in meta.chapters]
    segments = [tuple(s) for s in json.loads((cdir / "segments.json").read_text())]
    frames = [(f["seconds"], f["why"], (cdir / "frames" / f["file"]).read_bytes())
              for f in json.loads((cdir / "frames.json").read_text())]
    return meta, segments, frames


async def run_one(tag, vid):
    m = {"tag": tag, "video_id": vid}
    t0 = time.time()
    await prepare(tag, vid, m)
    meta, segments, frames = load(tag)
    transcript = bot.format_transcript(segments, meta.video_id)
    m.update(title=meta.title, channel=meta.channel, duration=meta.duration_seconds, chapters=len(meta.chapters),
             words=bot.transcript_word_count(transcript), frames=len(frames), cue_reasons=[w for _, w, _ in frames])
    t = time.time()
    note = await bot.generate_note(meta, transcript, frames)
    m["claude_s"] = round(time.time() - t)
    path = bot.write_note(bot.append_transcript(note, transcript), meta)
    body = note.split("\n---\n", 1)[-1] if note.startswith("---") else note
    notes_section = body.split("## Notes", 1)[-1].split("## Mentioned", 1)[0]
    m.update(
        note=path.name, body_words=len(body.split()),
        frontmatter_ok=note.startswith("---\ntitle:") and note.count("\n---\n") == 1,
        unfilled=[l[:80] for l in body.splitlines() if "{" in l and "}" in l],
        bare_timestamps=bot._BARE_TIMESTAMP_RE.findall(body),
        code_blocks=body.count("```") // 2,
        sections=len(re.findall(r"^### ", notes_section, re.M)),
        bullets_with_timestamps=len(re.findall(r"^\s*- \[\d", notes_section, re.M)),
        bold_lead_bullets=len(re.findall(r"^- \*\*", notes_section, re.M)),
        wikilinks=sorted(set(re.findall(r"\[\[([^\]]+)\]\]", body))),
        sponsor_word=len(re.findall(r"sponsor", body, re.I)),
        total_s=round(time.time() - t0),
    )
    return m


async def main():
    if not notes_only:
        bot.get_whisper_model()
    for tag, vid in VIDEOS:
        if only_tags and tag not in only_tags:
            continue
        try:
            m = await run_one(tag, vid)
        except Exception as exc:
            m = {"tag": tag, "video_id": vid, "error": f"{type(exc).__name__}: {exc}"}
            logging.exception("FAILED %s", tag)
        results.append(m)
        print("RESULT " + json.dumps(m), flush=True)
        (HERE / "results.json").write_text(json.dumps(results, indent=1))
    print("BATCH DONE", flush=True)

asyncio.run(main())
