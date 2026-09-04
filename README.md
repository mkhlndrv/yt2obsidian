# yt2obsidian

A personal Telegram bot: send it a YouTube link, get back a structured Obsidian note as a
`.md` file in the same chat.

```
YouTube URL ──▶ yt-dlp (audio) ──▶ faster-whisper (transcript) ──▶ Claude (note) ──▶ <title>.md in Telegram
```

Everything is in `bot.py`. The note template and the Claude prompts are the
`NOTE_TEMPLATE`, `SYSTEM_PROMPT`, `USER_PROMPT`, and `CUE_PROMPT` constants at the top of
that file, so the note format is a text edit away.

## Layout

| Path | What |
|---|---|
| `bot.py` | The whole bot: Telegram handlers, yt-dlp download, faster-whisper transcription, Claude screenshot selection and note writing, file delivery. |
| `tests/` | Helper tests plus stub-driven tests of the Telegram and Claude layers; no network or API key needed. |
| `eval/` | Cross-genre evaluation harness: eight real videos through the full pipeline, with cached transcripts and screenshots for fast prompt iteration. |
| `deploy/` | First-boot script for an AWS EC2 instance, and the Obsidian-on-the-server installer for Obsidian Sync. |
| `.env.example` | Every configuration variable with its default. |

## Setup (Ubuntu)

1. **System packages.** `ffmpeg` is required by yt-dlp (and used for audio decoding).

   ```bash
   sudo apt update
   sudo apt install -y ffmpeg python3 python3-venv python3-pip
   python3 --version   # needs 3.11 or newer
   ```

2. **Python environment.**

   ```bash
   cd video_transcript
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

   The `yt-dlp[default,deno]` line pulls in the JavaScript runtime that yt-dlp now needs
   for YouTube (it lands in `.venv/bin/deno`, no system-wide install required).

3. **Telegram bot token.** Message [@BotFather](https://t.me/BotFather), run `/newbot`,
   and copy the token it gives you.

4. **Anthropic API key.** Create one at <https://console.anthropic.com/>.

5. **Config.** Copy the example file and fill it in:

   ```bash
   cp .env.example .env
   nano .env
   ```

   | Variable | Meaning |
   | --- | --- |
   | `TELEGRAM_BOT_TOKEN` | Token from BotFather |
   | `ANTHROPIC_API_KEY` | Anthropic API key |
   | `OBSIDIAN_VAULT_PATH` | Optional. Folder where a copy of every note is kept (default `notes/` next to `bot.py`). Point it inside your vault if the bot runs on the machine that has the vault. |
   | `TELEGRAM_ALLOWED_USER_IDS` | Your Telegram user id(s). Send `/start` to the bot to see yours. Empty means anyone can use the bot. |

   Optional overrides (`CLAUDE_MODEL`, `WHISPER_MODEL`, `WHISPER_DEVICE`,
   `WHISPER_COMPUTE_TYPE`, `MAX_VIDEO_MINUTES`, `YTDLP_COOKIES_FILE`) are listed in `.env.example`.

6. **Run.**

   ```bash
   source .venv/bin/activate
   python bot.py
   ```

   The first start downloads the `distil-large-v3` Whisper model (about 1.5 GB) into
   `~/.cache/huggingface/`. After that the bot logs `Bot is running` and you can send it links.

## Using it

Send the bot any YouTube video link (`youtube.com/watch?v=…`, `youtu.be/…`, Shorts, or
`/live/` links of finished streams). It replies at each step:

1. "Processing started" as soon as the link is accepted.
2. Video title and length once the audio is downloaded.
3. Word count once transcription is done.
4. The finished note as a `.md` file attached to the chat. A copy is also kept in the
   notes folder on the bot's machine.

The note has a summary, key takeaways, timestamped sections that follow the video's own
structure (each heading and quote links to that moment on YouTube), things worth following
up on, quotes, related-topic wikilinks, and, folded at the bottom, the full timestamped
transcript so the whole video is searchable in Obsidian (`INCLUDE_TRANSCRIPT=false` turns
that off).

**Screenshots.** Speech alone misses what is only on screen. After transcription, Claude reads
the timestamped transcript once (the `CUE_PROMPT` constant in `bot.py`, run at low effort) and
returns the moments where the screen carries information the speech doesn't: code, slides,
charts, a website being walked through, and so on, each with a one-line reason. The bot
grabs a frame at each of those moments from a small video-only stream (720p by default, about
15 MB for a ten-minute video), drops near-duplicates, and sends up to 30 frames to the
note-writing call labelled with their timestamps and reasons. Claude uses them to put the
code, slide text, chart, or UI being discussed into the note, and to say explicitly when a
screen reference could not be captured. Videos where nothing on screen matters download no
video stream; the selection pass costs about a cent and each frame adds roughly 800 input
tokens. `INCLUDE_FRAMES=false` turns it off; `CUE_MODEL`, `FRAMES_MAX`, and
`FRAMES_VIDEO_HEIGHT` tune it. If the selection call or the video download fails, the note is
still produced without screenshots.

**Putting the file into Obsidian.** On the iPhone, tap the file in Telegram, tap the share
icon, choose "Save to Files", and pick On My iPhone > Obsidian > your vault (or the vault's
iCloud folder). Obsidian shows the note immediately. On a Mac, drag the file from Telegram
into the vault folder in Finder.

Errors (bad link, private or removed video, transcription failure, Anthropic API problems,
unwritable notes folder) come back as a `❌` message with the reason.

Notes are named after the video title (sanitized). An existing file is never overwritten;
a duplicate title gets ` (2)`, ` (3)`, and so on.

## Run it as a service (optional)

Create `/etc/systemd/system/yt2obsidian.service` (adjust user and paths):

```ini
[Unit]
Description=yt2obsidian Telegram bot
Wants=network-online.target
After=network-online.target

[Service]
User=you
WorkingDirectory=/home/you/video_transcript
ExecStart=/home/you/video_transcript/.venv/bin/python bot.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now yt2obsidian
journalctl -u yt2obsidian -f
```

## Deploy on AWS (EC2)

The bot is a single long-running process that polls Telegram, so it wants one small
always-on Linux VM. It needs no inbound ports at all; all traffic is outbound.

1. **Launch an instance** in the EC2 console: Ubuntu Server 24.04 LTS, 2 vCPU and 4 GB RAM or
   more (`t4g.medium` on ARM is the cheapest sensible choice and everything here ships ARM
   wheels; `t3.medium` is the x86 equivalent; the 1 GB free-tier sizes cannot hold the model),
   a 20 GB disk, a key pair you can SSH with, and a security group that allows SSH from your
   IP only.
2. **Paste the first-boot script** from [`deploy/ec2-user-data.sh`](deploy/ec2-user-data.sh)
   into *Advanced details → User data* before launching. It installs ffmpeg and Python, clones
   this repo into `/home/ubuntu/yt2obsidian`, installs the requirements, downloads the Whisper
   model, and registers a systemd service. Give it about five minutes after boot;
   `sudo tail -f /var/log/cloud-init-output.log` shows progress.
3. **Configure and start.** SSH in as `ubuntu`, then:

   ```bash
   cd ~/yt2obsidian
   cp .env.example .env && nano .env      # bot token, Anthropic key, your Telegram user id
   chmod 600 .env
   sudo systemctl start yt2obsidian
   journalctl -u yt2obsidian -f           # wait for "Bot is running"
   ```

   The service restarts on failure and starts on boot. If you also run the bot elsewhere,
   stop that copy first: Telegram lets only one process poll a bot token.

4. **Pick the transcription speed you want.** With two vCPUs and local Whisper, expect
   roughly realtime (a ten-minute video takes about ten minutes). For much faster notes, switch
   to the API backend described in the next section; then a `t4g.small` is plenty.

**Updating:** `cd ~/yt2obsidian && git pull && sudo systemctl restart yt2obsidian`.

## Faster transcription

Transcription is the slow step, and on a CPU it scales with cores. Measured on the same
ten-minute video:

| Setup | Time for 10 min of audio |
| --- | --- |
| Local `distil-large-v3`, 12-core laptop, defaults | 129 s |
| Same, `WHISPER_BEAM_SIZE=1` | 118 s |
| Same, faster-whisper batched pipeline | 6278 s (do not use on CPU) |
| Local on a 2-vCPU cloud instance | roughly 600 s (about realtime) |
| API backend (OpenAI `whisper-1` or Groq `whisper-large-v3-turbo`) | typically 15 to 60 s |

The knobs that stay local (`WHISPER_BEAM_SIZE=1`, `WHISPER_CPU_THREADS=<cores>`, a bigger
instance, or a GPU instance with `WHISPER_DEVICE=cuda`) give at most tens of percent per step
or cost several times more per month. The API backend is the big win: set in `.env`

```bash
TRANSCRIBER=api
STT_API_KEY=...                                  # OpenAI or Groq key
STT_BASE_URL=https://api.openai.com/v1           # Groq: https://api.groq.com/openai/v1
STT_MODEL=whisper-1                              # Groq: whisper-large-v3-turbo
```

and restart. The audio is re-encoded to small mono chunks (about 5 MB per 20 minutes), each
chunk is uploaded, and the timestamps are stitched back together, so long videos work and the
note format is unchanged. Cost is on the order of half a cent per minute of audio at OpenAI
and a fraction of that at Groq, which also has a free tier. With this backend the server no
longer needs the 1.5 GB model or a capable CPU. The local backend remains the default and
needs no extra account.

**Cost:** a `t4g.medium` runs in the region of $25 a month on-demand and a `t3.medium` about
$30; the API calls add a few cents per video. Check the EC2 pricing page for your region.

## Automatic sync into your vault

Saving the Telegram file by hand needs no setup. To have notes appear in the vault on their
own, use one of the routes below and set `SEND_NOTE_FILE=false` so the bot just confirms
each note instead of attaching it.

**Option A: Remotely Save plugin + an S3 bucket (free, recommended on AWS).** The
[Remotely Save](https://github.com/remotely-save/remotely-save) community plugin syncs an
Obsidian vault with cloud storage and runs on iPhone, Mac, and desktop. The server side needs
no Obsidian at all: the bot uploads each finished note to the bucket, and your devices pull it
into the vault. S3 storage for a vault costs cents per month.

1. **Bucket and keys.** In the AWS console create a private S3 bucket (say `my-vault`) and an
   IAM user whose only permission is that bucket; download its access key. On the server,
   the simplest is an instance role with access to the bucket, otherwise `aws configure` with
   the same key.
2. **Server.** `sudo apt install -y awscli`, then in `.env`:

   ```bash
   AFTER_NOTE_COMMAND=aws s3 cp {path} s3://my-vault/YouTube/
   SEND_NOTE_FILE=false
   ```

   and restart the bot. Every note now lands in `YouTube/` in the bucket. A failed upload
   comes back as a Telegram warning; the note is still saved on the server.
3. **Devices.** In Obsidian on the phone and the Mac install Remotely Save, choose S3, enter
   the endpoint `https://s3.<region>.amazonaws.com`, the region, the access key, the secret,
   and the bucket name, then run a sync and turn on auto-sync in its settings. New notes
   appear under `YouTube/` in the vault.

Remotely Save also speaks Dropbox, OneDrive, Google Drive, and WebDAV; with
[rclone](https://rclone.org/) on the server, the same one-liner works for those:
`AFTER_NOTE_COMMAND=rclone copy {path} dropbox:Vault/YouTube`.

**Option B: [Syncthing](https://syncthing.net/), free and peer-to-peer.** Syncs the vault
folder directly between devices; the iOS client, Möbius Sync, is a one-time paid app:

1. **Server.** Install and start Syncthing for your user:

   ```bash
   sudo apt install -y syncthing
   sudo systemctl enable --now syncthing@$USER
   ```

   Open its web UI from your laptop through an SSH tunnel:

   ```bash
   ssh -L 8384:localhost:8384 you@server   # then browse to http://localhost:8384
   ```

   Add a folder for the vault root, for example `/home/you/vault`, and set
   `OBSIDIAN_VAULT_PATH=/home/you/vault/YouTube` in `.env`. Note the server's Device ID
   (Actions -> Show ID).

2. **iPhone.** In Obsidian, create a new vault stored on the phone (leave "Store in iCloud"
   off). Install Möbius Sync from the App Store. In Möbius Sync add the server as a device
   (scan the QR code from the server's web UI), accept the device on the server side, then
   accept the shared vault folder on the phone. When Möbius asks where to put it, pick the
   Obsidian vault folder through the Files picker (On My iPhone -> Obsidian -> your vault).

3. **Mac (optional).** `brew install syncthing`, share the same folder, and open it with
   Obsidian on the desktop for editing.

Syncthing creates a small `.stfolder` marker inside the vault; Obsidian ignores it. New notes
appear on the phone when Möbius Sync runs, which is whenever the app is open plus the short
background windows iOS grants it.

**Option C: Obsidian Sync (paid subscription).** Obsidian Sync only works inside the Obsidian
app, so this route runs the desktop app on the server on a virtual display, signed in, with
the vault open; it then pushes notes to your devices natively, background sync included.
`sudo bash deploy/obsidian-server.sh` installs Obsidian (x86 `.deb` or ARM AppImage), the
virtual display, and two systemd services, and prints the one-time VNC sign-in steps. Needs
about 500 MB of extra RAM on the server. Afterwards set
`OBSIDIAN_VAULT_PATH=/home/ubuntu/vault/YouTube` and check once that the sign-in survives
`sudo systemctl restart obsidian`.

## Development

Tests need no network or API key; they exercise the helpers and drive the Telegram and
Claude layers with stubs:

```bash
.venv/bin/python tests/test_helpers.py
.venv/bin/python tests/test_stubs.py
```

`eval/eval_batch.py` runs eight real videos of different genres (tech, crypto, economics,
interview, science, finance, business, health) through the full pipeline and writes the notes
to `eval/vault/` with metrics in `eval/results.json`. Transcripts and screenshots are cached
under `eval/cache/`, so after the first full run a prompt change can be re-checked across all
genres with only the Claude calls:

```bash
.venv/bin/python eval/eval_batch.py                       # full run (uses cache when present)
.venv/bin/python eval/eval_batch.py --notes-only          # only regenerate the notes
.venv/bin/python eval/eval_batch.py --notes-only crypto   # one genre
```

## Notes and troubleshooting

- **Speed.** Local transcription runs on the CPU with `int8` weights: about 4.5x realtime on
  a 12-core laptop, about realtime on a 2-vCPU cloud instance, and several times slower on a
  busy machine. See "Faster transcription" above for the API backend. Jobs are transcribed one
  at a time; the bot stays responsive and tells you when a job is queued.
- **`Sign in to confirm you're not a bot` / HTTP 403 from YouTube.** Export your browser
  cookies to a Netscape-format `cookies.txt` (for example with the "Get cookies.txt LOCALLY"
  browser extension) and set `YTDLP_COOKIES_FILE` to its path.
- **Downloads suddenly failing.** YouTube changes often; update yt-dlp:
  `pip install -U "yt-dlp[default,deno]"`.
- **`No supported JavaScript runtime could be found` in the log.** The `deno` extra did not
  install. Either re-run the pip command above or install deno system-wide
  (`curl -fsSL https://deno.land/install.sh | sh`) and make sure it is on the bot's `PATH`.
- **GPU.** With an NVIDIA GPU and CUDA libraries installed, set `WHISPER_DEVICE=cuda` and
  `WHISPER_COMPUTE_TYPE=float16` in `.env`.
- **Other languages.** `distil-large-v3` is English-only. For other languages set
  `WHISPER_MODEL=large-v3` (slower) and remove `language="en"` in `transcribe_audio` so
  Whisper auto-detects the language.
- **Changing the note format.** Edit `NOTE_TEMPLATE` in `bot.py`. The bot fills
  `{title}`, `{url}`, `{channel}`, `{published}`, `{date}`, and `{duration}` from real video
  metadata; every other `{placeholder}` is written by Claude, and `{timestamp link}` becomes
  a link to that moment in the video. Prompt wording lives in `SYSTEM_PROMPT` and
  `USER_PROMPT` right below it. There is deliberately no target word count: the prompt asks
  for length to follow how dense the content is.
