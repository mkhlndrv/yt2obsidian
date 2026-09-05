"""Exercise generate_note() and the Telegram handlers with stubbed Anthropic / Telegram objects."""
import os, sys, asyncio, types, shlex
os.environ.update(TELEGRAM_BOT_TOKEN="x", ANTHROPIC_API_KEY="x", OBSIDIAN_VAULT_PATH=__import__("tempfile").mkdtemp(prefix="yt2obsidian-test-"))
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
import anthropic, httpx2 as httpx
import bot

meta = bot.VideoMeta(video_id="dQw4w9WgXcQ", url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                     title="A Talk", channel="Chan", published="2024-01-02", duration_seconds=65, description="")

class FakeStream:
    def __init__(self, msg): self.msg = msg
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get_final_message(self): return self.msg

def fake_client(msg=None, exc=None, parsed=None):
    captured = {}
    def stream(**kw):
        captured.update(kw)
        if exc: raise exc
        return FakeStream(msg)
    async def parse(**kw):
        captured.update(kw)
        if exc: raise exc
        return types.SimpleNamespace(parsed_output=parsed)
    return types.SimpleNamespace(messages=types.SimpleNamespace(stream=stream, parse=parse)), captured

def fake_message(text, stop_reason="end_turn"):
    blocks = [types.SimpleNamespace(type="thinking", thinking=""), types.SimpleNamespace(type="text", text=text)]
    return types.SimpleNamespace(stop_reason=stop_reason, content=blocks,
                                 usage=types.SimpleNamespace(input_tokens=10, output_tokens=5))

async def main():
    # happy path
    note_text = "---\ntitle: \"A Talk\"\ntags:\n  - youtube\n  - talks\n---\n\n# A Talk\n\n## Key Points\n- x"
    client, captured = fake_client(fake_message(note_text))
    bot._anthropic_client = client
    note = await bot.generate_note(meta, "hello world transcript")
    assert note == note_text + "\n", repr(note)
    assert captured["model"] == "claude-sonnet-5" and captured["thinking"] == {"type": "adaptive"}
    assert captured["system"] == bot.SYSTEM_PROMPT
    content = captured["messages"][0]["content"]
    assert isinstance(content, list) and len(content) == 1 and content[0]["type"] == "text", content
    assert "hello world transcript" in content[0]["text"] and 'title: "A Talk"' in content[0]["text"]
    print("generate_note happy path OK")

    # with screenshots: intro text, then label + image per frame, then the prompt
    import base64
    bot._anthropic_client, captured = fake_client(fake_message(note_text))
    await bot.generate_note(meta, "t", frames=[(12.0, "the code", b"\xff\xd8jpeg-bytes")])
    content = captured["messages"][0]["content"]
    assert [c["type"] for c in content] == ["text", "text", "image", "text"], content
    assert content[0]["text"] == bot.FRAMES_INTRO
    assert content[1]["text"] == "Frame at [0:12](https://youtu.be/dQw4w9WgXcQ?t=12) (the code):"
    assert content[2]["source"] == {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(b"\xff\xd8jpeg-bytes").decode()}
    assert "TRANSCRIPT" in content[3]["text"]
    print("generate_note with frames OK")

    # find_screen_cues: structured call, normalized result, failure -> no screenshots
    picked = bot.ScreenMoments(moments=[bot.ScreenMoment(seconds=40, why="terminal output"),
                                        bot.ScreenMoment(seconds=12.4, why="the code"),
                                        bot.ScreenMoment(seconds=13, why="same screen"),
                                        bot.ScreenMoment(seconds=5000, why="hallucinated")])
    bot._anthropic_client, captured = fake_client(parsed=picked)
    cues = await bot.find_screen_cues([(0.0, 1.0, "hello"), (12.0, 15.0, "this code here")], meta)
    assert cues == [(12.4, "the code"), (40.0, "terminal output")], cues
    assert captured["output_format"] is bot.ScreenMoments and captured["model"] == bot.CUE_MODEL
    assert captured["output_config"] == {"effort": "low"} and "12: this code here" in captured["messages"][0]["content"]
    assert captured["system"] == bot.CUE_PROMPT.format(max_moments=bot.FRAMES_MAX)
    cue_req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    bot._anthropic_client, _ = fake_client(exc=anthropic.APIConnectionError(request=cue_req))
    assert await bot.find_screen_cues([(0.0, 1.0, "hello")], meta) == []
    bot._anthropic_client, _ = fake_client(parsed=None)
    assert await bot.find_screen_cues([(0.0, 1.0, "hello")], meta) == []
    print("find_screen_cues OK")

    # transcribe_audio via the API backend: chunks are stitched with time offsets; failures are clear
    import httpx as _httpx
    from pathlib import Path as _P
    bot.TRANSCRIBER = "api"
    bot._split_audio_for_api = lambda audio, out: [_P("chunk_000.mp3"), _P("chunk_001.mp3")]
    payloads = {
        "chunk_000.mp3": {"duration": 1200.0, "segments": [{"start": 0.0, "end": 4.0, "text": " Hello "}, {"start": 4.0, "end": 9.0, "text": "world"}]},
        "chunk_001.mp3": {"duration": 30.0, "text": "plain text only"},
    }
    bot._post_transcription = lambda chunk: payloads[chunk.name]
    segs = bot.transcribe_audio(_P("audio.m4a"), "dQw4w9WgXcQ")
    assert segs == [(0.0, 4.0, "Hello"), (4.0, 9.0, "world"), (1200.0, 1230.0, "plain text only")], segs
    def boom_post(chunk): raise _httpx.ConnectError("no route")
    bot._post_transcription = boom_post
    try:
        bot.transcribe_audio(_P("audio.m4a"), "x"); raise AssertionError("no error")
    except bot.PipelineError as e:
        assert "Could not reach the transcription API" in str(e), e
    bot._post_transcription = lambda chunk: {"duration": 5.0, "segments": []}
    try:
        bot.transcribe_audio(_P("audio.m4a"), "x"); raise AssertionError("no error")
    except bot.PipelineError as e:
        assert "no text" in str(e), e
    bot.TRANSCRIBER = "local"
    print("transcribe_audio api backend OK")

    # truncated
    bot._anthropic_client, _ = fake_client(fake_message(note_text, stop_reason="max_tokens"))
    note = await bot.generate_note(meta, "t")
    assert "Note truncated" in note
    print("generate_note max_tokens OK")

    # refusal / empty
    for msg, expect in [(fake_message(note_text, "refusal"), "declined"), (fake_message("   "), "empty")]:
        bot._anthropic_client, _ = fake_client(msg)
        try:
            await bot.generate_note(meta, "t"); raise AssertionError("no error")
        except bot.PipelineError as e:
            assert expect in str(e), e
    print("generate_note refusal/empty OK")

    # API errors -> PipelineError with clear text
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    def status_exc(cls, code):
        return cls("boom", response=httpx.Response(code, request=req), body=None)
    errs = [
        (status_exc(anthropic.AuthenticationError, 401), "API key"),
        (status_exc(anthropic.NotFoundError, 404), "not found"),
        (status_exc(anthropic.RateLimitError, 429), "rate limit"),
        (status_exc(anthropic.InternalServerError, 500), "API error 500"),
        (anthropic.APIConnectionError(request=req), "Could not reach"),
    ]
    for exc, expect in errs:
        bot._anthropic_client, _ = fake_client(exc=exc)
        try:
            await bot.generate_note(meta, "t"); raise AssertionError("no error")
        except bot.PipelineError as e:
            assert expect in str(e), (type(exc).__name__, str(e))
    print("generate_note error mapping OK")

    # --- Telegram handlers with a fake update/context ------------------------
    replies, sent, tasks, docs, edits = [], [], [], [], []
    async def reply_text(t): replies.append(t)
    async def send_message(chat_id, text): sent.append((chat_id, text)); return types.SimpleNamespace(message_id=len(sent))
    async def edit_message_text(chat_id, message_id, text): edits.append((message_id, text))
    async def send_document(chat_id, document, filename, caption): docs.append((chat_id, document, filename, caption))
    def create_task(coro, update=None): tasks.append(coro)
    def mk_update(text, uid=42):
        msg = types.SimpleNamespace(text=text, chat_id=7, reply_text=reply_text)
        return types.SimpleNamespace(effective_message=msg, effective_user=types.SimpleNamespace(id=uid))
    ctx = types.SimpleNamespace(bot=types.SimpleNamespace(send_message=send_message, edit_message_text=edit_message_text,
                                                          send_document=send_document),
                                application=types.SimpleNamespace(create_task=create_task))
    def status(): return edits[-1][1]   # the job's status message as last edited

    await bot.handle_message(mk_update("hi there"), ctx)
    assert replies[-1] == "Not a YouTube link. Send a youtube.com or youtu.be video URL." and not tasks
    n_replies = len(replies)
    await bot.handle_message(mk_update("https://youtu.be/dQw4w9WgXcQ"), ctx)
    assert len(tasks) == 1 and len(replies) == n_replies   # no chatter: the job's own status message does the talking
    tasks[-1].close()
    print("handle_message routing OK")

    bot.ALLOWED_USER_IDS.add(1)
    await bot.handle_message(mk_update("https://youtu.be/dQw4w9WgXcQ", uid=42), ctx)
    assert "private" in replies[-1] and len(tasks) == 1
    bot.ALLOWED_USER_IDS.clear()
    print("authorization OK")

    await bot.cmd_start(mk_update("/start"), ctx)
    assert replies[-1].endswith("Your Telegram user id: 42"), replies[-1]
    print("cmd_start OK")

    from telegram.error import NetworkError as NE, TimedOut
    n = len(replies)
    await bot.on_error(None, types.SimpleNamespace(error=NE("dns down")))      # polling error, no update
    await bot.on_error(None, types.SimpleNamespace(error=TimedOut()))          # subclass of NetworkError
    assert len(replies) == n
    await bot.on_error(mk_update("x"), types.SimpleNamespace(error=RuntimeError("bad")))
    assert replies[-1] == "Unexpected error (RuntimeError): bad", replies[-1]
    print("on_error OK")

    # process_video: download failure surfaces as a clear message, nothing else runs
    def boom(*a, **k): raise bot.PipelineError("Could not download the video: Video unavailable")
    bot.download_audio = boom
    await bot.process_video("dQw4w9WgXcQ", 7, ctx)
    assert sent[-1] == (7, "Processing\n\nDownloading…"), sent[-1]
    assert status() == "Failed\n\nCould not download the video: Video unavailable", status()
    def crash(*a, **k): raise RuntimeError("weird")
    bot.download_audio = crash
    await bot.process_video("dQw4w9WgXcQ", 7, ctx)
    assert status() == "Failed\n\nUnexpected error (RuntimeError): weird", status()
    # shutdown mid-job: the status message says so and the cancellation still propagates
    def cancelled(*a, **k): raise asyncio.CancelledError()
    bot.download_audio = cancelled
    try:
        await bot.process_video("dQw4w9WgXcQ", 7, ctx); raise AssertionError("cancellation swallowed")
    except asyncio.CancelledError:
        pass
    assert status() == "Failed\n\nThe bot restarted while processing. Send the link again.", status()
    print("process_video error reporting OK")

    # process_video full flow with stubbed steps -> file written, stage messages sent
    import pathlib, shutil, tempfile
    v = pathlib.Path(os.environ["OBSIDIAN_VAULT_PATH"]); shutil.rmtree(v, ignore_errors=True); v.mkdir()
    def fake_download(vid, out_dir):
        p = out_dir / "audio.m4a"; p.write_bytes(b"x"); return meta, p
    seen = {}
    def fake_transcribe(p, video_id): seen["audio_existed"] = p.exists(); seen["path"] = p; seen["vid"] = video_id; return [(0.0, 1.0, "one two three")]
    bot.download_audio = fake_download; bot.transcribe_audio = fake_transcribe
    bot.INCLUDE_TRANSCRIPT = False
    bot.INCLUDE_FRAMES = False
    bot._anthropic_client, _ = fake_client(fake_message(note_text))
    n_before = len(sent)
    await bot.process_video("dQw4w9WgXcQ", 7, ctx)
    assert len(sent) == n_before + 1, "exactly one status message per job"
    assert status() == "Done\nA Talk (1:05)\n\nDownloaded\nTranscribed — 3 words\nNote saved: A Talk", status()
    assert (v / "A Talk.md").read_text() == note_text + "\n"
    assert docs == [(7, (note_text + "\n").encode(), "A Talk.md", "A Talk")], docs
    assert seen["audio_existed"] and not seen["path"].exists(), "temp audio not deleted"
    assert seen["vid"] == "dQw4w9WgXcQ"
    print("process_video full flow OK (temp audio deleted, note written, file sent)")

    # with INCLUDE_TRANSCRIPT the saved note and the sent file carry the folded transcript
    bot.INCLUDE_TRANSCRIPT = True
    bot._anthropic_client, _ = fake_client(fake_message(note_text))
    await bot.process_video("dQw4w9WgXcQ", 7, ctx)
    saved = (v / "A Talk (2).md").read_text()
    assert saved.endswith("## Transcript\n> [!quote]- Full transcript (speech recognition, may contain errors)\n> [0:00](https://youtu.be/dQw4w9WgXcQ?t=0) one two three\n"), saved
    assert docs[-1][1] == saved.encode()
    bot.INCLUDE_TRANSCRIPT = False
    print("transcript appended OK")

    # screenshots enabled but Claude picks no moment: no video download, no frames
    bot.INCLUDE_FRAMES = True
    calls = []
    def fake_video(vid, out_dir): calls.append("video"); p = out_dir / "video.mp4"; p.write_bytes(b"v"); return p
    def fake_frames(vp, m, out_dir, cues): assert vp.exists(); calls.append(("frames", cues)); return [(5.0, "why", b"img")]
    async def no_cues(segments, m): return []
    bot.download_video_stream = fake_video; bot.extract_frames = fake_frames; bot.find_screen_cues = no_cues
    bot._anthropic_client, captured = fake_client(fake_message(note_text))
    await bot.process_video("dQw4w9WgXcQ", 7, ctx)
    assert calls == [] and status().startswith("Done") and "Screenshots" not in status(), (calls, status())
    # with a picked moment: video stream + frame at that moment, Claude receives the image, message counts it
    async def one_cue(segments, m): return [(12.0, "the code")]
    bot.find_screen_cues = one_cue
    bot._anthropic_client, captured = fake_client(fake_message(note_text))
    await bot.process_video("dQw4w9WgXcQ", 7, ctx)
    assert calls == ["video", ("frames", [(12.0, "the code")])], calls
    assert "\nScreenshots — 1\n" in status() and status().startswith("Done"), status()
    assert any(c["type"] == "image" for c in captured["messages"][0]["content"])
    # frame extraction failure must not lose the note
    def broken_frames(vp, m, out_dir, cues): raise RuntimeError("ffmpeg missing")
    bot.extract_frames = broken_frames
    bot._anthropic_client, captured = fake_client(fake_message(note_text))
    await bot.process_video("dQw4w9WgXcQ", 7, ctx)
    assert status().startswith("Done") and "Screenshots" not in status(), status()
    assert (v / "A Talk (5).md").exists()
    bot.INCLUDE_FRAMES = False
    print("frames flow OK (Claude-gated, image sent, extraction failure tolerated)")

    # SEND_NOTE_FILE=false: the note is saved and confirmed, no file is attached
    n_docs = len(docs)
    bot.SEND_NOTE_FILE = False
    bot._anthropic_client, _ = fake_client(fake_message(note_text))
    await bot.process_video("dQw4w9WgXcQ", 7, ctx)
    assert "\nNote saved: A Talk" in status() and status().startswith("Done") and len(docs) == n_docs, (status(), len(docs) - n_docs)
    bot.SEND_NOTE_FILE = True
    print("SEND_NOTE_FILE=false OK")

    # AFTER_NOTE_COMMAND: runs with the note path substituted; a failing command warns but keeps the note
    marker = pathlib.Path(os.environ["OBSIDIAN_VAULT_PATH"]) / "uploaded.txt"
    bot.AFTER_NOTE_COMMAND = f"cat {{path}} > {shlex.quote(str(marker))}"
    bot._anthropic_client, _ = fake_client(fake_message(note_text))
    await bot.process_video("dQw4w9WgXcQ", 7, ctx)
    assert marker.read_text() == note_text + "\n", marker.read_text()[:80]
    assert "\nNote added to vault: A Talk" in status() and "failed" not in status(), status()
    bot.AFTER_NOTE_COMMAND = "exit 3"
    bot._anthropic_client, _ = fake_client(fake_message(note_text))
    await bot.process_video("dQw4w9WgXcQ", 7, ctx)
    assert "\nVault upload failed (exit 3)" in status() and "\nNote kept on the server: A Talk" in status(), status()
    assert docs[-1][2].startswith("A Talk"), docs[-1][2]   # the file was still delivered
    bot.AFTER_NOTE_COMMAND = ""
    print("AFTER_NOTE_COMMAND OK")

    # send_document failure -> note kept, clear warning
    from telegram.error import NetworkError
    async def bad_send_document(**kw): raise NetworkError("boom")
    ctx.bot.send_document = bad_send_document
    n_files = len(list(v.glob("A Talk*.md")))
    await bot.process_video("dQw4w9WgXcQ", 7, ctx)
    assert status().startswith("Done") and "Sending the file failed: boom" in status(), status()
    assert len(list(v.glob("A Talk*.md"))) == n_files + 1
    shutil.rmtree(v)
    print("send_document failure path OK")
    print("ALL STUB TESTS PASSED")

asyncio.run(main())
