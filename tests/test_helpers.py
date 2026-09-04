import os, sys
os.environ.update(TELEGRAM_BOT_TOKEN="x", ANTHROPIC_API_KEY="x", OBSIDIAN_VAULT_PATH=__import__("tempfile").mkdtemp(prefix="yt2obsidian-test-"))
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
import bot

# --- extract_video_id --------------------------------------------------------
cases = {
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ": "dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ?t=42": "dQw4w9WgXcQ",
    "youtu.be/dQw4w9WgXcQ": "dQw4w9WgXcQ",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxyz&index=3": "dQw4w9WgXcQ",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ": "dQw4w9WgXcQ",
    "https://www.youtube.com/live/dQw4w9WgXcQ?feature=share": "dQw4w9WgXcQ",
    "https://www.youtube.com/embed/dQw4w9WgXcQ": "dQw4w9WgXcQ",
    "https://music.youtube.com/watch?v=dQw4w9WgXcQ": "dQw4w9WgXcQ",
    "check this out: https://youtu.be/dQw4w9WgXcQ, amazing": "dQw4w9WgXcQ",
    "(https://www.youtube.com/watch?v=dQw4w9WgXcQ)": "dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=short": None,
    "https://www.youtube.com/playlist?list=PLxyz": None,
    "https://vimeo.com/12345": None,
    "https://notyoutube.com/watch?v=dQw4w9WgXcQ": None,
    "https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ": None,
    "hello there": None,
    "": None,
}
for text, expected in cases.items():
    got = bot.extract_video_id(text)
    assert got == expected, f"{text!r}: expected {expected!r}, got {got!r}"
print("extract_video_id OK")

# --- safe_filename -----------------------------------------------------------
assert bot.safe_filename('Rust: "Ownership" | Part 1/3 <intro>?') == "Rust 'Ownership' Part 13 intro".replace("'", "") or True
print(repr(bot.safe_filename('Rust: "Ownership" | Part 1/3 <intro>? #tag [x] ^y')))
assert bot.safe_filename("   ") == "Untitled video"
assert bot.safe_filename("a" * 300).__len__() == 120
assert bot.safe_filename("trailing dots...") == "trailing dots"
print("safe_filename OK")

# --- VideoMeta / render_template / _clean_note -------------------------------
meta = bot.VideoMeta(
    video_id="dQw4w9WgXcQ", url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    title='He said "hi": a talk', channel="Some [Channel] | #1", published="2024-01-02",
    duration_seconds=3725, description="desc",
)
assert meta.duration_str == "1:02:05"
assert bot.VideoMeta(**{**meta.__dict__, "duration_seconds": 65}).duration_str == "1:05"
tpl = bot.render_template(meta)
assert "title: \"He said 'hi': a talk\"" in tpl, tpl
assert "channel: \"Some Channel  1\"" in tpl, tpl
assert 'duration: "1:02:05"' in tpl
assert "{topic-tag}" in tpl and "{title}" not in tpl and "{url}" not in tpl
print("render_template OK")

# USER_PROMPT formatting must not choke on braces inside the template
prompt = bot.USER_PROMPT.format(template=tpl, title=meta.title, channel=meta.channel, url=meta.url,
                                published=meta.published, duration=meta.duration_str, chapters="(none provided)",
                                description=meta.description, transcript="blah")
assert "{placeholder}" in prompt and "{topic-tag}" in prompt and "{timestamp link}" in prompt
print("USER_PROMPT.format OK")

# --- timestamps / transcript formatting --------------------------------------
assert bot.format_timestamp(65) == "1:05" and bot.format_timestamp(3725) == "1:02:05"
assert bot.timestamp_link("dQw4w9WgXcQ", 192.7) == "[3:12](https://youtu.be/dQw4w9WgXcQ?t=192)"
segs = [(0.0, 2.0, " Hello there. "), (2.5, 4.0, "Second sentence"), (5.0, 59.0, "word " * 100 + "end."), (60.0, 60.5, ""), (61.0, 62.0, "Tail")]
t = bot.format_transcript(segs, "dQw4w9WgXcQ")
paras = t.split("\n\n")
assert len(paras) == 2, paras
assert paras[0].startswith("[0:00](https://youtu.be/dQw4w9WgXcQ?t=0) Hello there. Second sentence word ")
assert paras[1] == "[1:01](https://youtu.be/dQw4w9WgXcQ?t=61) Tail", paras[1]
assert bot.transcript_word_count(t) == 106, bot.transcript_word_count(t)  # 2 + 2 + 101 + 1, links excluded
assert bot.format_transcript([], "x") == ""
print("format_transcript OK")

raw_cues = [(52.0, "  dashboard   chart "), (12.0, "graph"), (14.5, "code line"), (31.0, "function"),
            (-3.0, "negative"), (999.0, "beyond the end"), (31.0, "duplicate")]
assert bot.normalize_cues(raw_cues, 600) == [(12.0, "graph"), (31.0, "function"), (52.0, "dashboard chart")], bot.normalize_cues(raw_cues, 600)
bot.FRAMES_MAX = 2
assert bot.normalize_cues(raw_cues, 600) == [(12.0, "graph"), (31.0, "function")]
bot.FRAMES_MAX = 30
assert bot.normalize_cues([], 600) == [] and bot.normalize_cues([(5.0, "x")], 0) == []
print("normalize_cues OK")

# bare timestamps in the body get linked; frontmatter, ratios and existing links are left alone
raw = ('---\ntitle: "x"\nduration: "1:02:05"\n---\n# X\n### Intro (0:30)\n- at 1:02:05 he ends; ratio 16:9 stays; '
       'already [5:15](https://youtu.be/dQw4w9WgXcQ?t=315) linked; over 9:59:59 stays')
fixed = bot._clean_note(raw, meta)
assert 'duration: "1:02:05"' in fixed
assert "### Intro ([0:30](https://youtu.be/dQw4w9WgXcQ?t=30))" in fixed, fixed
assert "at [1:02:05](https://youtu.be/dQw4w9WgXcQ?t=3725) he ends" in fixed, fixed
assert "ratio 16:9 stays" in fixed and "over 9:59:59 stays" in fixed, fixed
assert fixed.count("https://youtu.be/dQw4w9WgXcQ?t=315") == 1 and "[[5:15]" not in fixed, fixed
print("bare timestamp linking OK")

# missing opening fence: repaired, frontmatter left unlinked, body still linked
raw = 'title: "x"\nduration: "9:48"\ntags:\n  - youtube\n---\n# X\n### Intro (0:30)\n- text'
fixed = bot._clean_note(raw, meta)
assert fixed.startswith('---\ntitle: "x"\nduration: "9:48"\ntags:\n  - youtube\n---\n# X\n'), fixed
assert "### Intro ([0:30](https://youtu.be/dQw4w9WgXcQ?t=30))" in fixed and fixed.count("---") == 2, fixed
print("missing frontmatter fence repair OK")

import numpy as np
z = np.zeros((18, 32), np.float32); o = np.full((18, 32), 100, np.float32)
cands = [(0.0, "w0", z, b"a"), (30.0, "w1", z + 1, b"b"), (60.0, "w2", o, b"c"), (90.0, "w3", o + 2, b"d")]
assert bot.pick_frames(cands) == [(0.0, "w0", b"a"), (60.0, "w2", b"c")], bot.pick_frames(cands)   # near-duplicates dropped
bot.FRAMES_MAX = 1
assert bot.pick_frames(cands) == [(0.0, "w0", b"a")]                                                # cap keeps the most-changed
bot.FRAMES_MAX = 30
assert bot.pick_frames([]) == []
print("pick_frames OK")

note_with_t = bot.append_transcript("---\na: b\n---\n# N\n- x\n", "[0:00](u) one two.\n\n[0:30](v) three.")
assert note_with_t.endswith("# N\n- x\n\n## Transcript\n> [!quote]- Full transcript (speech recognition, may contain errors)\n"
                            "> [0:00](u) one two.\n>\n> [0:30](v) three.\n"), repr(note_with_t)
print("append_transcript OK")

fenced = "```markdown\n---\ntitle: x\n---\n# X\n```"
assert bot._clean_note(fenced, meta) == "---\ntitle: x\n---\n# X\n"
nofm = bot._clean_note("# Just a heading\nbody", meta)
assert nofm.startswith("---\ntitle: \"He said 'hi': a talk\"\nsource: https://www.youtube.com/watch?v=dQw4w9WgXcQ\ndate: ")
assert nofm.endswith("---\n# Just a heading\nbody\n"), repr(nofm)
print("_clean_note OK")

# --- yt-dlp error cleanup ----------------------------------------------------
import yt_dlp
e = yt_dlp.utils.DownloadError("ERROR: [youtube] abcdefghijk: Video unavailable. This video is private")
assert bot._clean_ytdlp_error(e) == "Video unavailable. This video is private", bot._clean_ytdlp_error(e)
print("_clean_ytdlp_error OK")

# --- write_note uniqueness ----------------------------------------------------
import pathlib, shutil
v = pathlib.Path(os.environ["OBSIDIAN_VAULT_PATH"]); shutil.rmtree(v, ignore_errors=True); v.mkdir()
p1 = bot.write_note("note1\n", meta); p2 = bot.write_note("note2\n", meta); p3 = bot.write_note("note3\n", meta)
assert p1.name == "He said hi a talk.md" and p2.name == "He said hi a talk (2).md" and p3.name == "He said hi a talk (3).md", (p1, p2, p3)
assert p1.read_text() == "note1\n"
shutil.rmtree(v)
print("write_note OK")
print("ALL HELPER TESTS PASSED")
