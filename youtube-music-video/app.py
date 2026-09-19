import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

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

APP_NAME = "YouTube Music Video Generator"

BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"
OUTPUT_DIR = BASE_DIR / "output"

OUTPUT_DIR.mkdir(exist_ok=True)

# OAuth scope required for uploading videos.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload"
]

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080

COVER_SIZE = 800

WAVEFORM_WIDTH = 1800
WAVEFORM_HEIGHT = 150

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


# ============================================================
# Utility functions
# ============================================================

def check_command(command: str) -> bool:
    """Check whether a command exists on PATH."""
    return shutil.which(command) is not None


def validate_dependencies():
    """Ensure FFmpeg and ffprobe are installed."""
    missing = []

    if not check_command(FFMPEG):
        missing.append("ffmpeg")

    if not check_command(FFPROBE):
        missing.append("ffprobe")

    if missing:
        raise RuntimeError(
            "Missing required system dependencies: "
            + ", ".join(missing)
            + ". Install FFmpeg and make sure it is on your PATH."
        )


def run_command(command: list[str]) -> subprocess.CompletedProcess:
    """
    Execute a subprocess and raise a useful exception on failure.
    """
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Command failed:\n\n"
            + " ".join(command)
            + "\n\n"
            + result.stderr[-5000:]
        )

    return result


def get_audio_duration(audio_path: str) -> float:
    """Get MP3 duration using ffprobe."""
    command = [
        FFPROBE,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]

    result = run_command(command)

    try:
        duration = float(result.stdout.strip())
    except ValueError:
        raise RuntimeError("Could not determine the MP3 duration.")

    if duration <= 0:
        raise RuntimeError("The MP3 has an invalid duration.")

    return duration


def escape_filter_path(path: str) -> str:
    """
    Escape a path for use inside an FFmpeg filter expression.
    """
    return (
        path.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


# ============================================================
# Video generation
# ============================================================

def create_video(
    audio_path: str,
    cover_path: str,
    output_path: str,
    progress=None,
) -> str:
    """
    Create a 1920x1080 music video.

    Layout:

    ┌───────────────────────────────────────────────┐
    │                                               │
    │            blurred cover background           │
    │                                               │
    │             ┌─────────────────┐               │
    │             │                 │               │
    │             │   800x800       │               │
    │             │   COVER         │               │
    │             │                 │               │
    │             └─────────────────┘               │
    │                                               │
    │       ─────── audio waveform ─────────        │
    │                                               │
    │ ███████████████████░░░░░░░░░░░░░░░░░░░       │
    └───────────────────────────────────────────────┘

    Audio is copied directly from the source MP3.
    """

    validate_dependencies()

    duration = get_audio_duration(audio_path)

    if progress:
        progress(0.05, desc=f"Audio duration: {duration:.2f} seconds")

    # Avoid paths with problematic characters in filter expressions.
    cover_filter_path = escape_filter_path(cover_path)

    # The filter graph creates:
    #
    # 1. Blurred 1920x1080 background
    # 2. Centered 800x800 cover
    # 3. Waveform generated from the original audio
    # 4. Progress bar
    #
    # The original audio stream is mapped separately and copied
    # without re-encoding.
    filter_complex = (
        # ----------------------------------------------------
        # Blurred background
        # ----------------------------------------------------
        f"[1:v]"
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
        f"force_original_aspect_ratio=increase,"
        f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
        f"boxblur=20:10"
        f"[bg];"

        # ----------------------------------------------------
        # Main 800x800 cover
        # ----------------------------------------------------
        f"[1:v]"
        f"scale={COVER_SIZE}:{COVER_SIZE}:"
        f"force_original_aspect_ratio=decrease,"
        f"pad={COVER_SIZE}:{COVER_SIZE}:"
        f"(ow-iw)/2:(oh-ih)/2:color=black"
        f"[cover];"

        # ----------------------------------------------------
        # Audio waveform
        # ----------------------------------------------------
        f"[0:a]"
        f"showwaves="
        f"s={WAVEFORM_WIDTH}x{WAVEFORM_HEIGHT}:"
        f"mode=cline:"
        f"rate=30:"
        f"colors=white,"
        f"format=rgba"
        f"[wave];"

        # ----------------------------------------------------
        # Background + cover
        # ----------------------------------------------------
        f"[bg][cover]"
        f"overlay="
        f"x=(W-w)/2:"
        f"y=(H-h)/2-40"
        f"[base];"

        # ----------------------------------------------------
        # Waveform
        # ----------------------------------------------------
        f"[base][wave]"
        f"overlay="
        f"x=(W-w)/2:"
        f"y=865"
        f"[withwave];"

        # ----------------------------------------------------
        # Progress bar background
        # ----------------------------------------------------
        f"[withwave]"
        f"drawbox="
        f"x=60:"
        f"y=1035:"
        f"w=1800:"
        f"h=10:"
        f"color=white@0.25:"
        f"t=fill"
        f"[barbase];"

        # ----------------------------------------------------
        # Animated progress
        #
        # FFmpeg's `t` is current video time.
        # ----------------------------------------------------
        f"[barbase]"
        f"drawbox="
        f"x=60:"
        f"y=1035:"
        f"w='min(1800,1800*t/{duration})':"
        f"h=10:"
        f"color=white:"
        f"t=fill"
        f"[vout]"
    )

    command = [
        FFMPEG,
        "-y",

        # Input audio
        "-i",
        audio_path,

        # Input cover
        "-loop",
        "1",
        "-i",
        cover_path,

        # Filtered video
        "-filter_complex",
        filter_complex,

        # Output video
        "-map",
        "[vout]",

        # Original MP3 audio stream.
        #
        # This means FFmpeg does not decode/re-encode the audio
        # for the final output.
        "-map",
        "0:a:0",

        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",

        # Make the video widely compatible.
        "-pix_fmt",
        "yuv420p",

        # IMPORTANT:
        # Keep the original MP3 stream.
        "-c:a",
        "copy",

        # Explicit duration matching the source MP3.
        "-t",
        f"{duration:.6f}",

        # MP4 metadata / streaming compatibility.
        "-movflags",
        "+faststart",

        output_path,
    ]

    if progress:
        progress(0.10, desc="Rendering video with FFmpeg...")

    # Run FFmpeg.
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    # FFmpeg progress parsing could be added here. For simplicity,
    # we wait for completion and update the UI afterwards.
    stdout, stderr = process.communicate()

    if process.returncode != 0:
        raise RuntimeError(
            "FFmpeg failed:\n\n"
            + stderr[-8000:]
        )

    if not Path(output_path).exists():
        raise RuntimeError("FFmpeg completed but no output video was created.")

    if progress:
        progress(1.0, desc="Video rendering complete.")

    return output_path


# ============================================================
# YouTube OAuth
# ============================================================

def get_youtube_service():
    """
    Authenticate with YouTube using OAuth 2.0.

    credentials.json:
        OAuth client credentials downloaded from Google Cloud.

    token.json:
        Cached user authorization generated after first login.
    """

    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(
            "credentials.json was not found.\n\n"
            "Download your OAuth 2.0 Desktop App credentials from "
            "Google Cloud Console and place the file next to app.py."
        )

    credentials: Optional[Credentials] = None

    # Load previously authorized credentials.
    if TOKEN_FILE.exists():
        try:
            credentials = Credentials.from_authorized_user_file(
                str(TOKEN_FILE),
                SCOPES,
            )
        except Exception:
            credentials = None

    # Refresh expired token when possible.
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

    # First-time OAuth authorization.
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

        # Save token for future uploads.
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
    """
    Convert comma-separated tags into a list.

    Example:
        music, electronic, chill, ambient

    becomes:
        ["music", "electronic", "chill", "ambient"]
    """

    if not tags_text:
        return []

    tags = [
        tag.strip()
        for tag in tags_text.split(",")
        if tag.strip()
    ]

    return tags


def upload_to_youtube(
    video_path: str,
    title: str,
    description: str,
    tags_text: str,
    privacy_status: str,
    progress=None,
) -> str:
    """
    Upload generated video to YouTube.
    """

    if not title.strip():
        raise ValueError("YouTube video title is required.")

    if len(title) > 100:
        raise ValueError("YouTube titles can contain at most 100 characters.")

    tags = parse_tags(tags_text)

    if progress:
        progress(0.05, desc="Authorizing YouTube...")

    youtube = get_youtube_service()

    body = {
        "snippet": {
            "title": title.strip(),
            "description": description.strip(),
            "tags": tags,
            "categoryId": "10",  # Music
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

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
        progress(0.10, desc="Uploading video to YouTube...")

    response = None

    while response is None:
        try:
            status, response = request.next_chunk()

            if status and progress:
                # Google resumable upload progress.
                percent = status.progress()
                ui_progress = 0.10 + (percent * 0.85)

                progress(
                    ui_progress,
                    desc=f"Uploading to YouTube: {percent * 100:.1f}%",
                )

        except HttpError as exc:
            raise RuntimeError(
                f"YouTube API error:\n{exc}"
            ) from exc

    if not response or "id" not in response:
        raise RuntimeError(
            "YouTube upload completed without returning a video ID."
        )

    video_id = response["id"]

    if progress:
        progress(1.0, desc="YouTube upload complete.")

    return video_id


# ============================================================
# Main Gradio workflow
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
    """
    Complete workflow:

        MP3 + JPG
            ↓
        FFmpeg
            ↓
        MP4
            ↓
        YouTube OAuth
            ↓
        YouTube upload
    """

    if audio_file is None:
        raise gr.Error("Please select an MP3 file.")

    if cover_file is None:
        raise gr.Error("Please select a JPG cover image.")

    if not title or not title.strip():
        raise gr.Error("Please enter a YouTube title.")

    audio_path = getattr(audio_file, "name", audio_file)
    cover_path = getattr(cover_file, "name", cover_file)

    if not audio_path or not Path(audio_path).exists():
        raise gr.Error("The MP3 file could not be accessed.")

    if not cover_path or not Path(cover_path).exists():
        raise gr.Error("The cover image could not be accessed.")

    audio_path = str(audio_path)
    cover_path = str(cover_path)

    # Verify extensions as an additional UI-level safeguard.
    if Path(audio_path).suffix.lower() != ".mp3":
        raise gr.Error("The audio file must be an MP3.")

    if Path(cover_path).suffix.lower() not in {
        ".jpg",
        ".jpeg",
    }:
        raise gr.Error("The cover image must be JPG/JPEG.")

    validate_dependencies()

    # Temporary directory is automatically deleted after workflow.
    with tempfile.TemporaryDirectory(prefix="yt_music_video_") as temp_dir:
        temp_dir = Path(temp_dir)

        generated_video = temp_dir / "music_video.mp4"

        try:
            progress(0.0, desc="Starting...")

            # -----------------------------------------------
            # Step 1: Generate video
            # -----------------------------------------------
            create_video(
                audio_path=audio_path,
                cover_path=cover_path,
                output_path=str(generated_video),
                progress=progress,
            )

            # -----------------------------------------------
            # Step 2: Copy generated video to output folder
            # -----------------------------------------------
            final_filename = (
                Path(audio_path).stem
                + "_youtube.mp4"
            )

            permanent_output = OUTPUT_DIR / final_filename

            shutil.copy2(
                generated_video,
                permanent_output,
            )

            # -----------------------------------------------
            # Step 3: Upload to YouTube
            # -----------------------------------------------
            video_id = upload_to_youtube(
                video_path=str(permanent_output),
                title=title,
                description=description or "",
                tags_text=tags or "",
                privacy_status=privacy_status,
                progress=progress,
            )

            youtube_url = f"https://www.youtube.com/watch?v={video_id}"

            result = (
                "## Upload complete\n\n"
                f"**Video ID:** `{video_id}`\n\n"
                f"**YouTube:** {youtube_url}\n\n"
                f"**Local MP4:** `{permanent_output}`"
            )

            return str(permanent_output), result

        except Exception as exc:
            raise gr.Error(str(exc))


# ============================================================
# Gradio UI
# ============================================================

DESCRIPTION = """
Upload an MP3 and JPG cover image to create a 1920×1080 music video.

The video contains:

- blurred cover-art background
- centered 800×800 cover
- animated audio waveform
- animated playback progress bar
- original MP3 audio stream
- duration matching the source MP3

The generated MP4 is then uploaded to YouTube using OAuth 2.0.
"""

with gr.Blocks(
    title=APP_NAME,
    theme=gr.themes.Soft(),
) as demo:

    gr.Markdown(
        f"# 🎵 {APP_NAME}"
    )

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
                placeholder="Enter video title...",
                max_lines=1,
            )

            description_input = gr.Textbox(
                label="YouTube Description",
                placeholder="Enter video description...",
                lines=6,
            )

            tags_input = gr.Textbox(
                label="Tags",
                placeholder="music, electronic, ambient, ...",
                info="Comma-separated tags.",
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
        "🎬 Generate Video & Upload to YouTube",
        variant="primary",
    )

    progress_bar = gr.Markdown(
        "Ready."
    )

    output_video = gr.File(
        label="Generated MP4",
    )

    result_output = gr.Markdown(
        label="Result"
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
# Application entry point
# ============================================================

if __name__ == "__main__":
    validate_dependencies()

    demo.launch(
        server_name=os.getenv("GRADIO_SERVER_NAME", "127.0.0.1"),
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
        show_error=True,
    )
