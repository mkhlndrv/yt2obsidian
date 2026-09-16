# yt2obsidian

Send a YouTube, Instagram, or X link to a Telegram bot; get a structured Obsidian note filed
into the right folder of your vault, without watching or reading the original.

```
link ──▶ download (yt-dlp / gallery-dl / FixTweet) ──▶ transcript (Whisper) ──▶ screenshots of
the moments that matter (Claude picks them) ──▶ note (Claude) ──▶ folder (Claude picks it)
──▶ .md in your vault (or in the chat)
```

For a video the note is built to replace watching: a summary, key takeaways, notes organised
by topic with links back to the exact moment in the video, code and slide text pulled from
screenshots, things worth following up, quotes, wikilinks into your vault, and the full
timestamped transcript folded at the bottom so the whole video is searchable. See
[`examples/Rust in 100 Seconds.md`](examples/Rust%20in%20100%20Seconds.md) for a real one.
An Instagram post, a reel, or a tweet gets a shorter note: the author's own words, what the
images show, and the takeaways.

In Telegram the bot posts one status message per video and updates it in place:

```
Processing
How does raising interest rates control inflation? (8:14)

Downloaded
Transcribed — 1,202 words
Screenshots — 2
Writing note…
```

It ends as `Done` with `Filed under Knowledge/Economics` and `Note added to vault: …`, or
`Failed` with the reason.

Everything is one Python file. It runs on a laptop or on a small always-on server (an AWS
setup is included) and costs a few cents per video in API calls.

## Contents

- [How it works](#how-it-works)
- [Layout](#layout)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Deploy on AWS (EC2)](#deploy-on-aws-ec2)
- [Faster transcription](#faster-transcription)
- [Automatic sync into your vault](#automatic-sync-into-your-vault)
- [Customising the notes](#customising-the-notes)
- [Limitations and troubleshooting](#limitations-and-troubleshooting)
- [License](#license)

## How it works

Each link goes through six steps, all in `bot.py`:

1. **Download.** For YouTube, yt-dlp fetches the audio-only stream and the video's metadata
   (title, channel, chapters). For Instagram, gallery-dl fetches the post's images or the
   reel's video plus the caption. For X, the FixTweet API returns the tweet's text, author,
   media, quoted tweet, and the earlier tweets it replies to, with no account needed. Nothing
   is kept after the job.
2. **Transcribe.** Either locally with faster-whisper (`distil-large-v3`, int8, English
   only, no account needed) or through an OpenAI-compatible speech-to-text API (OpenAI or
   Groq), which is about 100 times faster. Segments keep their timestamps.
3. **Pick screenshots.** Speech alone misses what is only on screen. Two sources feed the
   same pipeline, and the rule depends on the video, not the platform:
   - *Transcript-chosen moments*, for any video with speech. Claude reads the timestamped
     transcript once (a cheap, low-effort call with a structured JSON reply) and returns the
     moments where the screen carries information the words don't: code, slides, charts,
     tables, a UI being walked through.
   - *A visual scan*, for any video up to `FRAMES_SCAN_MAX_MINUTES` (default 5): reels,
     tweet clips, Shorts. The video is decoded once at thumbnail size and a frame is taken
     wherever the picture changes, thinned evenly to `FRAMES_SCAN_MAX`. A talking head yields
     one or two frames, a slideshow one per slide, a silent clip still gets covered. Longer
     videos rely on the transcript-chosen moments alone, because sampling a long video on a
     timer both misses things and wastes tokens.

   A frame is grabbed at each moment, near-duplicates are dropped, and up to 30 frames go to
   the next step labelled with their timestamps. For YouTube the small video-only stream is
   fetched only when something will be taken from it. Clips without an audio track skip
   transcription, and a clip with music but no speech gets an empty transcript, not an error.
4. **Write the note.** Claude gets the transcript, the post's own text, the metadata, the
   chapters, the frames, and any attached images, plus a fixed template and a rule set, and
   writes the note. Videos get the full template; posts, reels, and tweets get a short-form
   one. The rules that matter most, all learned from testing across genres:
   - Notes are organised by topic, not by time: each section is a concept, step, or argument,
     bullets lead with the key point in bold, details nest under it.
   - Timestamp links go on section headings, quotes, and on-screen references only.
   - No fixed length; it follows how dense the content is.
   - Sponsors, ads, and self-promotion are left out entirely, whether spoken or on screen.
   - Speakers are attributed in interviews and inserted clips; stated numbers and named
     examples are kept; on-screen references that could not be captured are flagged.
   - Wikilinks only for entities you would want a note about, plus a Related section of
     broader topics, so notes connect over time.
5. **File.** Claude sees the folders that already exist in the vault and picks the one the
   note belongs in, or names a new topic folder under `Knowledge/` (for example
   `Knowledge/Crypto trading`, `Knowledge/Programming`). Existing project folders are used
   only when a note is clearly about that project; new folders never go anywhere else. A
   catch-all answer ("Inbox", "Misc") is asked again once, and the pick is normalised in
   code, so a bad answer lands in `Knowledge/Inbox` rather than somewhere odd. Each topic
   folder gets a **topic page** named after it (created with the
   folder's first note) that lists its notes live through an Obsidian `query` block and links
   up to `Home`; every filed note gets a `topic` property and a Related entry pointing at that
   page. That is what keeps the graph connected: Home → topics → notes, with no plugins.
6. **Deliver.** The note is written under that folder, optionally pushed anywhere by a shell
   command (for example `aws s3 cp` into a bucket your vault syncs from, keeping the folder),
   and optionally attached to the chat as a file.

Everything Claude-facing is a constant at the top of `bot.py`: `NOTE_TEMPLATE` and
`POST_TEMPLATE`, `SYSTEM_PROMPT`, `USER_PROMPT` (the note), `CUE_PROMPT` (screenshot
selection), `FILE_PROMPT` (filing), and `TOPIC_NOTE_TEMPLATE` (topic pages). A `Home` note
with a `tag:#topic` query block lists every topic page automatically.

## Layout

| Path | What |
|---|---|
| `bot.py` | The whole bot: Telegram handlers, download, transcription (local or API), screenshot selection, note writing, delivery. |
| `.env.example` | Every configuration variable with its default. |
| `tests/` | Helper tests plus stub-driven tests of the Telegram and Claude layers; no network or API key needed. |
| `eval/` | Cross-genre evaluation harness: eight real videos through the full pipeline, with cached transcripts and screenshots for fast prompt iteration. |
| `deploy/` | First-boot script for an AWS EC2 instance, and an Obsidian-on-the-server installer for the Obsidian Sync route. |
| `examples/` | A note generated by the pipeline. |

## Quick start

You need Python 3.11+, `ffmpeg`, a Telegram bot token, and an Anthropic API key.

1. **System packages** (Ubuntu shown; on macOS `brew install ffmpeg`):

   ```bash
   sudo apt update && sudo apt install -y ffmpeg python3 python3-venv python3-pip
   ```

2. **Python environment:**

   ```bash
   git clone https://github.com/mkhlndrv/yt2obsidian.git
   cd yt2obsidian
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

   `yt-dlp[default,deno]` brings the JavaScript runtime yt-dlp needs for YouTube; it lands in
   `.venv/bin/deno`, nothing system-wide.

3. **Telegram bot token:** message [@BotFather](https://t.me/BotFather), send `/newbot`,
   copy the token.

4. **Anthropic API key:** create one at <https://console.anthropic.com/>.

5. **Config:**

   ```bash
   cp .env.example .env
   nano .env      # TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY; everything else has a default
   ```

6. **Run:**

   ```bash
   python bot.py
   ```

   The first start downloads the Whisper model (about 1.5 GB) into `~/.cache/huggingface/`.
   When the log says `Bot is running`, send the bot `/start` (it replies with your Telegram
   user id; put it into `TELEGRAM_ALLOWED_USER_IDS` so nobody else can use your bot), then a
   YouTube link. Notes are saved under `notes/` next to `bot.py` and attached to the chat.

To keep it running on any Linux box, a systemd unit like the one written by
[`deploy/ec2-user-data.sh`](deploy/ec2-user-data.sh) works everywhere: `ExecStart` the venv's
Python with `bot.py`, `Restart=always`, `WorkingDirectory` set to the repo.

## Configuration

All settings live in `.env` (see `.env.example`). Only the first two are required.

| Variable | Default | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | | Token from BotFather. |
| `ANTHROPIC_API_KEY` | | Anthropic API key. |
| `TELEGRAM_ALLOWED_USER_IDS` | empty (anyone) | Comma-separated Telegram user ids allowed to use the bot. Send `/start` to learn yours. |
| `OBSIDIAN_VAULT_PATH` | `notes/` next to `bot.py` | Folder where every note is written. Point it inside your vault if the bot runs on the machine that has the vault. |
| `SEND_NOTE_FILE` | `true` | Attach the finished `.md` to the chat. Set `false` once the vault syncs by itself. |
| `AFTER_NOTE_COMMAND` | empty | Shell command run after each note; `{path}` is the file, `{relpath}` its path inside the vault including the folder, `{folder}` the folder alone. Typically an upload, e.g. `aws s3 cp {path} s3://my-vault/{relpath}`. |
| `KNOWLEDGE_ROOT` | `Knowledge` | Where new topic folders are created. |
| `VAULT_LIST_COMMAND` | empty | Prints the vault's file paths (one per line; `aws s3 ls --recursive` output works) so the bot sees the existing folders when the vault is not on this machine. Empty: the folders under `OBSIDIAN_VAULT_PATH`. |
| `GALLERY_DL_BIN` | next to the Python interpreter | Path to `gallery-dl` (Instagram) if it is elsewhere. |
| `CLAUDE_MODEL` | `claude-sonnet-5` | Model that writes the note. |
| `NOTE_BACKEND` | `api` | `api` (Anthropic API, metered) or `claude-code` (the Claude Code CLI on this machine with a Pro/Max subscription); see [Running on a Claude subscription](#running-on-a-claude-subscription-instead-of-the-api). |
| `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CODE_BIN` | | Token from `claude setup-token`; path to the `claude` binary if not on PATH. Only for `NOTE_BACKEND=claude-code`. |
| `TRANSCRIBER` | `local` | `local` (faster-whisper on this machine) or `api` (OpenAI-compatible endpoint). |
| `STT_API_KEY`, `STT_BASE_URL`, `STT_MODEL` | OpenAI defaults | Transcription API settings; see [Faster transcription](#faster-transcription). |
| `WHISPER_MODEL`, `WHISPER_DEVICE`, `WHISPER_COMPUTE_TYPE` | `distil-large-v3`, `cpu`, `int8` | Local model settings. `cuda` + `float16` on an NVIDIA GPU. |
| `WHISPER_CPU_THREADS`, `WHISPER_BEAM_SIZE` | `0` (library default), `5` | Local speed knobs: threads = your core count; beam 1 is about 10% faster. |
| `INCLUDE_FRAMES` | `true` | Screenshots on or off. |
| `CUE_MODEL` | same as `CLAUDE_MODEL` | Model for the screenshot-selection pass. |
| `FRAMES_MAX`, `FRAMES_VIDEO_HEIGHT` | `30`, `720` | Most screenshots per video; resolution of the video stream fetched for them. |
| `FRAMES_SCAN_MAX_MINUTES`, `FRAMES_SCAN_MAX` | `5`, `12` | Videos up to this length also get the visual scan (0 turns it off); most scan frames per video. |
| `INCLUDE_TRANSCRIPT` | `true` | Append the full timestamped transcript in a folded section. |
| `MAX_VIDEO_MINUTES` | `0` (no limit) | Refuse longer videos. |
| `YTDLP_COOKIES_FILE` | empty | Netscape-format cookies file; needed for YouTube on most cloud servers and always for Instagram. One file can hold both sites' cookies. |

## Instagram and X

- **X (Twitter):** any public tweet works with no account, via the FixTweet API. The note
  includes the tweet, the earlier tweets it replies to (so a thread read from its last tweet
  keeps its context), a quoted tweet, attached photos (read by Claude), and an attached video
  (transcribed, with screenshots). Replies *below* the linked tweet are not fetched, so link
  the last tweet of a thread you want whole.
- **Instagram:** posts, carousels, and reels through gallery-dl, which needs the cookies of a
  logged-in Instagram session in `YTDLP_COOKIES_FILE` (export them the same way as the
  YouTube cookies, from a private window logged into both sites, into one file). Reels are
  transcribed like videos; image posts go to Claude as images with the caption.
- Both get the short-form note (`POST_TEMPLATE`): the author's words, what the images show
  where it matters, and the takeaways. Timestamps in a reel's transcript link to the post
  itself, since those platforms have no jump-to-second links.

## Deploy on AWS (EC2)

The bot is one long-running process that polls Telegram, so it wants one small always-on
Linux VM with no inbound ports.

1. **Launch an instance** in the EC2 console: Ubuntu Server 24.04 LTS; `t4g.medium` (ARM, 2
   vCPU, 4 GB, about $25 a month) or `t3.medium` on x86; a 20 GB disk; a key pair; a security
   group that allows SSH from your IP only. With the API transcription backend a `t4g.small`
   is enough. The 1 GB free-tier sizes cannot hold the Whisper model.
2. **Paste [`deploy/ec2-user-data.sh`](deploy/ec2-user-data.sh)** into *Advanced details →
   User data* before launching (edit `REPO_URL` first if you forked). On first boot it installs
   ffmpeg and Python, clones the repo into `/home/ubuntu/yt2obsidian`, installs the
   requirements, downloads the Whisper model, and registers a systemd service. About five
   minutes; `sudo tail -f /var/log/cloud-init-output.log` shows progress.
3. **Configure and start** over SSH as `ubuntu`:

   ```bash
   cd ~/yt2obsidian
   cp .env.example .env && nano .env      # bot token, Anthropic key, your Telegram user id
   chmod 600 .env
   sudo systemctl start yt2obsidian
   journalctl -u yt2obsidian -f           # wait for "Bot is running"
   ```

   The service restarts on failure and starts on boot. Telegram lets only one process poll a
   bot token, so stop any other copy first.
4. **YouTube cookies.** YouTube refuses downloads from most datacenter addresses
   ("Sign in to confirm you're not a bot"). Fix: in your browser, install a cookies-export
   extension ("Get cookies.txt LOCALLY" for Chrome, allowed in incognito), open a private
   window, log in to YouTube, open `youtube.com/robots.txt` in a tab, export the cookies in
   Netscape format, close the private window (so the browser never rotates those cookies).
   Copy the file to the server, `chmod 600` it, and set `YTDLP_COOKIES_FILE` to its path. A
   throwaway Google account works fine for this.

**Updating:** `cd ~/yt2obsidian && git pull && sudo systemctl restart yt2obsidian`.

## Faster transcription

Transcription is the slow step on a CPU. Measured on the same ten-minute video:

| Setup | Time for 10 min of audio |
| --- | --- |
| Local `distil-large-v3`, 12-core laptop, defaults | 129 s |
| Same, `WHISPER_BEAM_SIZE=1` | 118 s |
| Same, faster-whisper batched pipeline | 6278 s (do not use on CPU) |
| Local on a 2-vCPU cloud instance | roughly 600 s (about realtime) |
| API backend (Groq `whisper-large-v3-turbo`, 19-minute video) | 7 s |

Local knobs give tens of percent; the API backend is the real win and lets the server be
tiny. Groq has a free tier; OpenAI charges about half a cent per minute. In `.env`:

```bash
TRANSCRIBER=api
STT_API_KEY=...                                  # Groq or OpenAI key
STT_BASE_URL=https://api.groq.com/openai/v1      # OpenAI: https://api.openai.com/v1
STT_MODEL=whisper-large-v3-turbo                 # OpenAI: whisper-1
```

The audio is re-encoded to small mono chunks (about 5 MB per 20 minutes), each chunk is
uploaded, and the timestamps are stitched back together, so long videos work and the note is
unchanged. The local backend stays the default and needs no extra account.

## Running on a Claude subscription instead of the API

If you pay for Claude Pro or Max, the bot can use the **Claude Code CLI** on the server for the
two Claude calls (screenshot selection and the note) instead of the metered API. Claude Code's
headless mode is an official feature, the calls count against your subscription's usage limits
rather than a bill, and the notes are the same. Only Claude Code itself may use a subscription
login; the bot therefore runs the `claude` command and never touches the token directly.

Trade-offs: each call adds a few seconds of CLI start-up, screenshots are read from disk by
the CLI rather than sent inline, and a busy day can hit the five-hour usage window (Max has
plenty of room; Pro is tight for screenshot-heavy notes). If a call is rate-limited the job
fails with the CLI's message and you can resend later.

1. **Install Claude Code on the server:** `curl -fsSL https://claude.ai/install.sh | bash`
   (it lands in `~/.local/bin/claude`).
2. **Get a long-lived token** on a machine with a browser: `claude setup-token`, sign in
   with your subscription, copy the token it prints.
3. **In `.env`** on the server:

   ```bash
   NOTE_BACKEND=claude-code
   CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
   CLAUDE_CODE_BIN=/home/ubuntu/.local/bin/claude
   ```

   `ANTHROPIC_API_KEY` can stay for the record, but the bot deliberately does not pass it to
   the CLI, so nothing is billed to it. Restart the bot; the log line `Note backend:
   claude-code` confirms the switch.

## Automatic sync into your vault

Saving the attached file by hand needs no setup. For notes to appear in the vault on their
own, use one of these routes and set `SEND_NOTE_FILE=false`.

**Option A: Remotely Save plugin + an S3 bucket (free, no Obsidian on the server).** The
[Remotely Save](https://github.com/remotely-save/remotely-save) community plugin syncs an
Obsidian vault with cloud storage on iPhone, Android, Mac, and desktop. The bot uploads each
note to the bucket; your devices pull it into the vault. Storage costs cents per month.

1. **AWS console.** Create a private S3 bucket (say `my-vault`). Create an IAM policy that
   allows `s3:ListBucket` on the bucket and `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`
   on `bucket/*`, attach it to a new IAM user, and create an access key for that user.
2. **Server.** Install the AWS CLI (Ubuntu 24.04 has no `awscli` package; use
   [Amazon's installer](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)),
   run `aws configure` with the key and the bucket's region, then in `.env`:

   ```bash
   AFTER_NOTE_COMMAND=aws s3 cp {path} s3://my-vault/{relpath}
   VAULT_LIST_COMMAND=aws s3 ls s3://my-vault --recursive
   SEND_NOTE_FILE=false
   ```

   The first line uploads each note into its folder; the second lets the bot see the vault's
   folders when filing. Restart the bot. A failed upload is reported in the status message;
   the note stays on the server.
3. **Devices.** In Obsidian, install Remotely Save, choose S3, enter the endpoint
   `https://s3.<region>.amazonaws.com`, the region, the access key and secret, and the bucket
   name; run a sync; turn on auto sync. Notes appear under `Knowledge/<Topic>/`.

Remotely Save also speaks Dropbox, OneDrive, Google Drive, and WebDAV; with
[rclone](https://rclone.org/) on the server the same one-liner works for those, e.g.
`AFTER_NOTE_COMMAND=rclone copyto {path} dropbox:Vault/{relpath}` and
`VAULT_LIST_COMMAND=rclone lsf -R --files-only dropbox:Vault`.

**Option B: [Syncthing](https://syncthing.net/), free and peer-to-peer.** Run Syncthing on
the server (`sudo apt install syncthing`, `sudo systemctl enable --now syncthing@$USER`, web UI
on `localhost:8384` through an SSH tunnel), share the vault folder, and set
`OBSIDIAN_VAULT_PATH` to a subfolder of it. Desktop clients are free; on iOS use Möbius Sync
(a one-time paid app) and point it at the Obsidian vault folder through the Files picker.
Notes reach the phone whenever Möbius Sync runs.

**Option C: Obsidian Sync (paid).** Obsidian Sync only works inside the Obsidian app, so run
the desktop app on the server on a virtual display, signed in, with the vault open:
`sudo bash deploy/obsidian-server.sh` installs Obsidian (x86 `.deb` or ARM AppImage), the
display, and two systemd services, and prints the one-time VNC sign-in steps. Needs about
500 MB extra RAM. Then set `OBSIDIAN_VAULT_PATH=/home/ubuntu/vault/YouTube`.

## Customising the notes

- **Template and prompts** are the constants at the top of `bot.py`. In `NOTE_TEMPLATE` the
  bot fills `{title}`, `{url}`, `{channel}`, `{published}`, `{date}`, and `{duration}` from
  real metadata; every other `{placeholder}` is written by Claude, and `{timestamp link}`
  becomes a link to that moment. `SYSTEM_PROMPT` holds the rules, `CUE_PROMPT` decides which
  moments get a screenshot.
- **Check a change across genres** before trusting it. `eval/eval_batch.py` runs eight real
  videos (tech, crypto, economics, interview, science, finance, business, health) through the
  full pipeline into `eval/vault/` with metrics in `eval/results.json`. Transcripts and
  screenshots are cached under `eval/cache/`, so after the first full run a prompt tweak
  re-checks every genre with only the Claude calls, about four minutes:

  ```bash
  .venv/bin/python eval/eval_batch.py                       # full run (uses cache when present)
  .venv/bin/python eval/eval_batch.py --notes-only          # only regenerate the notes
  .venv/bin/python eval/eval_batch.py --notes-only crypto   # one genre
  ```

- **Tests** need no network or API key and run in CI on every push:

  ```bash
  .venv/bin/python tests/test_helpers.py
  .venv/bin/python tests/test_stubs.py
  ```

## Limitations and troubleshooting

- **English only** with the default local model. For other languages set
  `WHISPER_MODEL=large-v3` (slower) and remove `language="en"` in `transcribe_audio_local`,
  or use the API backend, which handles any language.
- **Long videos get screenshots only from the transcript.** Past `FRAMES_SCAN_MAX_MINUTES`,
  a slide deck whose presenter never refers to the slides gets no screenshots. That is the
  trade-off of not sampling a long video on a timer, which wasted tokens on illustrations.
- **Model variance.** Occasionally a note drops a fence or keeps a mis-heard name; the
  structural slips are repaired in code, the rare content-level ones are not.
- **`Sign in to confirm you're not a bot`** from YouTube: see the cookies step under Deploy.
  If it reappears months later, the cookies expired; export again.
- **Downloads suddenly failing:** YouTube changes often; `pip install -U "yt-dlp[default,deno]"`.
- **`No supported JavaScript runtime`** in the log: the `deno` extra did not install; re-run
  the pip command above.
- **Costs** per ten-minute video on the defaults: about 6k input tokens for a talk-only video
  and up to 25k with many screenshots, plus 2 to 4k output, so a few cents on Sonnet; the
  screenshot-selection pass is about a cent; Groq transcription is free-tier or fractions of a
  cent.
- **Secrets.** `.env`, cookies files, and `.pem` keys are gitignored. Keep `.env` at mode 600
  on a server and lock the bot to your user id.

## License

MIT, see [LICENSE](LICENSE).
