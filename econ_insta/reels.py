"""릴스(9:16 세로 영상) 렌더·인코딩·발행.

카드(1080×1350)를 그대로 늘려 쓸 수 없다. 릴스는 1080×1920이라 **레이아웃을 다시 짠다**.
대신 renderer의 폰트·줄바꿈·색 규칙은 그대로 재사용한다(크기에 의존하지 않는 함수들이다).

영상 규격은 인스타가 까다롭다. 실측·문서로 확인된 것:
- H.264 / yuv420p / progressive. `-movflags +faststart`로 moov 아톰을 앞으로 보내야 한다.
- **무음이어도 오디오 트랙은 넣는다.** 트랙이 아예 없으면 처리에서 실패하는 사례가 있다.
  anullsrc로 무음 AAC를 깔아둔다.
- 릴스 탭에 노출되려면 9:16, 5~90초.
- **음원은 라이선스가 확인된 것만 붙인다.** 인스타 앱 안의 음원 라이브러리는 API로 쓸 수
  없고(앱 내 사용 한정 라이선스), 임의의 음악을 넣으면 이미지에서 피해온 저작권 문제가
  소리로 옮겨올 뿐이다. 대신 `audio.py`가 관리하는 로열티프리 번들(assets/audio/tracks.json,
  cc0/cc-by만 허용)에서 고른 트랙을 `encode(..., audio_path=...)`로 얹는다. `audio_path`가
  없으면 기존대로 무음(anullsrc)으로 낸다.

호스팅은 raw.githubusercontent가 아니라 **GitHub Pages**를 쓴다. raw는 mp4를
`application/octet-stream`으로 주므로 인스타가 받지 않는다(실측). Pages는 `video/mp4`를 준다.

CLI:
    python -m econ_insta.reels --publish out/2026-07-14-hynix
"""

from __future__ import annotations

import math
import subprocess
import time
from collections.abc import Callable, Iterator
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

COVER_SECONDS = 3.5   # 기존 3.0 — 카운트업이 읽힐 시간
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


def _draw_chart_stats(draw: ImageDraw.ImageDraw, series: Series, fonts: FontSet, rule_y: int) -> None:
    """구분선 아래 기간별(1주·1개월·3개월) 등락률 3칸."""
    inner = WIDTH - MARGIN * 2
    closes = series.closes
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


def _chart_frame(series: Series, when: datetime, fonts: FontSet, p: float) -> Image.Image:
    """차트 장면의 진행률 p 시점 프레임. p=1.0이 완성된(정지) 프레임이다."""
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

    visible = _visible_count(len(closes), p)
    points = [point(i, v) for i, v in enumerate(closes[:visible])]
    trend = _change_color(series.change_pct(len(closes) - 1))
    fill_color = tuple(int(c * 0.22 + BG[i] * 0.78) for i, c in enumerate(trend))
    draw.polygon([(MARGIN, bottom)] + points + [(points[-1][0], bottom)], fill=fill_color)
    draw.line(points, fill=trend, width=6, joint="curve")

    high_index = closes.index(high)
    if high_index < visible:
        hx, hy = point(high_index, high)
        draw.ellipse([hx - 8, hy - 8, hx + 8, hy + 8], fill=trend)
        draw.text(
            (min(max(hx, MARGIN + 60), WIDTH - MARGIN - 60), hy - 22),
            f"고 {high:,.0f}",
            font=fonts.at(30),
            fill=MUTED,
            anchor="ms",
        )
    low_index = closes.index(low)
    if low_index < visible:
        lx, ly = point(low_index, low)
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

    basis = f"장중 {series.basis}" if series.intraday else f"{series.basis} 기준"
    draw.text(
        (WIDTH - MARGIN, HEIGHT - MARGIN - 50),
        f"자료 · {basis} ({when:%Y.%m.%d})",
        font=fonts.at(32),
        fill=MUTED,
        anchor="ra",
    )

    seg = ease_out_cubic(min(max((p - 0.75) / 0.25, 0.0), 1.0))
    if seg <= 0:
        return image
    with_stats = image.copy()
    _draw_chart_stats(ImageDraw.Draw(with_stats), series, fonts, rule_y)
    return Image.blend(image, with_stats, seg)


def scene_chart(series: Series, when: datetime, fonts: FontSet) -> Image.Image:
    """정지 차트 = 애니메이션의 마지막 프레임 (기존 호출부·테스트 계약 유지)."""
    return _chart_frame(series, when, fonts, 1.0)


# --- 모션 ------------------------------------------------------------------


def ease_out_cubic(p: float) -> float:
    """감속 이징. 모든 모션이 이 하나를 쓴다."""
    p = min(max(p, 0.0), 1.0)
    return 1 - (1 - p) ** 3


def _count_value(target: float, p: float) -> float:
    """도입부 카운트업: 장면 앞 60% 동안 0 → target."""
    return target * ease_out_cubic(min(p / 0.6, 1.0))


def _visible_count(n: int, p: float) -> int:
    """차트 드로잉: 장면 앞 70% 동안 가시 점 2 → n."""
    return max(2, math.ceil(n * ease_out_cubic(min(p / 0.7, 1.0))))


# --- 애니메이션 장면 --------------------------------------------------------


def anim_cover(
    headline: str,
    pct: float | None,
    when: datetime,
    fonts: FontSet,
    kicker: str,
    background: Image.Image | None = None,
) -> Callable[[float], Image.Image]:
    """도입부: 배경 슬로우 줌 + 등락률 카운트업 + 헤드라인 슬라이드인."""
    base = Image.new("RGB", (WIDTH, HEIGHT), BG_COVER)
    photo = cover_crop(background) if background is not None else None
    shade = _shade()
    inner = WIDTH - MARGIN * 2
    title_font = fonts.at(96, bold=True)
    lines = wrap(headline, title_font, inner)
    step = _line_height(title_font)
    title_top = HEIGHT - MARGIN - 260 - len(lines) * step

    def render(p: float) -> Image.Image:
        if photo is not None:
            image = Image.composite(base, _zoomed(photo, 1.0 + 0.06 * p), shade)
        else:
            image = base.copy()
        draw = ImageDraw.Draw(image)
        draw.text((MARGIN, MARGIN + 40), kicker, font=fonts.at(44, bold=True), fill=ACCENT)
        draw.text((MARGIN, MARGIN + 112), f"{when:%Y년 %m월 %d일}", font=fonts.at(34), fill=MUTED)
        if pct is not None:
            value = _count_value(pct, p)
            draw.text(
                (WIDTH // 2, 620),
                _fmt_pct(value),
                font=fonts.at(190, bold=True),
                fill=_change_color(pct),
                anchor="mm",
            )
        seg = ease_out_cubic(min(max((p - 0.55) / 0.25, 0.0), 1.0))
        if seg > 0:
            overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
            odraw = ImageDraw.Draw(overlay)
            alpha = int(255 * seg)
            offset = int((1 - seg) * 60)
            odraw.line(
                [(MARGIN, title_top + offset - 56), (MARGIN + 140, title_top + offset - 56)],
                fill=ACCENT + (alpha,),
                width=7,
            )
            for i, line in enumerate(lines):
                odraw.text(
                    (MARGIN, title_top + offset + i * step), line, font=title_font, fill=FG + (alpha,)
                )
            image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(image)
        draw.text((MARGIN, HEIGHT - MARGIN - 60), "넘겨서 확인하세요 →", font=fonts.at(34), fill=MUTED)
        return image

    return render


def anim_reason(
    reason: Reason, index: int, total: int, fonts: FontSet
) -> Callable[[float], Image.Image]:
    """이유 카드: 첫 15% 슬라이드인·페이드인, 이후 정지 장면과 동일."""
    final = scene_reason(reason, index, total, fonts)
    blank = Image.new("RGB", (WIDTH, HEIGHT), BG)

    def render(p: float) -> Image.Image:
        seg = ease_out_cubic(min(p / 0.15, 1.0))
        if seg >= 1.0:
            return final
        shifted = blank.copy()
        shifted.paste(final, (0, int((1 - seg) * 40)))
        return Image.blend(blank, shifted, seg)

    return render


def anim_chart(series: Series, when: datetime, fonts: FontSet) -> Callable[[float], Image.Image]:
    """차트: 진행률에 따라 선이 좌→우로 그려지고, 후반부에 통계 블록이 페이드인."""
    return lambda p: _chart_frame(series, when, fonts, p)


@dataclass(frozen=True)
class Scene:
    image: Image.Image | None
    seconds: float
    zoom: float = 1.0
    """정지 장면 전용 — 장면이 끝날 때의 확대율."""
    render: Callable[[float], Image.Image] | None = None
    """애니메이션 장면: 진행률 p(0.0~1.0)를 받아 그 시점 프레임을 그린다.
    있으면 image·zoom은 무시된다. 페이드는 frames()가 공통으로 얹는다."""


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
            if scene.render is not None:
                frame = scene.render(progress)
            else:
                frame = _zoomed(scene.image, 1.0 + (scene.zoom - 1.0) * progress)

            if i < fade_frames:  # 페이드 인
                frame = Image.blend(black, frame, i / fade_frames)
            elif i >= total - fade_frames:  # 페이드 아웃
                frame = Image.blend(black, frame, (total - 1 - i) / fade_frames)
            yield frame


# --- 인코딩 ----------------------------------------------------------------


def _ffmpeg_command(path: Path, audio_path: Path | None) -> list[str]:
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-",
    ]
    if audio_path is None:
        # 무음 트랙. 오디오 스트림이 아예 없으면 처리에서 실패할 수 있다.
        command += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    else:
        command += ["-stream_loop", "-1", "-i", str(audio_path)]
    command += [
        "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-g", str(FPS * 2), "-r", str(FPS), "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-shortest",
        "-movflags", "+faststart", str(path),  # moov 아톰을 앞으로 — 인스타가 요구한다
    ]
    return command


def encode(scenes: list[Scene], path: Path, audio_path: Path | None = None) -> Path:
    """프레임을 ffmpeg에 직접 파이프해 인스타 규격 mp4로 만든다."""
    seconds = sum(scene.seconds for scene in scenes)
    if not REEL_MIN_SECONDS <= seconds <= REEL_MAX_SECONDS:
        raise ReelError(
            f"릴스는 {REEL_MIN_SECONDS}~{REEL_MAX_SECONDS}초여야 릴스 탭에 노출됩니다 "
            f"(현재 {seconds:.1f}초)."
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    command = _ffmpeg_command(path, audio_path)

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
    audio_path: Path | None = None,
) -> tuple[Path, Path]:
    """종목 브리핑 → 릴스 mp4 + 표지 이미지(cover_url용). 둘 다 경로를 돌려준다."""
    fonts = fonts or FontSet.discover()

    day_change = brief.series.change_pct(1)
    cover_render = anim_cover(
        brief.headline, day_change, when, fonts, kicker="종목 이슈 브리핑", background=background
    )
    scenes = [Scene(None, COVER_SECONDS, render=cover_render)]
    scenes += [
        Scene(None, REASON_SECONDS, render=anim_reason(reason, i + 1, len(brief.reasons), fonts))
        for i, reason in enumerate(brief.reasons)
    ]
    scenes.append(Scene(None, CHART_SECONDS, render=anim_chart(brief.series, when, fonts)))

    out_dir.mkdir(parents=True, exist_ok=True)
    cover_path = out_dir / "reel-cover.jpg"
    cover_render(1.0).save(cover_path, "JPEG", quality=92)
    video_path = encode(scenes, out_dir / "reel.mp4", audio_path=audio_path)
    return video_path, cover_path


# --- 발행 ------------------------------------------------------------------


def _video_hosting_ready(url: str, *, attempts: int = 10, delay: float = 30.0,
                         sleep=time.sleep, get=requests.get) -> bool:
    """Pages가 mp4를 video/*로 주는지 재시도하며 확인한다.

    daily.hosting_ready(daily.py:89)의 단일 URL 축소판 — Pages 빌드는 1~2분 걸려
    기존 단발 확인은 크론에서 거의 매번 죽었다.
    """
    for attempt in range(1, attempts + 1):
        response = get(url, timeout=30, allow_redirects=False)
        kind = response.headers.get("Content-Type", "")
        if response.status_code == 200 and kind.startswith("video/"):
            return True
        print(f"호스팅 미전파 (시도 {attempt}/{attempts}, HTTP {response.status_code}, {kind}): {url}")
        if attempt < attempts:
            sleep(delay)
    return False


def publish_reel(out_dir: Path, *, attempts: int = 10, delay: float = 30.0, sleep=time.sleep) -> int:
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
    # Pages가 video/mp4를 주는지 발행 전에 확인한다(빌드 1~2분 — 재시도).
    if not _video_hosting_ready(video_url, attempts=attempts, delay=delay, sleep=sleep):
        print("Pages 빌드가 끝났는지 확인하세요 (push 후 1~2분).")
        return 1
    print(f"호스팅 OK: {video_url}")

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
