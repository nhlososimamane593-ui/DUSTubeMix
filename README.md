# DUSTubeMix
# YouTube Music Video Generator

A Gradio application that converts an MP3 and JPG cover image into a 1920×1080 music video and uploads it directly to YouTube using the YouTube Data API v3.

## Features

* Gradio web interface
* MP3 upload
* JPG/JPEG cover upload
* 1920×1080 output
* Blurred cover-art background
* Centered 800×800 original artwork
* Animated audio waveform
* Animated playback progress bar
* Video duration matches the MP3
* Original MP3 audio stream copied without audio re-encoding
* YouTube Data API v3 upload
* OAuth 2.0 authentication
* YouTube title, description and tags
* Private, unlisted or public upload selection
* OAuth token caching
* Resumable YouTube upload
* Generated MP4 saved locally
* Designed for local/self-hosted deployment

## Requirements

* Python 3.10+
* FFmpeg
* A Google Cloud project
* YouTube Data API v3 enabled
* OAuth 2.0 Desktop App credentials

## 1. Install FFmpeg

### Windows

Install FFmpeg and add its `bin` directory to your PATH.

Verify:

```powershell
ffmpeg -version
ffprobe -version
```

### macOS

With Homebrew:

```bash
brew install ffmpeg
```

Verify:

```bash
ffmpeg -version
ffprobe -version
```

### Ubuntu/Debian

```bash
sudo apt update
sudo apt install ffmpeg
```

Verify:

```bash
ffmpeg -version
ffprobe -version
```

## 2. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/youtube-music-video.git
cd youtube-music-video
```

## 3. Create a virtual environment

### Windows

```powershell
python -m venv .venv
.venv\Scripts\activate
```

### macOS/Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 4. Install Python dependencies

```bash
pip install -r requirements.txt
```

## 5. Configure Google Cloud

Go to:

https://console.cloud.google.com/

Create a new Google Cloud project or select an existing project.

Open:

**APIs & Services → Library**

Search for:

**YouTube Data API v3**

Enable it.

Then open:

**APIs & Services → Credentials**

Choose:

**Create Credentials → OAuth client ID**

If Google asks you to configure the OAuth consent screen first, configure it.

For this local application, choose:

**Application type → Desktop app**

Download the OAuth JSON file.

Rename it:

```text
credentials.json
```

and place it in the same directory as `app.py`:

```text
youtube-music-video/
├── app.py
├── credentials.json
├── requirements.txt
└── ...
```

Do not commit this file to GitHub.

## 6. OAuth authorization

Start the application:

```bash
python app.py
```

Open:

```text
http://127.0.0.1:7860
```

The first time you upload a video, the application will open a Google OAuth authorization page in your browser.

Sign into the Google account associated with the YouTube channel you want to upload to.

Approve the requested YouTube upload permission.

The application stores the resulting OAuth token in:

```text
token.json
```

The token is ignored by Git using `.gitignore`.

Future uploads can normally reuse the stored authorization without asking you to authorize again.

## 7. Using the application

Upload:

1. MP3 file
2. JPG/JPEG cover image

Then enter:

* YouTube title
* YouTube description
* comma-separated tags
* privacy status

For example:

```text
Title:
My New Song - Official Audio

Description:
Official audio for my new song.

Tags:
music, electronic, ambient, new music
```

Choose:

```text
private
```

for an initial test.

Then click:

```text
Generate Video & Upload to YouTube
```

The application will:

```text
MP3 + JPG
    ↓
FFmpeg
    ↓
1920×1080 MP4
    ↓
OAuth 2.0
    ↓
YouTube Data API v3
    ↓
YouTube
```

## Important: YouTube API verification

New/unverified API projects can have restrictions on uploaded videos. In particular, YouTube documents that videos uploaded through `videos.insert` by unverified API projects created after July 28, 2020 are restricted to private viewing until the project completes the required audit.

Therefore, during development/testing, use:

```text
Privacy: private
```

If you intend to publish videos publicly through an application at scale, review YouTube's API compliance and audit requirements.

## Audio quality

The application maps the original MP3 audio stream and uses:

```text
-c:a copy
```

Therefore FFmpeg does not perform another MP3 encoding pass.

The video stream is encoded separately using H.264.

## Output

Generated videos are saved under:

```text
output/
```

For example:

```text
output/
└── my-song_youtube.mp4
```

## Security

Never commit:

```text
credentials.json
token.json
```

to GitHub.

If credentials are accidentally committed, revoke/rotate them through Google Cloud immediately.

## License

Choose an appropriate license for your project, such as MIT, before publishing the repository.
