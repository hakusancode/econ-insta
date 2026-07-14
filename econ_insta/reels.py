"""릴스(9:16 세로 영상) 렌더·인코딩·발행.

카드(1080×1350)를 그대로 늘려 쓸 수 없다. 릴스는 1080×1920이라 **레이아웃을 다시 짠다**.
대신 renderer의 폰트·줄바꿈·색 규칙은 그대로 재사용한다(크기에 의존하지 않는 함수들이다).

영상 규격은 인스타가 까다롭다. 실측·문서로 확인된 것:
- H.264 / yuv420p / progressive. `-movflags +faststart`로 moov 아톰을 앞으로 보내야 한다.
- **무음이어도 오디오 트랙은 넣는다.** 트랙이 아예 없으면 처리에서 실패하는 사례가 있다.
  anullsrc로 무음 AAC를 깔아둔다.
- 릴스 탭에 노출되려면 9:16, 5~90초.
- **음원은 붙이지 않는다.** 인스타 앱 안의 음원 라이브러리는 API로 쓸 수 없고(앱 내 사용
  한정 라이선스), 우리가 임의의 음악을 넣으면 이미지에서 피해온 저작권 문제가 소리로
  옮겨올 뿐이다. 무음으로 내고 필요하면 앱에서 수동으로 음원을 얹는다.

호스팅은 raw.githubusercontent가 아니라 **GitHub Pages**를 쓴다. raw는 mp4를
`application/octet-stream`으로 주므로 인스타가 받지 않는다(실측). Pages는 `video/mp4`를 준다.

CLI:
    python -m econ_insta.reels --publish out/2026-07-14-hynix
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import imageio_ffmpeg
import requests
from PIL import Image, ImageDraw

from .config import PROJECT_ROOT
from .renderer import (
    ACCENT,
    BG,
    BG_COVER,
    FG,
    MUTED,
    FontSet,
    _block_height,
    _draw_block,
    _line_height,
    wrap,
)
from .stock_brief import Reason, Series, StockBrief, _change_color, _fmt_pct

WIDTH, HEIGHT = 1080, 1920
MARGIN = 96
FPS = 30

COVER_SECONDS = 3.0
REASON_SECONDS = 4.5
CHART_SECONDS = 5.0
FADE_SECONDS = 0.4

# 릴스 탭 노출 조건. 벗어나면 그냥 피드 영상이 된다.
REEL_MIN_SECONDS, REEL_MAX_SECONDS = 5, 90

PAGES_BASE = "https://hakusancode.github.io/econ-insta"


class ReelError(RuntimeError):
    """릴스 렌더·인코딩 실패."""


def _canvas(color: tuple[int, int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), color)
    return image, ImageDraw.Draw(image)


def _rule(draw: ImageDraw.ImageDraw, y: int, color=(42, 48, 62)) -> None:
    draw.line([(MARGIN, y), (WIDTH - MARGIN, y)], fill=color, width=2)


def _shade() -> Image.Image:
    """사진 배경을 눌러 흰 글씨가 읽히게 하는 세로 그라디언트."""
    column = []
    for y in range(HEIGHT):
        if y < 460:
            alpha = 150 - int(80 * y / 460)
        elif y < 980:
            alpha = 70
        else:
            alpha = 70 + int(170 * (y - 980) / (HEIGHT - 980))
        column.append(alpha)
    mask = Image.new("L", (1, HEIGHT))
    mask.putdata(column)
    return mask.resize((WIDTH, HEIGHT))


def cover_crop(image: Image.Image) -> Image.Image:
    """9:16으로 채워 자른다."""
    image = image.convert("RGB")
    scale = max(WIDTH / image.width, HEIGHT / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    left = (resized.width - WIDTH) // 2
    top = (resized.height - HEIGHT) // 2
    return resized.crop((left, top, left + WIDTH, top + HEIGHT))


# --- 장면(정지 이미지) -----------------------------------------------------


def scene_cover(
    headline: str,
    when: datetime,
    fonts: FontSet,
    kicker: str,
    background: Image.Image | None = None,
) -> Image.Image:
    inner = WIDTH - MARGIN * 2
    if background is None:
        image, draw = _canvas(BG_COVER)
    else:
        image = Image.composite(
            Image.new("RGB", (WIDTH, HEIGHT), BG_COVER), cover_crop(background), _shade()
        )
        draw = ImageDraw.Draw(image)

    draw.text((MARGIN, MARGIN + 40), kicker, font=fonts.at(44, bold=True), fill=ACCENT)
    draw.text((MARGIN, MARGIN + 112), f"{when:%Y년 %m월 %d일}", font=fonts.at(34), fill=MUTED)

    title_font = fonts.at(96, bold=True)
    lines = wrap(headline, title_font, inner)
    step = _line_height(title_font)
    top = HEIGHT - MARGIN - 260 - len(lines) * step
    for i, line in enumerate(lines):
        draw.text((MARGIN, top + i * step), line, font=title_font, fill=FG)
    draw.line([(MARGIN, top - 56), (MARGIN + 140, top - 56)], fill=ACCENT, width=7)

    draw.text(
        (MARGIN, HEIGHT - MARGIN - 60), "넘겨서 확인하세요 →", font=fonts.at(34), fill=MUTED
    )
    return image


def scene_reason(reason: Reason, index: int, total: int, fonts: FontSet) -> Image.Image:
    image, draw = _canvas(BG)
    inner = WIDTH - MARGIN * 2

    draw.text((MARGIN, MARGIN + 40), f"이유 {index}", font=fonts.at(50, bold=True), fill=ACCENT)
    draw.text(
        (WIDTH - MARGIN, MARGIN + 52),
        f"{index} / {total}",
        font=fonts.at(32),
        fill=MUTED,
        anchor="ra",
    )

    title_font = fonts.at(70, bold=True)
    body_font = fonts.at(46)
    gap = 52

    block = (
        _block_height(reason.title, title_font, inner)
        + gap * 2
        + _block_height(reason.body, body_font, inner)
    )
    field_top, field_bottom = MARGIN + 200, HEIGHT - MARGIN - 120
    top = max(field_top, field_top + (field_bottom - field_top - block) // 2)

    top = _draw_block(draw, reason.title, title_font, top=top, fill=FG, max_width=inner, left=MARGIN)
    top += gap
    _rule(draw, top)
    top += gap
    _draw_block(
        draw, reason.body, body_font, top=top, fill=(206, 212, 224), max_width=inner, left=MARGIN
    )

    draw.text(
        (MARGIN, HEIGHT - MARGIN - 50), f"출처 · {reason.source}", font=fonts.at(32), fill=MUTED
    )
    return image


def scene_chart(series: Series, when: datetime, fonts: FontSet) -> Image.Image:
    image, draw = _canvas(BG)
    inner = WIDTH - MARGIN * 2

    draw.text((MARGIN, MARGIN + 40), "주가 추이", font=fonts.at(70, bold=True), fill=FG)
    draw.text(
        (MARGIN, MARGIN + 130),
        f"{series.name} · {series.ticker} · 최근 3개월",
        font=fonts.at(34),
        fill=MUTED,
    )

    day_change = series.change_pct(1)
    draw.text(
        (WIDTH - MARGIN, MARGIN + 44),
        f"{series.last:,.0f}{series.currency}",
        font=fonts.at(60, bold=True),
        fill=FG,
        anchor="ra",
    )
    draw.text(
        (WIDTH - MARGIN, MARGIN + 126),
        _fmt_pct(day_change),
        font=fonts.at(40, bold=True),
        fill=_change_color(day_change),
        anchor="ra",
    )

    top, bottom = 700, 1330
    closes = series.closes
    low, high = min(closes), max(closes)
    span = (high - low) or 1.0

    def point(i: int, v: float) -> tuple[float, float]:
        x = MARGIN + inner * i / max(len(closes) - 1, 1)
        y = bottom - (bottom - top) * (v - low) / span
        return x, y

    points = [point(i, v) for i, v in enumerate(closes)]
    trend = _change_color(series.change_pct(len(closes) - 1))
    fill_color = tuple(int(c * 0.22 + BG[i] * 0.78) for i, c in enumerate(trend))
    draw.polygon([(MARGIN, bottom)] + points + [(WIDTH - MARGIN, bottom)], fill=fill_color)
    draw.line(points, fill=trend, width=6, joint="curve")

    hx, hy = point(closes.index(high), high)
    draw.ellipse([hx - 8, hy - 8, hx + 8, hy + 8], fill=trend)
    draw.text(
        (min(max(hx, MARGIN + 60), WIDTH - MARGIN - 60), hy - 22),
        f"고 {high:,.0f}",
        font=fonts.at(30),
        fill=MUTED,
        anchor="ms",
    )
    lx, ly = point(closes.index(low), low)
    draw.ellipse([lx - 8, ly - 8, lx + 8, ly + 8], fill=trend)
    draw.text(
        (min(max(lx, MARGIN + 60), WIDTH - MARGIN - 60), ly + 26),
        f"저 {low:,.0f}",
        font=fonts.at(30),
        fill=MUTED,
        anchor="ma",
    )

    rule_y = bottom + 90
    _rule(draw, rule_y)

    periods = [("1주", 5), ("1개월", 21), ("3개월", len(closes) - 1)]
    cell = inner // len(periods)
    stats_top = rule_y + 60
    for i, (label, sessions) in enumerate(periods):
        change = series.change_pct(sessions)
        cx = MARGIN + cell * i + cell // 2
        draw.text((cx, stats_top), label, font=fonts.at(34), fill=MUTED, anchor="ma")
        draw.text(
            (cx, stats_top + 56),
            _fmt_pct(change),
            font=fonts.at(56, bold=True),
            fill=_change_color(change),
            anchor="ma",
        )

    basis = f"장중 {series.basis}" if series.intraday else f"{series.basis} 기준"
    draw.text(
        (WIDTH - MARGIN, HEIGHT - MARGIN - 50),
        f"자료 · {basis} ({when:%Y.%m.%d})",
        font=fonts.at(32),
        fill=MUTED,
        anchor="ra",
    )
    return image


# --- 모션 ------------------------------------------------------------------


@dataclass(frozen=True)
class Scene:
    image: Image.Image
    seconds: float
    zoom: float = 1.0
    """장면이 끝날 때의 확대율. 1.0이면 정지, 1.06이면 아주 느리게 밀려든다."""


def _zoomed(image: Image.Image, scale: float) -> Image.Image:
    """중심을 유지한 채 scale배 확대해 같은 크기로 자른다."""
    if abs(scale - 1.0) < 1e-3:
        return image
    big = image.resize((round(WIDTH * scale), round(HEIGHT * scale)), Image.LANCZOS)
    left = (big.width - WIDTH) // 2
    top = (big.height - HEIGHT) // 2
    return big.crop((left, top, left + WIDTH, top + HEIGHT))


def frames(scenes: list[Scene]) -> Iterator[Image.Image]:
    """장면들을 프레임 시퀀스로. 장면 경계는 검정으로 짧게 페이드한다.

    크로스페이드가 아니라 페이드아웃/인이다 — 글자가 겹쳐 뭉개지지 않는다.
    """
    black = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    fade_frames = max(int(FADE_SECONDS * FPS), 1)

    for scene in scenes:
        total = max(int(scene.seconds * FPS), 1)
        for i in range(total):
            progress = i / max(total - 1, 1)
            frame = _zoomed(scene.image, 1.0 + (scene.zoom - 1.0) * progress)

            if i < fade_frames:  # 페이드 인
                frame = Image.blend(black, frame, i / fade_frames)
            elif i >= total - fade_frames:  # 페이드 아웃
                frame = Image.blend(black, frame, (total - 1 - i) / fade_frames)
            yield frame


# --- 인코딩 ----------------------------------------------------------------


def encode(scenes: list[Scene], path: Path) -> Path:
    """프레임을 ffmpeg에 직접 파이프해 인스타 규격 mp4로 만든다."""
    seconds = sum(scene.seconds for scene in scenes)
    if not REEL_MIN_SECONDS <= seconds <= REEL_MAX_SECONDS:
        raise ReelError(
            f"릴스는 {REEL_MIN_SECONDS}~{REEL_MAX_SECONDS}초여야 릴스 탭에 노출됩니다 "
            f"(현재 {seconds:.1f}초)."
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-y",
        "-loglevel", "error",
        # 영상: 파이프로 들어오는 생 RGB 프레임
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS),
        "-i", "-",
        # 소리: 무음 트랙. 오디오 스트림이 아예 없으면 처리에서 실패할 수 있다.
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-g", str(FPS * 2), "-r", str(FPS), "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",  # moov 아톰을 앞으로 — 인스타가 요구한다
        str(path),
    ]

    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for frame in frames(scenes):
            process.stdin.write(frame.tobytes())
    except BrokenPipeError as exc:
        raise ReelError(f"ffmpeg가 중간에 죽었습니다: {process.stderr.read().decode()}") from exc
    finally:
        if process.stdin:
            process.stdin.close()

    if process.wait() != 0:
        raise ReelError(f"인코딩 실패: {process.stderr.read().decode()}")
    return path


def build_stock_reel(
    brief: StockBrief,
    when: datetime,
    out_dir: Path,
    fonts: FontSet | None = None,
    background: Image.Image | None = None,
) -> tuple[Path, Path]:
    """종목 브리핑 → 릴스 mp4 + 표지 이미지(cover_url용). 둘 다 경로를 돌려준다."""
    fonts = fonts or FontSet.discover()

    cover = scene_cover(
        brief.headline, when, fonts, kicker="종목 이슈 브리핑", background=background
    )
    scenes = [Scene(cover, COVER_SECONDS, zoom=1.06 if background else 1.0)]
    scenes += [
        Scene(scene_reason(reason, i + 1, len(brief.reasons), fonts), REASON_SECONDS)
        for i, reason in enumerate(brief.reasons)
    ]
    scenes.append(Scene(scene_chart(brief.series, when, fonts), CHART_SECONDS))

    out_dir.mkdir(parents=True, exist_ok=True)
    cover_path = out_dir / "reel-cover.jpg"
    cover.save(cover_path, "JPEG", quality=92)
    video_path = encode(scenes, out_dir / "reel.mp4")
    return video_path, cover_path


# --- 발행 ------------------------------------------------------------------


def publish_reel(out_dir: Path) -> int:
    """Pages에 올라간 mp4를 릴스로 발행한다."""
    from .ig_client import InstagramClient

    out_dir = out_dir.resolve()
    video = out_dir / "reel.mp4"
    cover = out_dir / "reel-cover.jpg"
    caption_path = out_dir / "caption.txt"
    for path in (video, caption_path):
        if not path.exists():
            print(f"없습니다: {path}")
            return 1

    rel = out_dir.relative_to(PROJECT_ROOT.resolve()).as_posix()
    video_url = f"{PAGES_BASE}/{rel}/{video.name}"
    cover_url = f"{PAGES_BASE}/{rel}/{cover.name}" if cover.exists() else None

    # raw.githubusercontent는 mp4를 application/octet-stream으로 준다 → 인스타가 안 받는다.
    # Pages가 video/mp4를 주는지 발행 전에 확인한다.
    response = requests.get(video_url, timeout=30, allow_redirects=False)
    kind = response.headers.get("Content-Type", "")
    if response.status_code != 200 or not kind.startswith("video/"):
        print(f"호스팅 확인 실패 ({response.status_code}, {kind}): {video_url}")
        print("Pages 빌드가 끝났는지 확인하세요 (push 후 1~2분).")
        return 1
    print(f"호스팅 OK: {kind}, {len(response.content):,} bytes")

    caption = caption_path.read_text(encoding="utf-8")
    result = InstagramClient().publish_reel(video_url, caption, cover_url=cover_url)
    print(f"발행 완료: media_id={result.media_id}")
    print(f"  {result.permalink}")
    return 0


def main() -> int:
    import sys

    if "--publish" in sys.argv:
        index = sys.argv.index("--publish")
        if index + 1 >= len(sys.argv):
            print("사용법: python -m econ_insta.reels --publish out/<날짜>-<종목>")
            return 1
        return publish_reel(Path(sys.argv[index + 1]))

    print("사용법: python -m econ_insta.reels --publish out/<날짜>-<종목>")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
