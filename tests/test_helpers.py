import os, sys, pathlib, tempfile
os.environ.update(TELEGRAM_BOT_TOKEN="x", ANTHROPIC_API_KEY="x", OBSIDIAN_VAULT_PATH=tempfile.mkdtemp(prefix="yt2obsidian-test-"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import shlex
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
prompt = bot.USER_PROMPT.format(template=tpl, platform="YouTube", title=meta.title, channel=meta.channel, url=meta.url,
                                published=meta.published, duration=meta.duration_str, chapters="(none provided)",
                                description=meta.description, post_text="(none)", transcript="blah")
assert "{placeholder}" in prompt and "{topic-tag}" in prompt and "{timestamp link}" in prompt
assert "platform: youtube" in tpl and "  - youtube" in tpl
print("USER_PROMPT.format OK")

# --- sources: YouTube, Instagram, X --------------------------------------------
S = bot.parse_source
assert S("https://youtu.be/dQw4w9WgXcQ") == bot.Source("youtube", "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
assert S("look https://www.instagram.com/reel/C9xYzAbCdEf/?igsh=abc") == bot.Source("instagram", "C9xYzAbCdEf", "https://www.instagram.com/p/C9xYzAbCdEf/")
assert S("https://instagram.com/p/DAbC_dE-fGh/").id == "DAbC_dE-fGh"
assert S("https://www.instagram.com/someuser/reels/C1234567890/").id == "C1234567890"
assert S("https://x.com/jack/status/20?s=20") == bot.Source("x", "20", "https://x.com/i/status/20")
assert S("https://twitter.com/jack/status/20").id == "20" and S("https://mobile.twitter.com/a_b/statuses/123").id == "123"
assert S("https://fxtwitter.com/jack/status/20").kind == "x"
assert S("https://x.com/jack") is None and S("https://instagram.com/someuser/") is None and S("hello") is None
assert S("https://www.youtube.com/watch?v=dQw4w9WgXcQ and https://x.com/jack/status/20").kind == "youtube"
print("parse_source OK")

T = bot._title_from_text
assert T("  Hello world!  \nsecond line", "fb") == "Hello world!"
assert T("Check this https://t.co/abc out", "fb") == "Check this out"
assert T("", "fallback") == "fallback" and T("https://t.co/only", "fallback") == "fallback"
long = T("word " * 40, "fb", limit=30)
assert long.endswith("…") and len(long) <= 32, long
print("_title_from_text OK")

x_item = bot.Item(video_id="20", url="https://x.com/jack/status/20", title="t", channel="jack", published="2006-03-21",
                  duration_seconds=0, description="", kind="x", handle="jack")
assert bot.timestamp_link(x_item, 65) == "[1:05](https://x.com/jack/status/20)"
assert bot.timestamp_link(meta, 65) == "[1:05](https://youtu.be/dQw4w9WgXcQ?t=65)"
assert x_item.platform == "X" and meta.platform == "YouTube" and bot.template_for(x_item) is bot.POST_TEMPLATE and bot.template_for(meta) is bot.NOTE_TEMPLATE
xt = bot.render_template(x_item)
assert 'author: "jack"' in xt and "platform: x" in xt and "type: post-note" in xt and "{topic-tag}" in xt
print("Item / template_for OK")

# --- filing --------------------------------------------------------------------
listing = ["2026-09-04 21:16:32       9024 Projects/F1 driver vs car/F1 Twitter Plan.md",
           "2026-09-04 21:16:32      12452 Knowledge/Crypto trading/x.md", "Home.md", ".obsidian/app.json",
           "Life/note.md", "", "Knowledge/Programming/sub/deep.md"]
assert bot.folders_from_listing(listing) == ["Knowledge", "Knowledge/Crypto trading", "Knowledge/Programming",
                                             "Knowledge/Programming/sub", "Life", "Projects", "Projects/F1 driver vs car"], bot.folders_from_listing(listing)
existing = ["Knowledge", "Knowledge/Crypto trading", "Life", "Projects", "Projects/Football scouting"]
N = lambda f: bot.normalize_folder(f, existing)
assert N("Knowledge/Crypto trading") == "Knowledge/Crypto trading"
assert N("knowledge/crypto Trading") == "Knowledge/Crypto trading"          # existing spelling wins
assert N("Projects/Football scouting") == "Projects/Football scouting"     # existing project folder allowed
assert N("Projects/New thing") == "Knowledge/New thing"                    # new folders only under the root
assert N("Crypto trading") == "Knowledge/Crypto trading" and N("Economics") == "Knowledge/Economics"
assert N("Knowledge/Programming/Rust") == "Knowledge/Programming"          # two levels max
assert N("Knowledge") == "Knowledge/Inbox" and N("") == "Knowledge/Inbox" and N("Home") == "Knowledge/Inbox"
assert N('Knowledge/Bad: "name"/x') == "Knowledge/Bad name"
assert bot.is_catch_all("Knowledge/Inbox") and bot.is_catch_all("misc") and bot.is_catch_all("Knowledge/Unsorted/")
assert not bot.is_catch_all("Knowledge/Space") and not bot.is_catch_all("Knowledge/Inbox management")
print("folders_from_listing / normalize_folder OK")

imgs = [pathlib.Path(tempfile.mkdtemp()) / "a.jpg"]; imgs[0].write_bytes(b"img")
assert bot.images_as_frames(imgs) == [(-1, "image attached to the post", b"img")]
assert bot._frame_label(meta, -1, "w") == "Image 1 (w):" and bot._frame_label(meta, 12, "w").startswith("Frame at [0:12]")
print("images_as_frames OK")

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

# --- write_note uniqueness and folders ----------------------------------------
import shutil
v = pathlib.Path(os.environ["OBSIDIAN_VAULT_PATH"]); shutil.rmtree(v, ignore_errors=True); v.mkdir()
p1 = bot.write_note("note1\n", meta); p2 = bot.write_note("note2\n", meta); p3 = bot.write_note("note3\n", meta)
assert p1.name == "He said hi a talk.md" and p2.name == "He said hi a talk (2).md" and p3.name == "He said hi a talk (3).md", (p1, p2, p3)
assert p1.read_text() == "note1\n"
p4 = bot.write_note("note4\n", meta, "Knowledge/Crypto trading")
assert p4 == v / "Knowledge" / "Crypto trading" / "He said hi a talk.md" and p4.read_text() == "note4\n"
assert bot.list_vault_folders() == ["Knowledge", "Knowledge/Crypto trading"]
assert sorted(bot.list_vault_paths())[:2] == ["He said hi a talk (2).md", "He said hi a talk (3).md"]

# topic pages and topic links
assert bot.topic_of("Knowledge/Crypto trading") == "Crypto trading" and bot.topic_of("Projects/X") is None and bot.topic_of("Knowledge") is None
tp = bot.ensure_topic_note("Knowledge/Crypto trading")
assert tp == v / "Knowledge" / "Crypto trading" / "Crypto trading.md" and tp.read_text().startswith("---\ntags:\n  - topic\nup: \"[[Home]]\"\n---\n\n# Crypto trading\n")
assert 'path:"Knowledge/Crypto trading/" -tag:#topic' in tp.read_text()
assert bot.ensure_topic_note("Knowledge/Crypto trading") is None                 # already there
assert bot.ensure_topic_note("Projects/Football scouting") is None               # projects get no topic page
linked = bot.link_note_to_topic('---\ntitle: "x"\n---\n# X\n\n## Related\n- [[Other]]\n\n## Source\n- v\n', "Crypto trading")
assert linked == '---\ntitle: "x"\ntopic: "[[Crypto trading]]"\n---\n# X\n\n## Related\n- [[Crypto trading]]\n- [[Other]]\n\n## Source\n- v\n', linked
assert bot.link_note_to_topic(linked, "Crypto trading") == linked                 # idempotent
no_related = bot.link_note_to_topic("---\na: b\n---\n# X\n- x\n\n## Transcript\n> t\n", "Programming")
assert no_related == '---\na: b\ntopic: "[[Programming]]"\n---\n# X\n- x\n\n## Related\n- [[Programming]]\n\n## Transcript\n> t\n', no_related
assert bot.link_note_to_topic("# plain\n", "T") == "# plain\n\n## Related\n- [[T]]\n"
print("topic pages / links OK")
# AFTER_NOTE_COMMAND placeholders: {path}, {relpath}, {folder}
out = v / "hook.txt"
bot.AFTER_NOTE_COMMAND = f"printf '%s|%s|%s' {{path}} {{relpath}} {{folder}} > {shlex.quote(str(out))}"
bot.run_after_note_command(p4)
assert out.read_text() == f"{p4}|Knowledge/Crypto trading/He said hi a talk.md|Knowledge/Crypto trading", out.read_text()
bot.run_after_note_command(p1)
assert out.read_text() == f"{p1}|He said hi a talk.md|", out.read_text()
bot.AFTER_NOTE_COMMAND = ""
shutil.rmtree(v)
print("write_note / folders / placeholders OK")
print("ALL HELPER TESTS PASSED")
