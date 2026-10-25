"""Demo helpers: replay the fictional chat through a webhook, make sample clips, or post them into a secret thread."""
import argparse
import json
import mimetypes
import os
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).parent
CLIPS = HERE / "clips"
SAMPLES = [("Priya", "video", "#F25F5C"), ("Jordan", "video", "#247BA0"), ("Maya", "audio", None)]


def post(url: str, name: str, text: str = "", file: Path | None = None, thread: str | None = None):
    target = url + "?wait=true" + (f"&thread_id={thread}" if thread else "")
    headers = {"User-Agent": "candles-seed"}
    if file:
        boundary = uuid.uuid4().hex
        payload = json.dumps({"username": name, "content": text})
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\nContent-Type: application/json\r\n\r\n{payload}\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"{file.name}\"\r\n"
                f"Content-Type: {mimetypes.guess_type(file.name)[0]}\r\n\r\n").encode() + file.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    else:
        body = json.dumps({"username": name, "content": text}).encode()
        headers["Content-Type"] = "application/json"
    urllib.request.urlopen(urllib.request.Request(target, body, headers)).close()


def make_clips():
    CLIPS.mkdir(exist_ok=True)
    for name, kind, color in SAMPLES:
        if kind == "video":
            out = CLIPS / f"{name.lower()}.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=720x1280:d=6",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-shortest",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)], check=True)
        else:
            out = CLIPS / f"{name.lower()}.m4a"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=660:duration=5",
                            "-c:a", "aac", str(out)], check=True)
        print("made", out)


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("what", choices=["chat", "make-clips", "clips"])
    parser.add_argument("--thread", help="secret thread ID, for clips")
    parser.add_argument("--delay", type=float, default=2.0)
    args = parser.parse_args()
    if args.what == "make-clips":
        return make_clips()
    url = os.getenv("DEMO_WEBHOOK_URL")
    if not url:
        raise SystemExit("Set DEMO_WEBHOOK_URL in .env (channel settings, Integrations, Webhooks).")
    if args.what == "clips":
        if not args.thread:
            raise SystemExit("Pass --thread with the secret thread's ID.")
        for path in sorted(CLIPS.glob("*")):
            name = path.stem.capitalize()
            post(url, name, "for the video 🎬", path, args.thread)
            print("posted", path.name, "as", name)
            time.sleep(args.delay)
        return
    lines = [l for l in (HERE / "chat.txt").read_text().splitlines() if l.strip() and not l.startswith("#")]
    for line in lines:
        name, _, text = line.partition(":")
        post(url, name.strip(), text.strip())
        print(line)
        time.sleep(args.delay)


if __name__ == "__main__":
    main()
