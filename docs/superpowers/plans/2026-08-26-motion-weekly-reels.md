# 모션 릴스 + 주간 정기화 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 릴스의 정지 표지를 애니메이션 도입부로 바꾸고(카운트업·슬라이드인·차트 드로잉), 로열티프리 음원을 자동 삽입하며, 일요일 19:00 KST 주간 릴스 크론을 가동한다.

**Architecture:** `reels.Scene`에 진행률 기반 `render` 콜백을 추가해 기존 프레임 파이프라인 위에 애니메이션을 얹는다. 데일리 크론이 `briefing.json`을 매일 남기고(국내 RSS는 D-2면 증발), 신규 `weekly_reel.py`가 그 주의 briefing.json에서 최대 이슈를 골라 Claude로 대본을 쓰고 모션 릴스를 렌더·발행한다.

**Tech Stack:** Python 3.13, Pillow, imageio-ffmpeg, anthropic SDK(`claude-sonnet-5`), yfinance, GitHub Actions, GitHub Pages(영상 호스팅).

**스펙:** `docs/superpowers/specs/2026-08-26-motion-weekly-reels-design.md`

## Global Constraints

- 테스트 러너는 **`unittest`** (`python -m unittest discover -s tests -q`). pytest 미설치.
- 테스트는 네트워크·모델 호출 없이 돌아야 한다 (FakeClient·주입 패턴 — `tests/test_blog_brief` 계열 참조).
- 등락 색: **상승 빨강·하락 파랑** (한국 관행, `stock_brief._change_color` 재사용).
- yfinance는 반드시 `auto_adjust=False`. NaN 행은 `v == v`로 걸러라.
- 스크립트 실행 시 `PYTHONIOENCODING=utf-8` (Git Bash cp949 콘솔).
- 발행된 게시물은 API로 수정·삭제 불가 — 발행 전 캡션·영상을 눈으로 확인.
- 라이선스 크레딧(📷·🎵)은 캡션에서 빼면 실제 라이선스 위반이다.
- 커밋 메시지 끝: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. **push는 사용자 승인 후에만.**
- 뮤테이션 복원은 git checkout이 아니라 **역치환**으로만 (2026-07-19 사고 재발 방지).

---

### Task 1: Scene.render 콜백 + frames() 분기

**Files:**
- Modify: `econ_insta/reels.py` (Scene dataclass ~273행, frames() ~291행)
- Test: `tests/test_reels_motion.py` (신규)

**Interfaces:**
- Produces: `Scene(image, seconds, zoom=1.0, render=None)` — `render: Callable[[float], Image.Image] | None`. render가 있으면 frames()가 진행률 p(0.0~1.0)로 그걸 호출하고 image·zoom은 무시. 페이드 인/아웃은 기존과 동일하게 공통 적용.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_reels_motion.py`:

```python
import unittest
from PIL import Image
from econ_insta import reels


def _solid(color):
    return Image.new("RGB", (reels.WIDTH, reels.HEIGHT), color)


class SceneRenderTest(unittest.TestCase):
    def test_render가_있으면_image_대신_render를_쓴다(self):
        red, blue = _solid((255, 0, 0)), _solid((0, 0, 255))
        scene = reels.Scene(image=blue, seconds=2.0, render=lambda p: red)
        # 2.0s×30fps=60프레임, 페이드 12프레임 — 30번째는 페이드 밖이라 원색이어야 한다.
        frame = list(reels.frames([scene]))[30]
        self.assertEqual(frame.getpixel((10, 10)), (255, 0, 0))

    def test_render에_진행률이_0에서_1까지_들어온다(self):
        seen = []

        def spy(p):
            seen.append(p)
            return _solid((0, 0, 0))

        list(reels.frames([reels.Scene(image=None, seconds=1.0, render=spy)]))
        self.assertAlmostEqual(seen[0], 0.0)
        self.assertAlmostEqual(seen[-1], 1.0)
        self.assertEqual(seen, sorted(seen))

    def test_정지_장면은_기존대로_image를_쓴다(self):
        blue = _solid((0, 0, 255))
        frame = list(reels.frames([reels.Scene(image=blue, seconds=2.0)]))[30]
        self.assertEqual(frame.getpixel((10, 10)), (0, 0, 255))
```

- [ ] **Step 2: 실패 확인** — `PYTHONIOENCODING=utf-8 python -m unittest tests.test_reels_motion -v` → 앞 두 개 FAIL/ERROR(`render` 인자 없음), 셋째 PASS.

- [ ] **Step 3: 구현** — `reels.py`의 Scene·frames 수정 (`from collections.abc import Callable, Iterator`로 import 확장):

```python
@dataclass(frozen=True)
class Scene:
    image: Image.Image | None
    seconds: float
    zoom: float = 1.0
    """정지 장면 전용 — 장면이 끝날 때의 확대율."""
    render: Callable[[float], Image.Image] | None = None
    """애니메이션 장면: 진행률 p(0.0~1.0)를 받아 그 시점 프레임을 그린다.
    있으면 image·zoom은 무시된다. 페이드는 frames()가 공통으로 얹는다."""
```

frames() 본문의 프레임 산출 한 줄만 분기로 교체:

```python
            if scene.render is not None:
                frame = scene.render(progress)
            else:
                frame = _zoomed(scene.image, 1.0 + (scene.zoom - 1.0) * progress)
```

- [ ] **Step 4: 통과 확인** — 같은 명령 3개 PASS + 전체 회귀 `python -m unittest discover -s tests -q`.
- [ ] **Step 5: 커밋** — `git add econ_insta/reels.py tests/test_reels_motion.py && git commit -m "feat(reels): Scene에 진행률 render 콜백 추가"`

---

### Task 2: 이징·카운트업·가시점 순수 함수

**Files:**
- Modify: `econ_insta/reels.py` (모션 섹션 상단, `import math` 추가)
- Test: `tests/test_reels_motion.py` (추가)

**Interfaces:**
- Produces: `ease_out_cubic(p: float) -> float` (0~1 클램프), `_count_value(target: float, p: float) -> float` (p=0.6에 target 도달), `_visible_count(n: int, p: float) -> int` (2 ≤ 반환 ≤ n, p=0.7에 n 도달, 단조증가).

- [ ] **Step 1: 실패하는 테스트 작성** (같은 파일에 클래스 추가):

```python
class MotionMathTest(unittest.TestCase):
    def test_ease_경계(self):
        self.assertEqual(reels.ease_out_cubic(0.0), 0.0)
        self.assertEqual(reels.ease_out_cubic(1.0), 1.0)
        self.assertEqual(reels.ease_out_cubic(-1.0), 0.0)   # 클램프
        self.assertEqual(reels.ease_out_cubic(2.0), 1.0)

    def test_count_value는_p06에_목표_도달(self):
        self.assertEqual(reels._count_value(-34.0, 0.0), 0.0)
        self.assertAlmostEqual(reels._count_value(-34.0, 0.6), -34.0)
        self.assertAlmostEqual(reels._count_value(-34.0, 1.0), -34.0)

    def test_visible_count_경계와_단조증가(self):
        n = 63
        values = [reels._visible_count(n, i / 100) for i in range(101)]
        self.assertEqual(values[0], 2)
        self.assertEqual(values[-1], n)
        self.assertEqual(values, sorted(values))
```

- [ ] **Step 2: 실패 확인** → AttributeError.
- [ ] **Step 3: 구현** — reels.py 모션 섹션에:

```python
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
```

- [ ] **Step 4: 통과 확인** + 전체 회귀.
- [ ] **Step 5: 커밋** — `feat(reels): 모션 수치 순수 함수 (이징·카운트업·가시점)`

---

### Task 3: 애니메이션 장면 3종 + build_stock_reel 교체

**Files:**
- Modify: `econ_insta/reels.py` (`COVER_SECONDS = 3.5`로 변경, scene_chart 리팩터, anim_* 신규. `_fmt_pct`·`_change_color`는 이미 import됨)
- Test: `tests/test_reels_motion.py` (추가)

**Interfaces:**
- Consumes: Task 1 `Scene.render`, Task 2 순수 함수.
- Produces:
  - `anim_cover(headline: str, pct: float | None, when: datetime, fonts: FontSet, kicker: str, background: Image.Image | None = None) -> Callable[[float], Image.Image]`
  - `anim_reason(reason: Reason, index: int, total: int, fonts: FontSet) -> Callable[[float], Image.Image]`
  - `anim_chart(series: Series, when: datetime, fonts: FontSet) -> Callable[[float], Image.Image]`
  - `build_stock_reel(...)` 시그니처 불변, 내부가 애니메이션 장면 사용. `reel-cover.jpg` = `anim_cover(...)(1.0)`.

- [ ] **Step 1: 실패하는 테스트 작성**:

```python
from datetime import datetime
from econ_insta.renderer import FontSet
from econ_insta.stock_brief import Reason, Series

WHEN = datetime(2026, 8, 26, 12, 0)


def _fonts():
    # FontSet 생성은 tests/test_reels.py 기존 테스트의 관용구를 열어 그대로 복사한다
    # (ImageFont.load_default(size) 주입 — 글리프는 두부여도 기하 검증엔 무방).
    ...


def _series():
    closes = [100.0 + i for i in range(63)]
    dates = [WHEN] * 63
    return Series(name="코스피", ticker="^KS11", closes=closes, dates=dates)


class AnimSceneTest(unittest.TestCase):
    def test_anim_cover_시작과_끝_프레임이_다르다(self):
        render = reels.anim_cover("코스피 급락", -6.4, WHEN, _fonts(), kicker="주간 이슈 브리핑")
        self.assertNotEqual(render(0.0).tobytes(), render(1.0).tobytes())

    def test_anim_cover_마지막_프레임은_결정적이다(self):
        render = reels.anim_cover("코스피 급락", -6.4, WHEN, _fonts(), kicker="주간 이슈 브리핑")
        self.assertEqual(render(1.0).tobytes(), render(1.0).tobytes())

    def test_anim_chart_중간에는_선이_덜_그려진다(self):
        render = reels.anim_chart(_series(), WHEN, _fonts())
        # 진행 30% 시점 프레임은 완성 프레임과 달라야 한다(오른쪽 구간 미가시).
        self.assertNotEqual(render(0.3).tobytes(), render(1.0).tobytes())

    def test_anim_reason_끝_프레임은_정지_장면과_같다(self):
        reason = Reason(title="수급 붕괴", body="외국인 순매도가 이어졌다.", source="매일경제")
        anim = reels.anim_reason(reason, 1, 2, _fonts())(1.0)
        still = reels.scene_reason(reason, 1, 2, _fonts())
        self.assertEqual(anim.tobytes(), still.tobytes())
```

- [ ] **Step 2: 실패 확인** → AttributeError(anim_cover 없음).
- [ ] **Step 3: 구현**:

```python
COVER_SECONDS = 3.5   # 기존 3.0 — 카운트업이 읽힐 시간


def anim_cover(headline, pct, when, fonts, kicker, background=None):
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
            draw.text((WIDTH // 2, 620), _fmt_pct(value), font=fonts.at(190, bold=True),
                      fill=_change_color(pct), anchor="mm")
        seg = ease_out_cubic(min(max((p - 0.55) / 0.25, 0.0), 1.0))
        if seg > 0:
            overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
            odraw = ImageDraw.Draw(overlay)
            alpha = int(255 * seg)
            offset = int((1 - seg) * 60)
            odraw.line([(MARGIN, title_top + offset - 56), (MARGIN + 140, title_top + offset - 56)],
                       fill=ACCENT + (alpha,), width=7)
            for i, line in enumerate(lines):
                odraw.text((MARGIN, title_top + offset + i * step), line,
                           font=title_font, fill=FG + (alpha,))
            image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(image)
        draw.text((MARGIN, HEIGHT - MARGIN - 60), "넘겨서 확인하세요 →", font=fonts.at(34), fill=MUTED)
        return image

    return render


def anim_reason(reason, index, total, fonts):
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
```

차트는 scene_chart를 **진행률 인자를 받는 `_chart_frame(series, when, fonts, p)`로 리팩터**한다. 기존 본문(178~267행)을 옮기되:
- `visible = _visible_count(len(closes), p)`; `points = [point(i, v) for i, v in enumerate(closes[:visible])]`
- 폴리곤 오른쪽 끝을 `(points[-1][0], bottom)`으로(가시 구간까지만 채움).
- 고/저 마커·라벨은 `closes.index(high) < visible`일 때만(low 동일).
- 기간별 통계 블록은 별도 내부 함수 `_draw_chart_stats(draw, series, fonts, rule_y)`로 빼고, `p > 0.75`이면 통계 없는 프레임과 있는 프레임을 `Image.blend(frame, with_stats, ease_out_cubic((p - 0.75) / 0.25))`.
- 하단 basis 문구는 항상 그린다.

```python
def anim_chart(series, when, fonts):
    return lambda p: _chart_frame(series, when, fonts, p)


def scene_chart(series, when, fonts):
    """정지 차트 = 애니메이션의 마지막 프레임 (기존 호출부·테스트 계약 유지)."""
    return _chart_frame(series, when, fonts, 1.0)
```

`build_stock_reel` 교체:

```python
    day_change = brief.series.change_pct(1)
    cover_render = anim_cover(brief.headline, day_change, when, fonts,
                              kicker="종목 이슈 브리핑", background=background)
    scenes = [Scene(None, COVER_SECONDS, render=cover_render)]
    scenes += [
        Scene(None, REASON_SECONDS, render=anim_reason(reason, i + 1, len(brief.reasons), fonts))
        for i, reason in enumerate(brief.reasons)
    ]
    scenes.append(Scene(None, CHART_SECONDS, render=anim_chart(brief.series, when, fonts)))
    ...
    cover_render(1.0).save(cover_path, "JPEG", quality=92)
```

- [ ] **Step 4: 통과 확인** + 전체 회귀 (기존 `tests/test_reels.py`의 scene_chart 테스트가 리팩터 후에도 통과해야 한다 — 최종 프레임 동등성이 계약).
- [ ] **Step 5: 커밋** — `feat(reels): 애니메이션 장면 3종 (카운트업 도입부·카드 슬라이드인·차트 드로잉)`

---

### Task 4: 음원 — assets/audio + encode(audio_path)

**Files:**
- Create: `econ_insta/audio.py`, `assets/audio/tracks.json`, `assets/audio/*.mp3` (2~3트랙)
- Modify: `econ_insta/reels.py` (encode 시그니처, `_ffmpeg_command` 분리)
- Test: `tests/test_audio.py` (신규), `tests/test_reels_motion.py` (ffmpeg 인자)

**Interfaces:**
- Produces: `audio.Track(path, title, artist, license, credit)` frozen dataclass, `audio.load_tracks() -> list[Track]`, `audio.pick_track(when: datetime, tracks: list[Track] | None = None) -> Track | None` (ISO 주차 % 트랙 수), `audio.needs_credit(track) -> bool` (cc-by만 True), `reels.encode(scenes, path, audio_path: Path | None = None)`, `reels._ffmpeg_command(path: Path, audio_path: Path | None) -> list[str]`, `reels.build_stock_reel(..., audio_path=None)`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_audio.py`:

```python
import unittest
from datetime import datetime
from pathlib import Path
from econ_insta import audio


def _track(name="a"):
    return audio.Track(path=Path(f"{name}.mp3"), title=name, artist="아무개",
                       license="cc-by-4.0", credit=f"{name} · 아무개 · CC BY 4.0")


class PickTrackTest(unittest.TestCase):
    def test_주차로_결정적_선택(self):
        tracks = [_track("a"), _track("b"), _track("c")]
        when = datetime(2026, 8, 30)          # ISO 35주차 → 35 % 3 == 2
        self.assertEqual(audio.pick_track(when, tracks), tracks[2])

    def test_트랙이_없으면_None(self):
        self.assertIsNone(audio.pick_track(datetime(2026, 8, 30), []))

    def test_cc_by만_크레딧_필요(self):
        self.assertTrue(audio.needs_credit(_track()))
        cc0 = audio.Track(Path("z.mp3"), "z", "x", "cc0", "")
        self.assertFalse(audio.needs_credit(cc0))


class LoadTracksTest(unittest.TestCase):
    def test_번들_tracks_json이_유효하다(self):
        for track in audio.load_tracks():
            self.assertTrue(track.path.exists(), track.path)
            self.assertIn(track.license, {"cc0", "cc-by-3.0", "cc-by-4.0"})
            if audio.needs_credit(track):
                self.assertTrue(track.credit.strip())
```

`tests/test_reels_motion.py`에 추가:

```python
from pathlib import Path


class FfmpegCommandTest(unittest.TestCase):
    def test_음원이_없으면_무음_트랙(self):
        cmd = reels._ffmpeg_command(Path("x.mp4"), None)
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=48000", cmd)

    def test_음원이_있으면_루프_입력과_shortest(self):
        cmd = reels._ffmpeg_command(Path("x.mp4"), Path("bgm.mp3"))
        self.assertIn("bgm.mp3", " ".join(cmd))
        self.assertIn("-stream_loop", cmd)
        self.assertIn("-shortest", cmd)
        self.assertNotIn("anullsrc=channel_layout=stereo:sample_rate=48000", cmd)
```

- [ ] **Step 2: 실패 확인** → ModuleNotFoundError / AttributeError.
- [ ] **Step 3: audio.py 구현**:

```python
"""릴스 배경 음원 — 로열티프리 트랙 번들 (스펙 §3).

people.json과 같은 원칙: 라이선스는 tracks.json에 기록으로 관리하고,
확인 안 되는 트랙은 넣지 않는다. CC BY는 캡션 🎵 크레딧이 의무다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import PROJECT_ROOT

AUDIO_DIR = PROJECT_ROOT / "assets" / "audio"
ALLOWED_LICENSES = {"cc0", "cc-by-3.0", "cc-by-4.0"}


class AudioError(RuntimeError):
    """트랙 메타데이터가 잘못됐다 — 라이선스 불명 트랙은 발행하면 안 된다."""


@dataclass(frozen=True)
class Track:
    path: Path
    title: str
    artist: str
    license: str
    credit: str


def load_tracks(audio_dir: Path = AUDIO_DIR) -> list[Track]:
    meta_path = audio_dir / "tracks.json"
    if not meta_path.exists():
        return []
    tracks = []
    for row in json.loads(meta_path.read_text(encoding="utf-8")):
        if row["license"] not in ALLOWED_LICENSES:
            raise AudioError(f"허용되지 않은 라이선스: {row['license']} ({row['file']})")
        track = Track(audio_dir / row["file"], row["title"], row["artist"],
                      row["license"], row.get("credit", ""))
        if not track.path.exists():
            raise AudioError(f"트랙 파일이 없습니다: {track.path}")
        if needs_credit(track) and not track.credit.strip():
            raise AudioError(f"CC BY 트랙에 크레딧이 없습니다: {row['file']}")
        tracks.append(track)
    return tracks


def pick_track(when: datetime, tracks: list[Track] | None = None) -> Track | None:
    """ISO 주차 % 트랙 수 — 결정적이고 주마다 바뀐다."""
    tracks = load_tracks() if tracks is None else tracks
    if not tracks:
        return None
    return tracks[when.isocalendar().week % len(tracks)]


def needs_credit(track: Track) -> bool:
    return track.license.startswith("cc-by")
```

- [ ] **Step 4: encode 리팩터** — 커맨드 조립을 순수 함수로 분리:

```python
def _ffmpeg_command(path: Path, audio_path: Path | None) -> list[str]:
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-",
    ]
    if audio_path is None:
        command += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    else:
        command += ["-stream_loop", "-1", "-i", str(audio_path)]
    command += [
        "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-g", str(FPS * 2), "-r", str(FPS), "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-shortest",
        "-movflags", "+faststart", str(path),
    ]
    return command
```

`encode(scenes, path, audio_path=None)`는 `command = _ffmpeg_command(path, audio_path)`만 쓰도록 교체(파이프 로직 불변). `build_stock_reel`에 `audio_path=None` 파라미터를 추가해 encode로 전달.

- [ ] **Step 5: 트랙 소싱 (수동 확인 단계)** — incompetech(Kevin MacLeod, CC BY)에서 차분한 코퍼레이트/일렉트로닉 계열 2~3곡을 내려받아 `assets/audio/`에 저장하고 tracks.json 작성:

```json
[
  {"file": "kevin-macleod-example.mp3", "title": "Example Title", "artist": "Kevin MacLeod",
   "license": "cc-by-4.0", "credit": "\"Example Title\" Kevin MacLeod (incompetech.com) · CC BY 4.0",
   "source_url": "https://incompetech.com/music/royalty-free/"}
]
```

곡명·URL은 다운로드 시점의 실제 값으로 기록한다(위는 형식 예시). **라이선스 페이지를 열어 CC BY임을 눈으로 확인**하고 credit 문자열을 기록. 파일 크기 합계 10MB 이내(리포 비대 방지). 다운로드가 막히면 이 단계에서 멈추고 사용자에게 대체 소스를 묻는다.

- [ ] **Step 6: 통과 확인** — test_audio + test_reels_motion + 전체 회귀.
- [ ] **Step 7: 커밋** — `feat(audio): 로열티프리 트랙 번들 + encode 음원 삽입`

---

### Task 5: daily.py — briefing.json 저장

**Files:**
- Modify: `econ_insta/daily.py` (`import json` 추가, `briefing_meta` 신규, `render_edition` 배선)
- Test: `tests/test_daily_briefing_meta.py` (신규)

**Interfaces:**
- Consumes: `summarizer.Briefing`(headline·cards·issue), `issues.Issue`(articles·sources).
- Produces: `daily.briefing_meta(briefing, edition: Edition, when: datetime) -> dict`, `render_edition`이 `out/<날짜>-<슬러그>/briefing.json` 저장. JSON 키: `date, edition, headline, issue_title, sources, article_count, cards`.

- [ ] **Step 1: 실패하는 테스트 작성**:

```python
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from econ_insta import daily

WHEN = datetime(2026, 8, 26, 19, 0)


def _briefing(issue=None):
    card = SimpleNamespace(title="금리 동결", source="매일경제·연합뉴스")
    return SimpleNamespace(headline="한은, 숨 고르기", cards=[card], issue=issue)


def _issue():
    articles = [SimpleNamespace(title="한은 기준금리 동결", source="매일경제"),
                SimpleNamespace(title="금통위 동결 배경", source="연합뉴스")]
    return SimpleNamespace(articles=articles, sources={"매일경제", "연합뉴스"})


class BriefingMetaTest(unittest.TestCase):
    def test_이슈가_있으면_전부_실린다(self):
        meta = daily.briefing_meta(_briefing(_issue()), daily.EDITIONS["kr"], WHEN)
        self.assertEqual(meta["date"], "2026-08-26")
        self.assertEqual(meta["edition"], "kr")
        self.assertEqual(meta["headline"], "한은, 숨 고르기")
        self.assertEqual(meta["issue_title"], "한은 기준금리 동결")
        self.assertEqual(meta["sources"], ["매일경제", "연합뉴스"])
        self.assertEqual(meta["article_count"], 2)
        self.assertEqual(meta["cards"], [{"title": "금리 동결", "source": "매일경제·연합뉴스"}])

    def test_이슈가_None이어도_메타는_만들어진다(self):
        meta = daily.briefing_meta(_briefing(None), daily.EDITIONS["kr"], WHEN)
        self.assertIsNone(meta["issue_title"])
        self.assertEqual(meta["sources"], [])
        self.assertEqual(meta["article_count"], 0)

    def test_json_직렬화_왕복(self):
        meta = daily.briefing_meta(_briefing(_issue()), daily.EDITIONS["kr"], WHEN)
        self.assertEqual(json.loads(json.dumps(meta, ensure_ascii=False)), meta)
```

- [ ] **Step 2: 실패 확인** → AttributeError.
- [ ] **Step 3: 구현**:

```python
def briefing_meta(briefing, edition: Edition, when: datetime) -> dict:
    """주간 릴스의 소재가 되는 이슈 메타데이터 (스펙 §4).

    국내 RSS는 D-2면 기사가 증발하므로 데일리가 매일 남겨야 주간이 읽을 수 있다.
    issue가 None(그래픽 폴백 날)이어도 파일은 만든다 — '없었음'도 기록이다.
    """
    issue = briefing.issue
    return {
        "date": f"{when:%Y-%m-%d}",
        "edition": edition.slug,
        "headline": briefing.headline,
        "issue_title": issue.articles[0].title if issue else None,
        "sources": sorted(issue.sources) if issue else [],
        "article_count": len(issue.articles) if issue else 0,
        "cards": [{"title": c.title, "source": c.source} for c in briefing.cards],
    }
```

`render_edition`의 caption 저장 직후에 배선:

```python
    (out / "briefing.json").write_text(
        json.dumps(briefing_meta(briefing, edition, brief.collected_at), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
```

배선 테스트("가드가 있다 ≠ 작동한다"): `unittest.mock.patch`로 `daily.collect`·`daily.naver_rerank`·`daily.rank_issues`·`daily.summarize`·`daily.build_background`·`daily.renderer`를 가짜로 갈고, `daily.output_dir`을 patch해 `tempfile.TemporaryDirectory` 경로를 돌려주게 한 뒤 `render_edition` 실행 → `<tmp>/briefing.json` 존재와 `date` 키를 단언.

- [ ] **Step 4: 통과 확인** + 전체 회귀.
- [ ] **Step 5: 커밋** — `feat(daily): briefing.json 저장 — 주간 릴스 소재 공급`

---

### Task 6: weekly_reel.py — 후보 로드·이슈 선정·대본 생성

**Files:**
- Create: `econ_insta/weekly_reel.py`
- Test: `tests/test_weekly_reel.py` (신규)

**Interfaces:**
- Consumes: Task 5의 briefing.json 스키마, `blog_brief._generate`(blog_brief.py:199)의 호출 규약(모델 `claude-sonnet-5`, `thinking={"type":"adaptive"}`, `output_config={"effort":..., "format":{"type":"json_schema","schema":SCHEMA}}`, stop_reason 검사), `blog_brief.summarize_blog`(221행)의 생성→audit→재생성 1회 구조, `factcheck.unsupported_amounts`, `summarizer.residual_hanja`·`replace_hanja`, `backgrounds.available_people`(SYSTEM 자동 삽입 — blog_brief.SYSTEM 관용구).
- Produces:
  - `WeeklyError(RuntimeError)`
  - `WeeklyCandidate(date, edition, headline, issue_title, sources, article_count, cards)` frozen dataclass
  - `load_candidates(out_root: Path, today: datetime, days: int = 7) -> list[WeeklyCandidate]` — briefing.json 전수 로드, `issue_title is None`인 날 제외, (매체 수, 기사 수) 내림차순 정렬
  - `WeeklyScript(hook, reasons: list[Reason], ticker_label, bg_query, people, chosen: WeeklyCandidate)`
  - `_chosen(payload, candidates) -> WeeklyCandidate` — issue_index 가드
  - `write_script(candidates, client=None) -> WeeklyScript`
  - `TICKERS: dict[str, str]`, `HOOK_MAX=24`, `MAX_CANDIDATES=7`

- [ ] **Step 1: 실패하는 테스트 작성** — FakeClient는 `tests/`의 기존 blog_brief/summarizer Fake 관용구를 복사해 payload 직접 주입:

```python
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from econ_insta import weekly_reel

TODAY = datetime(2026, 8, 30, 19, 0)


def _write_briefing(root, date, edition, sources, count, title="이슈"):
    d = root / f"{date}-{edition}"
    d.mkdir(parents=True)
    (d / "briefing.json").write_text(json.dumps({
        "date": date, "edition": edition, "headline": f"{title} 훅",
        "issue_title": title, "sources": sources, "article_count": count,
        "cards": [{"title": f"{title} 카드", "source": sources[0] if sources else "매일경제"}],
    }, ensure_ascii=False), encoding="utf-8")


def _candidate(title="이슈", sources=("매일경제",), count=3):
    return weekly_reel.WeeklyCandidate(
        date="2026-08-26", edition="kr", headline=f"{title} 훅", issue_title=title,
        sources=list(sources), article_count=count,
        cards=[{"title": f"{title} 카드", "source": sources[0]}])


class LoadCandidatesTest(unittest.TestCase):
    def test_매체수_기사수_내림차순(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_briefing(root, "2026-08-25", "kr", ["매일경제"], 9, "단독 이슈")
            _write_briefing(root, "2026-08-26", "kr", ["매일경제", "연합뉴스", "한국경제"], 5, "대형 이슈")
            _write_briefing(root, "2026-08-27", "global", ["WSJ", "이코노미스트"], 7, "해외 이슈")
            got = weekly_reel.load_candidates(root, TODAY)
            self.assertEqual([c.issue_title for c in got], ["대형 이슈", "해외 이슈", "단독 이슈"])

    def test_7일_밖과_이슈_없는_날은_제외(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_briefing(root, "2026-08-10", "kr", ["매일경제"], 3, "옛날 이슈")
            d = root / "2026-08-26-global"
            d.mkdir()
            (d / "briefing.json").write_text(json.dumps({
                "date": "2026-08-26", "edition": "global", "headline": "훅",
                "issue_title": None, "sources": [], "article_count": 0, "cards": [],
            }), encoding="utf-8")
            self.assertEqual(weekly_reel.load_candidates(root, TODAY), [])


class IssueIndexGuardTest(unittest.TestCase):
    def test_범위밖_번호와_타입이상은_전부_스킵(self):
        candidates = [_candidate("갑"), _candidate("을")]
        for bad in (0, 3, 99, -1, True, "2", None):
            with self.assertRaises(weekly_reel.WeeklyError):
                weekly_reel._chosen({"issue_index": bad}, candidates)

    def test_유효한_번호는_그_후보를_돌려준다(self):
        candidates = [_candidate("갑"), _candidate("을")]
        self.assertEqual(weekly_reel._chosen({"issue_index": 2}, candidates).issue_title, "을")
```

- [ ] **Step 2: 실패 확인** → ModuleNotFoundError.
- [ ] **Step 3: 구현** — 핵심 골격:

```python
TICKERS = {
    "코스피": "^KS11", "코스닥": "^KQ11", "원/달러": "KRW=X", "나스닥": "^IXIC",
    "S&P500": "^GSPC", "WTI": "CL=F", "금": "GC=F", "비트코인": "BTC-USD",
    "삼성전자": "005930.KS", "SK하이닉스": "000660.KS", "현대차": "005380.KS",
    "LG에너지솔루션": "373220.KS", "네이버": "035420.KS", "카카오": "035720.KS",
}
HOOK_MAX = 24            # summarizer HEADLINE_MAX와 동일
REASON_TITLE_MAX = 26
REASON_BODY_MAX = 90
MAX_CANDIDATES = 7

SCHEMA = {
    "type": "object",
    "properties": {
        "issue_index": {"type": "integer"},
        "hook": {"type": "string"},
        "reasons": {"type": "array", "minItems": 2, "maxItems": 3, "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "body": {"type": "string"},
                           "source": {"type": "string"}},
            "required": ["title", "body", "source"], "additionalProperties": False}},
        "ticker_label": {"type": "string", "enum": sorted(TICKERS)},
        "bg_query": {"type": "string"},
        "people": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["issue_index", "hook", "reasons", "ticker_label", "bg_query", "people"],
    "additionalProperties": False,
}


def _chosen(payload: dict, candidates: list[WeeklyCandidate]) -> WeeklyCandidate:
    """이슈 계약(2026-07-17)과 같은 가드 — bool은 isinstance(int)를 뚫는다.
    주간은 틀린 이슈로 나가느니 스킵이 낫다: 폴백 금지."""
    index = payload.get("issue_index")
    if type(index) is not int or not 1 <= index <= len(candidates):
        raise WeeklyError(f"issue_index가 유효하지 않습니다: {index!r}")
    return candidates[index - 1]
```

`load_candidates`: `out_root.glob("*/briefing.json")` 순회, JSON의 `date`를 `datetime.strptime(%Y-%m-%d)`로 파싱해 `(today - date).days < days`만, `issue_title is None` 제외, `sorted(key=lambda c: (len(c.sources), c.article_count), reverse=True)`.

`write_script(candidates, client=None)`: 상위 `MAX_CANDIDATES` 후보를 `[이슈 N] 제목 / 매체 / 기사수 / 카드제목들` 형식으로 프롬프트에 싣고 `_generate` 호출(blog_brief.py:199를 이 모듈 SCHEMA·SYSTEM으로 복제) → `_chosen` → `replace_hanja`를 hook·각 reason title/body에 적용 → audit(각 텍스트에 `residual_hanja` + `unsupported_amounts(text, prompt)` + 길이 한도 HOOK_MAX/REASON_TITLE_MAX/REASON_BODY_MAX) → 위반 시 재생성 1회(blog_brief.py:235 retry_prompt 관용구) → 남으면 `WeeklyError`. SYSTEM은 모듈 상수: 이슈 선택(issue_index)·훅(한자 금지·자료에 없는 수치 금지)·이유 2~3개(source는 후보의 실제 매체명만)·ticker_label(enum에서 이슈와 가장 관련 깊은 것, 없으면 "코스피")·bg_query(영어, 촬영 가능한 구체 대상)·people(`available_people()` 목록 삽입 — blog_brief.SYSTEM 관용구 재사용).

- [ ] **Step 4: 통과 확인** + 전체 회귀.
- [ ] **Step 5: 커밋** — `feat(weekly): 주간 릴스 후보 로드·이슈 선정·대본 생성`

---

### Task 7: weekly_reel.py — 차트·렌더·캡션·발행 + CLI

**Files:**
- Modify: `econ_insta/weekly_reel.py`, `econ_insta/reels.py` (publish_reel 재시도)
- Test: `tests/test_weekly_reel.py` (추가)

**Interfaces:**
- Consumes: Task 3 `anim_*`, Task 4 `audio.pick_track`·`needs_credit`·`reels.encode(audio_path=)`, `backgrounds.build_background([], bg_query, errors=, issue=None, headline=hook)` — daily.py:131 호출 형태(people은 script.people로), `stock_brief.Series`, yfinance, `daily.DISCLAIMER` 문구 동일.
- Produces:
  - `weekly_series(label: str) -> Series` — `TICKERS[label]` yfinance `Ticker(...).history(period="3mo", auto_adjust=False)`, NaN은 `v == v`로 제거, `intraday=False`(일요일 실행 전제), `name=label`, currency는 원화 티커(.KS·^KS11·^KQ11·KRW=X)만 "원" 나머지 "pt" — stock_brief의 기존 통화 처리 관용구를 확인해 따른다
  - `build_caption(script: WeeklyScript, when: datetime, credits: tuple[str, ...], track: Track | None) -> str`
  - `build_weekly(when=None, client=None) -> Path | None` — 후보 0건이면 None(스킵), 아니면 out 디렉터리 반환. 렌더 산출: reel.mp4·reel-cover.jpg·caption.txt
  - `reels._video_hosting_ready(url, *, attempts=10, delay=30.0, sleep=time.sleep, get=requests.get) -> bool`, `publish_reel(out_dir, *, attempts=10, delay=30.0, sleep=time.sleep)` — 호스팅 확인 재시도(Pages 빌드 1~2분 — 기존 단발 확인은 크론에서 거의 매번 죽는다)
  - CLI: `python -m econ_insta.weekly_reel --render|--publish` (daily.main:180 argparse 관용구). `--render`가 후보 0건이면 `skipped.txt`를 남기고 exit 0, `--publish`는 `skipped.txt`가 있으면 exit 0.

- [ ] **Step 1: 실패하는 테스트 작성**:

```python
from econ_insta import audio, reels
from econ_insta.stock_brief import Reason


def _script():
    return weekly_reel.WeeklyScript(
        hook="코스피, 한 주가 무너졌다",
        reasons=[Reason(title="외국인 이탈", body="외국인 순매도가 이어졌다.", source="매일경제·연합뉴스"),
                 Reason(title="AI 회의론", body="반도체 투자심리가 꺾였다.", source="매일경제")],
        ticker_label="코스피", bg_query="stock exchange", people=[],
        chosen=_candidate("코스피 급락", ("매일경제", "연합뉴스"), 8))


class FakeResponse:
    def __init__(self, status_code, content_type):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.content = b""


class WeeklyCaptionTest(unittest.TestCase):
    def test_cc_by_음원은_크레딧_줄이_실린다(self):
        track = audio.Track(Path("bgm.mp3"), "곡", "작곡가", "cc-by-4.0", "곡 · 작곡가 · CC BY 4.0")
        caption = weekly_reel.build_caption(_script(), TODAY, credits=(), track=track)
        self.assertIn("🎵 곡 · 작곡가 · CC BY 4.0", caption)

    def test_cc0_음원은_크레딧_줄이_없다(self):
        track = audio.Track(Path("bgm.mp3"), "곡", "작곡가", "cc0", "")
        caption = weekly_reel.build_caption(_script(), TODAY, credits=(), track=track)
        self.assertNotIn("🎵", caption)

    def test_복합_출처는_쪼개서_dedup(self):
        caption = weekly_reel.build_caption(_script(), TODAY, credits=(), track=None)
        self.assertIn("출처 · 매일경제 · 연합뉴스", caption)

    def test_투자유의_문구가_있다(self):
        caption = weekly_reel.build_caption(_script(), TODAY, credits=(), track=None)
        self.assertIn("투자 권유가 아닙니다", caption)


class PublishRetryTest(unittest.TestCase):
    def test_호스팅_미전파면_재시도한다(self):
        responses = [FakeResponse(404, ""), FakeResponse(200, "video/mp4")]
        sleeps = []
        ok = reels._video_hosting_ready("https://x/reel.mp4", attempts=3, delay=1.0,
                                        sleep=sleeps.append, get=lambda *a, **k: responses.pop(0))
        self.assertTrue(ok)
        self.assertEqual(sleeps, [1.0])

    def test_끝내_실패하면_False(self):
        ok = reels._video_hosting_ready("https://x/reel.mp4", attempts=2, delay=0.0,
                                        sleep=lambda s: None,
                                        get=lambda *a, **k: FakeResponse(404, ""))
        self.assertFalse(ok)
```

- [ ] **Step 2: 실패 확인.**
- [ ] **Step 3: 구현** — `_video_hosting_ready`는 daily.hosting_ready(daily.py:89)의 단일 URL 축소판(200 + `Content-Type.startswith("video/")`). publish_reel의 기존 단발 확인 블록(reels.py:407-413)을 이 함수 호출로 교체. build_caption:

```python
def build_caption(script, when, credits=(), track=None):
    sources = sorted({s.strip() for r in script.reasons for s in r.source.split("·") if s.strip()})
    lines = [script.hook, "", f"{when:%Y년 %m월 %d일} 주간 이슈 브리핑", ""]
    lines += [f"· {r.title}" for r in script.reasons]
    lines += ["", "출처 · " + " · ".join(sources)]
    lines += [f"📷 {credit}" for credit in credits]
    if track is not None and needs_credit(track):
        lines.append(f"🎵 {track.credit}")
    lines += ["", DISCLAIMER, "", HASHTAGS]
    return "\n".join(lines)
```

(DISCLAIMER는 daily와 동일 문구를 모듈 상수로, HASHTAGS는 `"#경제 #주식 #증시 #주간브리핑 #투자 #경제뉴스"`.) `build_weekly`: load_candidates(PROJECT_ROOT/"out", now_kst()) → 0건이면 out 디렉터리에 `skipped.txt` 쓰고 None → write_script → weekly_series(script.ticker_label) → build_background(script.people, script.bg_query, errors=[], issue=None, headline=script.hook) → 장면 조립(도입부 `pct=series.change_pct(5)`, kicker="주간 이슈 브리핑") → `audio.pick_track(when)` → encode(audio_path=track.path if track else None) → cover·caption 저장. out 디렉터리 = `PROJECT_ROOT/"out"/f"{when:%Y-%m-%d}-weekly-reel"`.

- [ ] **Step 4: 통과 확인** + 전체 회귀.
- [ ] **Step 5: 커밋** — `feat(weekly): 주간 릴스 파이프라인 (차트·렌더·캡션·발행 재시도)`

---

### Task 8: 주간 크론 워크플로

**Files:**
- Create: `.github/workflows/weekly-reel.yml`

**Interfaces:**
- Consumes: Task 7 CLI, 기존 시크릿(ANTHROPIC_API_KEY·IG_ACCESS_TOKEN·IG_USER_ID), daily-briefing.yml의 마커 가드 패턴(61-68행).

- [ ] **Step 1: 워크플로 작성**:

```yaml
# 주간 릴스 자동 발행 (스펙 docs/superpowers/specs/2026-08-26-motion-weekly-reels-design.md)
# KST 일요일 19:00. GitHub cron은 밀리거나 빠진다 — 25분 뒤 예비 크론 + published.txt 마커.

name: Weekly reel

on:
  schedule:
    - cron: "0 10 * * 0"    # KST 일요일 19:00
    - cron: "25 10 * * 0"   # 예비
  workflow_dispatch:

permissions:
  contents: write

concurrency: weekly-reel

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install -r requirements.txt

      - name: Resolve date
        id: meta
        run: echo "date=$(TZ=Asia/Seoul date +%F)" >> "$GITHUB_OUTPUT"

      - name: Skip if already published
        id: guard
        run: |
          marker="out/${{ steps.meta.outputs.date }}-weekly-reel/published.txt"
          if [ "${{ github.event_name }}" = "schedule" ] && [ -f "$marker" ]; then
            echo "skip=true" >> "$GITHUB_OUTPUT"
            echo "이번 주 릴스는 이미 발행됨 ($(cat "$marker")) — 종료"
          fi

      - name: Render
        if: steps.guard.outputs.skip != 'true'
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: python -m econ_insta.weekly_reel --render

      - name: Host video (Pages)
        if: steps.guard.outputs.skip != 'true'
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add -f out/
          git diff --cached --quiet || git commit -m "out: weekly reel ${{ steps.meta.outputs.date }} [skip ci]"
          git pull --rebase origin main
          git push origin main

      - name: Publish
        if: steps.guard.outputs.skip != 'true'
        env:
          IG_ACCESS_TOKEN: ${{ secrets.IG_ACCESS_TOKEN }}
          IG_USER_ID: ${{ secrets.IG_USER_ID }}
        run: python -m econ_insta.weekly_reel --publish

      - name: Mark published
        if: steps.guard.outputs.skip != 'true' && github.event_name == 'schedule'
        run: |
          out="out/${{ steps.meta.outputs.date }}-weekly-reel"
          if [ -f "$out/skipped.txt" ]; then
            echo "이번 주는 소재 없음 — 마커 없이 종료"
            exit 0
          fi
          marker="$out/published.txt"
          date -u +%FT%TZ > "$marker"
          git add -f "$marker"
          git commit -m "out: weekly reel ${{ steps.meta.outputs.date }} 발행 마커 [skip ci]"
          git pull --rebase origin main
          git push origin main
```

(후보 0건이면 --render가 skipped.txt만 남기고 exit 0 → Host는 그 파일을 커밋, Publish는 exit 0, 마커는 안 남는다.)

- [ ] **Step 2: 문법 검증** — `git diff --check` + YAML 파싱(`python -c "import json,sys;..."` 대신 PyYAML이 requirements에 있으면 safe_load, 없으면 육안 + actions 탭 dispatch로 검증).
- [ ] **Step 3: 커밋** — `ci: 주간 릴스 크론 (일 19:00 KST + 예비 + 마커 가드)`

---

### Task 9: 실물 검증 + 배포

**Files:** 없음 (검증·배포 단계)

- [ ] **Step 1: 전체 테스트** — `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -q` 전부 통과.
- [ ] **Step 2: 렌더 시간 실측** — 로컬 `--render` 실행 시간 기록. 20초 영상 기준 수 분 초과면 FPS 24 하향 검토(스펙 §9).
- [ ] **Step 3: 실데이터 렌더** — briefing.json이 아직 없으므로: 오늘 데일리를 로컬로 한 번 렌더해(`python -m econ_insta.daily --edition kr --render` — Task 5 반영분이 briefing.json을 만든다) 소재를 만들고 `python -m econ_insta.weekly_reel --render`. mp4를 열어 **카운트업·슬라이드인·차트 드로잉·음원을 눈으로 확인**하고 사용자에게 보여준다.
- [ ] **Step 4: 캡션 확인** — caption.txt를 사용자와 함께 확인 (발행 후 수정 불가).
- [ ] **Step 5: push (사용자 승인 필수)** — 승인 후 `git push origin main`. 그날 저녁 크론부터 briefing.json이 쌓인다.
- [ ] **Step 6: 첫 발행 확인** — 첫 일요일 크론(또는 dispatch) 결과를 확인. 실패 시 run 로그로 진단.

---

## Self-Review 기록

- 스펙 §2(모션)→Task 1~3, §3(음원)→Task 4, §4(briefing.json)→Task 5, §5(주간 파이프라인)→Task 6~7, §6(크론)→Task 8, §7(검증)→각 태스크 Step + Task 9. 커버 완료.
- 시그니처 일관성: `anim_cover(headline, pct, when, fonts, kicker, background)` — Task 3 정의 = Task 7 소비(pct=`series.change_pct(5)`, kicker="주간 이슈 브리핑"). `encode(scenes, path, audio_path)` — Task 4 정의 = Task 7 소비. `Track` — Task 4 정의 = Task 7 캡션 소비. `_video_hosting_ready` — Task 7 정의·소비. 일치.
- 구현자가 저장소에서 확인할 관용구(파일 위치 명시됨): FontSet 테스트 생성(tests/test_reels.py), FakeClient(tests의 blog_brief/summarizer 계열), `available_people()` SYSTEM 삽입(blog_brief.py), stock_brief의 통화 처리.
