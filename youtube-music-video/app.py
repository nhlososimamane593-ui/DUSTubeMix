```python
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

import gradio as gr

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload


# ============================================================
# Configuration
# ============================================================

APP_NAME = "DUSTubeMix"

BASE_DIR = Path(__file__).resolve().parent

CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"

OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload"
]

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080

COVER_SIZE = 800

FPS = 30

WAVEFORM_WIDTH = 1800
WAVEFORM_HEIGHT = 120

BAR_X = 60
BAR_Y = 1035
BAR_WIDTH = 1800
BAR_HEIGHT = 10


# ============================================================
# Errors
# ============================================================

class AppError(Exception):
    """Expected application error."""


# ============================================================
# System utilities
# ============================================================

def check_binary(binary: str) -> bool:
    return shutil.which(binary) is not None


def validate_ffmpeg() -> None:
    missing = []

    if not check_binary(FFMPEG):
        missing.append("ffmpeg")

    if not check_binary(FFPROBE):
        missing.append("ffprobe")

    if missing:
        raise AppError(
            "FFmpeg is not installed or is not available on PATH.\n\n"
            f"Missing: {', '.join(missing)}\n\n"
            "Install FFmpeg and restart the terminal/application."
        )


def run_command(command: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if result.returncode != 0:
        raise AppError(
            "Command failed:\n\n"
            + " ".join(command)
            + "\n\n"
            + result.stderr[-10000:]
        )

    return result


# ============================================================
# Media information
# ============================================================

def get_audio_duration(audio_path: str) -> float:
    """Return MP3 duration in seconds."""

    result = run_command(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ]
    )

    value = result.stdout.strip()

    try:
        duration = float(value)
    except ValueError as exc:
        raise AppError(
            f"Could not determine audio duration.\n\nffprobe returned:\n{value}"
        ) from exc

    if duration <= 0:
        raise AppError("The MP3 duration is invalid.")

    return duration


def validate_audio(audio_path: str) -> None:
    if not Path(audio_path).exists():
        raise AppError("The selected MP3 file does not exist.")

    if Path(audio_path).suffix.lower() != ".mp3":
        raise AppError("Please select an MP3 file.")


def validate_cover(cover_path: str) -> None:
    if not Path(cover_path).exists():
        raise AppError("The selected cover image does not exist.")

    if Path(cover_path).suffix.lower() not in {".jpg", ".jpeg"}:
        raise AppError("Please select a JPG/JPEG cover image.")


# ============================================================
# FFmpeg video generation
# ============================================================

def build_filter_graph(duration: float) -> str:
    """
    Build the FFmpeg filter graph.

    The important fix here is the progress bar.

    drawbox's `t` is thickness, NOT timestamp, so the old
    implementation could not use:

        w=1800*t/duration

    Instead we create a white bar as a video source and use
    the scale filter with eval=frame. FFmpeg's scale filter
    exposes the current frame timestamp as `t` when evaluated
    per frame.
    """

    # We deliberately use a lavfi color source for the progress bar.
    #
    # It starts as 1800x10 and is dynamically scaled horizontally.
    #
    # max(1, ...) prevents zero-width frames at t=0.

    progress_width = (
        f"max(1,min({BAR_WIDTH},"
        f"{BAR_WIDTH}*t/{duration:.6f}))"
    )

    graph = f"""
[1:v]
scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,
crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},
boxblur=30:15,
setsar=1
[bg];

[1:v]
scale={COVER_SIZE}:{COVER_SIZE}:force_original_aspect_ratio=decrease,
pad={COVER_SIZE}:{COVER_SIZE}:(ow-iw)/2:(oh-ih)/2:color=black,
setsar=1
[cover];

[0:a]
showwaves=
s={WAVEFORM_WIDTH}x{WAVEFORM_HEIGHT}:
mode=cline:
rate={FPS}:
colors=white:
scale=sqrt:
draw=full,
format=rgba
[wave];

[bg][cover]
overlay=
x=(W-w)/2:
y=(H-h)/2-45:
shortest=1
[art];

[art][wave]
overlay=
x=(W-w)/2:
y=870:
shortest=1
[wavevideo];

color=
c=white:
s={BAR_WIDTH}x{BAR_HEIGHT}:
r={FPS}:
d={duration:.6f}
[progress_source];

[progress_source]
scale=
w={progress_width}:
h={BAR_HEIGHT}:
eval=frame
[progress];

[wavevideo]
drawbox=
x={BAR_X}:
y={BAR_Y}:
w={BAR_WIDTH}:
h={BAR_HEIGHT}:
color=white@0.25:
t=fill
[track];

[track][progress]
overlay=
x={BAR_X}:
y={BAR_Y}:
eof_action=pass:
shortest=0
[vout]
"""

    return ",".join(
        line.strip()
        for line in graph.splitlines()
        if line.strip()
    )


def create_video(
    audio_path: str,
    cover_path: str,
    output_path: str,
    progress: Callable | None = None,
) -> str:

    validate_ffmpeg()

    duration = get_audio_duration(audio_path)

    if progress:
        progress(
            0.02,
            desc=f"Audio length: {duration:.2f} seconds",
        )

    filter_graph = build_filter_graph(duration)

    command = [
        FFMPEG,

        "-hide_banner",
        "-y",

        # ----------------------------------------------------
        # Audio
        # ----------------------------------------------------

        "-i",
        audio_path,

        # ----------------------------------------------------
        # Cover
        # ----------------------------------------------------

        "-loop",
        "1",

        "-framerate",
        str(FPS),

        "-i",
        cover_path,

        # ----------------------------------------------------
        # Filter graph
        # ----------------------------------------------------

        "-filter_complex",
        filter_graph,

        "-map",
        "[vout]",

        # Original MP3 stream.
        "-map",
        "0:a:0",

        # ----------------------------------------------------
        # Video encoder
        # ----------------------------------------------------

        "-c:v",
        "libx264",

        "-preset",
        "medium",

        "-crf",
        "18",

        "-pix_fmt",
        "yuv420p",

        "-r",
        str(FPS),

        # ----------------------------------------------------
        # IMPORTANT:
        # Keep original MP3 stream.
        # ----------------------------------------------------

        "-c:a",
        "copy",

        # ----------------------------------------------------
        # Exact duration
        # ----------------------------------------------------

        "-t",
        f"{duration:.6f}",

        # ----------------------------------------------------
        # MP4 optimization
        # ----------------------------------------------------

        "-movflags",
        "+faststart",

        output_path,
    ]

    if progress:
        progress(
            0.05,
            desc="Starting FFmpeg...",
        )

    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    stderr_lines: list[str] = []

    while True:
        line = process.stderr.readline()

        if not line:
            break

        stderr_lines.append(line)

        # FFmpeg prints useful progress information such as:
        #
        # time=00:01:23.45
        #
        # Parse it for a better Gradio progress display.
        if "time=" in line and progress:
            try:
                time_text = line.split("time=", 1)[1].split()[0]

                parts = time_text.split(":")

                if len(parts) == 3:
                    hours = float(parts[0])
                    minutes = float(parts[1])
                    seconds = float(parts[2])

                    current = (
                        hours * 3600
                        + minutes * 60
                        + seconds
                    )

                    percent = min(
                        0.98,
                        max(
                            0.05,
                            current / duration,
                        ),
                    )

                    progress(
                        percent,
                        desc=(
                            f"Rendering: "
                            f"{current:.1f} / "
                            f"{duration:.1f} seconds"
                        ),
                    )

            except Exception:
                pass

    return_code = process.wait()

    stderr = "".join(stderr_lines)

    if return_code != 0:
        raise AppError(
            "FFmpeg failed.\n\n"
            + stderr[-12000:]
        )

    if not Path(output_path).exists():
        raise AppError(
            "FFmpeg finished but the output video was not created."
        )

    if Path(output_path).stat().st_size == 0:
        raise AppError("FFmpeg created an empty video file.")

    if progress:
        progress(
            1.0,
            desc="Video rendering complete.",
        )

    return output_path


# ============================================================
# YouTube OAuth
# ============================================================

def get_youtube_service():
    if not CREDENTIALS_FILE.exists():
        raise AppError(
            "credentials.json was not found.\n\n"
            f"Expected location:\n{CREDENTIALS_FILE}\n\n"
            "Download your OAuth Desktop App credentials "
            "from Google Cloud Console."
        )

    credentials: Credentials | None = None

    if TOKEN_FILE.exists():
        try:
            credentials = Credentials.from_authorized_user_file(
                str(TOKEN_FILE),
                SCOPES,
            )
        except Exception:
            credentials = None

    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except Exception:
            credentials = None

    if not credentials or not credentials.valid:

        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_FILE),
            SCOPES,
        )

        credentials = flow.run_local_server(
            port=0,
            access_type="offline",
            prompt="consent",
        )

        TOKEN_FILE.write_text(
            credentials.to_json(),
            encoding="utf-8",
        )

    return build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )


# ============================================================
# YouTube upload
# ============================================================

def parse_tags(tags_text: str) -> list[str]:
    if not tags_text:
        return []

    return [
        tag.strip()
        for tag in tags_text.split(",")
        if tag.strip()
    ]


def upload_to_youtube(
    video_path: str,
    title: str,
    description: str,
    tags_text: str,
    privacy_status: str,
    progress: Callable | None = None,
) -> str:

    title = (title or "").strip()
    description = (description or "").strip()

    if not title:
        raise AppError("YouTube title is required.")

    if len(title) > 100:
        raise AppError(
            f"YouTube title is {len(title)} characters. "
            "Maximum is 100 characters."
        )

    if privacy_status not in {
        "private",
        "unlisted",
        "public",
    }:
        raise AppError("Invalid YouTube privacy status.")

    tags = parse_tags(tags_text)

    if progress:
        progress(
            0.02,
            desc="Authorizing YouTube...",
        )

    youtube = get_youtube_service()

    snippet = {
        "title": title,
        "description": description,
        "categoryId": "10",
    }

    # IMPORTANT:
    # Do NOT send tags=[].
    #
    # YouTube's API documentation indicates that an empty tags
    # value can result in a 400 invalid request.
    if tags:
        snippet["tags"] = tags

    body = {
        "snippet": snippet,
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    file_size = Path(video_path).stat().st_size

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        chunksize=8 * 1024 * 1024,
        resumable=True,
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    if progress:
        progress(
            0.05,
            desc=(
                f"Uploading {file_size / 1024 / 1024:.1f} MB "
                "to YouTube..."
            ),
        )

    response = None

    while response is None:
        try:
            status, response = request.next_chunk()

            if status and progress:
                upload_percent = status.progress()

                progress(
                    0.05 + upload_percent * 0.94,
                    desc=(
                        "YouTube upload: "
                        f"{upload_percent * 100:.1f}%"
                    ),
                )

        except HttpError as exc:
            message = getattr(exc, "content", None)

            if message:
                try:
                    message = message.decode(
                        "utf-8",
                        errors="replace",
                    )
                except Exception:
                    pass

            raise AppError(
                "YouTube API rejected the upload.\n\n"
                f"HTTP status: {exc.resp.status}\n\n"
                f"{message or str(exc)}"
            ) from exc

        except Exception as exc:
            raise AppError(
                f"YouTube upload failed:\n\n{exc}"
            ) from exc

    if not response or "id" not in response:
        raise AppError(
            "YouTube did not return a video ID."
        )

    video_id = response["id"]

    if progress:
        progress(
            1.0,
            desc="YouTube upload complete.",
        )

    return video_id


# ============================================================
# Main application
# ============================================================

def generate_and_upload(
    audio_file,
    cover_file,
    title,
    description,
    tags,
    privacy_status,
    progress=gr.Progress(),
):

    try:

        if not audio_file:
            raise AppError("Please select an MP3 file.")

        if not cover_file:
            raise AppError(
                "Please select a JPG/JPEG cover image."
            )

        audio_path = str(audio_file)
        cover_path = str(cover_file)

        validate_audio(audio_path)
        validate_cover(cover_path)

        if not title or not title.strip():
            raise AppError(
                "Please enter a YouTube title."
            )

        validate_ffmpeg()

        progress(
            0.0,
            desc="Preparing files...",
        )

        with tempfile.TemporaryDirectory(
            prefix="dustubemix_"
        ) as temp_directory:

            temp_directory = Path(temp_directory)

            temporary_video = (
                temp_directory / "music_video.mp4"
            )

            # ------------------------------------------------
            # Generate video
            # ------------------------------------------------

            create_video(
                audio_path=audio_path,
                cover_path=cover_path,
                output_path=str(temporary_video),
                progress=progress,
            )

            # ------------------------------------------------
            # Copy to permanent output directory
            # ------------------------------------------------

            safe_name = (
                Path(audio_path)
                .stem
                .replace(" ", "_")
            )

            final_path = (
                OUTPUT_DIR
                / f"{safe_name}_youtube.mp4"
            )

            shutil.copy2(
                temporary_video,
                final_path,
            )

            # ------------------------------------------------
            # Upload to YouTube
            # ------------------------------------------------

            video_id = upload_to_youtube(
                video_path=str(final_path),
                title=title,
                description=description,
                tags_text=tags,
                privacy_status=privacy_status,
                progress=progress,
            )

            youtube_url = (
                "https://www.youtube.com/watch?v="
                + video_id
            )

            result = f"""
## ✅ Upload complete

**Video ID:** `{video_id}`

**YouTube URL:**  
{youtube_url}

**Local MP4:**  
`{final_path}`

**Resolution:** 1920 × 1080

**Audio:** Original MP3 stream copied without re-encoding
"""

            return (
                str(final_path),
                result,
            )

    except AppError as exc:
        raise gr.Error(str(exc))

    except Exception as exc:
        raise gr.Error(
            "Unexpected error:\n\n"
            f"{type(exc).__name__}: {exc}"
        )


# ============================================================
# Gradio UI
# ============================================================

DESCRIPTION = """
# 🎵 DUSTubeMix

Create a YouTube-ready music video from an MP3 and JPG cover.

### Video

- 1920 × 1080
- Blurred cover background
- Centered 800 × 800 artwork
- Animated waveform
- Animated progress bar
- Video duration follows the MP3
- Original MP3 audio stream is copied without another MP3 encode

### YouTube

- OAuth 2.0
- Title
- Description
- Tags
- Private / Unlisted / Public
- Resumable upload
"""


with gr.Blocks(
    title=APP_NAME,
    theme=gr.themes.Soft(),
) as demo:

    gr.Markdown(DESCRIPTION)

    with gr.Row():

        with gr.Column():

            audio_input = gr.File(
                label="MP3 Audio",
                file_types=[".mp3"],
                type="filepath",
            )

            cover_input = gr.File(
                label="JPG Cover",
                file_types=[".jpg", ".jpeg"],
                type="filepath",
            )

        with gr.Column():

            title_input = gr.Textbox(
                label="YouTube Title",
                placeholder="Enter YouTube title...",
                max_lines=1,
            )

            description_input = gr.Textbox(
                label="YouTube Description",
                placeholder="Enter description...",
                lines=7,
            )

            tags_input = gr.Textbox(
                label="YouTube Tags",
                placeholder=(
                    "music, new music, official audio"
                ),
                info="Separate tags with commas.",
            )

            privacy_input = gr.Dropdown(
                label="Privacy",
                choices=[
                    "private",
                    "unlisted",
                    "public",
                ],
                value="private",
            )

    generate_button = gr.Button(
        "🎬 Generate Video & Upload",
        variant="primary",
        size="lg",
    )

    output_video = gr.File(
        label="Generated Video",
    )

    result_output = gr.Markdown(
        label="Result",
    )

    generate_button.click(
        fn=generate_and_upload,
        inputs=[
            audio_input,
            cover_input,
            title_input,
            description_input,
            tags_input,
            privacy_input,
        ],
        outputs=[
            output_video,
            result_output,
        ],
    )


# ============================================================
# Start
# ============================================================

if __name__ == "__main__":

    validate_ffmpeg()

    demo.launch(
        server_name=os.getenv(
            "GRADIO_SERVER_NAME",
            "127.0.0.1",
        ),
        server_port=int(
            os.getenv(
                "GRADIO_SERVER_PORT",
                "7860",
            )
        ),
        show_error=True,
    )
```
