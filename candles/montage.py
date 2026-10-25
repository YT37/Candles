import asyncio
import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
COLORS = ["#F25F5C", "#247BA0", "#70C1B3", "#FFB400", "#8E7DBE", "#50514F"]
FONT_BOLD = ["/usr/share/fonts/noto/NotoSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"]
ENCODE = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", "30",
          "-c:a", "aac", "-ar", "44100", "-ac", "2"]


def font(size: int):
    for path in FONT_BOLD:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def centered(draw, y, text, size, fill="white"):
    f = font(size)
    width = draw.textlength(text, font=f)
    draw.text(((W - width) / 2, y), text, font=f, fill=fill)


def wrap(draw, text: str, size: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and draw.textlength(trial, font=font(size)) > W - 140:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line]


def card(path: Path, title: str, subtitle: str = "", color: str = COLORS[0]):
    image = Image.new("RGB", (W, H), color)
    draw = ImageDraw.Draw(image)
    lines = wrap(draw, title, 110)
    y = H // 2 - 70 - 130 * len(lines)
    for line in lines:
        centered(draw, y, line, 110)
        y += 130
    if subtitle:
        centered(draw, y + 40, subtitle, 56)
    image.save(path)


def name_tag(path: Path, name: str):
    image = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    f = font(64)
    width = draw.textlength(name, font=f)
    draw.rounded_rectangle(((W - width) / 2 - 40, H - 300, (W + width) / 2 + 40, H - 190), 50, fill=(0, 0, 0, 170))
    draw.text(((W - width) / 2, H - 290), name, font=f, fill="white")
    image.save(path)


async def run(*args):
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await process.communicate()
    if process.returncode:
        raise RuntimeError(f"{args[0]} failed: {err.decode()[-500:]}")
    return out.decode()


async def probe(path: Path) -> tuple[float, bool]:
    data = json.loads(await run("ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)))
    return float(data["format"].get("duration", 0) or 0), any(s["codec_type"] == "audio" for s in data.get("streams", []))


async def still(image: Path, out: Path, seconds: float, audio: Path | None = None):
    sound = ["-i", str(audio)] if audio else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    await run("ffmpeg", "-y", "-loop", "1", "-i", str(image), *sound, "-t", f"{seconds:.2f}", "-shortest", *ENCODE, str(out))


async def video_segment(clip: Path, tag: Path, out: Path, seconds: float, has_audio: bool):
    sound = [] if has_audio else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    audio_map = "0:a" if has_audio else "2:a"
    graph = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
             "setsar=1,fps=30[v];[v][1:v]overlay=0:0[out]")
    await run("ffmpeg", "-y", "-i", str(clip), "-i", str(tag), *sound, "-filter_complex", graph,
              "-map", "[out]", "-map", audio_map, "-t", f"{seconds:.2f}", *ENCODE, str(out))


async def build(clips: list[dict], birthday_name: str, out: Path, limit_mb: float, max_clip: float = 15, max_total: float = 115) -> Path:
    work = out.parent / f"{out.stem}-work"
    work.mkdir(parents=True, exist_ok=True)
    try:
        card(work / "intro.png", f"Happy birthday, {birthday_name}!", "from your people", COLORS[0])
        card(work / "outro.png", "Have the best year", "love, all of us", COLORS[1])
        parts = [work / "intro.mp4"]
        await still(work / "intro.png", parts[0], 2.5)
        total = 5.0
        for i, clip in enumerate(clips):
            path = Path(clip["path"])
            duration, has_audio = await probe(path)
            seconds = min(duration or max_clip, max_clip, max_total - total)
            if seconds <= 0.5:
                break
            segment = work / f"seg{i}.mp4"
            if clip["kind"] == "audio":
                card(work / f"card{i}.png", clip["name"], "voice note", COLORS[(i + 2) % len(COLORS)])
                await still(work / f"card{i}.png", segment, seconds, path)
            else:
                name_tag(work / f"tag{i}.png", clip["name"])
                await video_segment(path, work / f"tag{i}.png", segment, seconds, has_audio)
            parts.append(segment)
            total += seconds
        parts.append(work / "outro.mp4")
        await still(work / "outro.png", parts[-1], 2.5)
        (work / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in parts))
        for scale in (0.9, 0.7, 0.5):
            kbps = int(limit_mb * 8192 * scale / total) - 128
            await run("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work / "list.txt"),
                      "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{max(kbps, 300)}k", "-maxrate", f"{max(kbps, 300)}k",
                      "-bufsize", f"{max(kbps, 300) * 2}k", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
                      "-movflags", "+faststart", str(out))
            if out.stat().st_size <= limit_mb * 1024 * 1024:
                return out
        raise RuntimeError("montage is still over the upload limit")
    finally:
        shutil.rmtree(work, ignore_errors=True)
