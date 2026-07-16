# -*- coding: utf-8 -*-
"""개편 방향 목업 — 다크 프리미엄 + 얼굴 표지 + C 버밀리언 + 단일 이슈 후크 카드
+ 스파크라인 지표 + 주간 릴스 키프레임. 브레인스토밍 검증용 일회성."""
from __future__ import annotations
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = Path(__file__).parent
FD = HERE / "fonts"
PEOPLE = Path(r"C:\Users\user\econ-insta\assets\people")
OUT = HERE / "mock"
OUT.mkdir(exist_ok=True)
W, H = 1080, 1350
M = 92

# ---------- 팔레트 (스펙 §5.2) ----------
BG_TOP = (11, 14, 22)      # #0B0E16
BG_BOT = (20, 16, 32)      # #141020
GOLD = (242, 197, 78)      # #F2C54E
WHITE = (245, 246, 250)    # #F5F6FA
MUTE = (139, 147, 167)     # #8B93A7
BODY = (206, 210, 222)     # #CED2DE
RULE = (42, 48, 62)        # #2A303E
UP = (240, 90, 96)         # #F05A60 상승 빨강
DOWN = (91, 141, 239)      # #5B8DEF 하락 파랑
VERM = (240, 78, 42)       # #F04E2A
INK = (22, 17, 14)         # #16110E


def f(weight, size):
    return ImageFont.truetype(str(FD / f"Pretendard-{weight}.otf"), size)


def vgrad(w, h, top, bot):
    img = Image.new("RGB", (w, h), top)
    px = img.load()
    for y in range(h):
        t = y / (h - 1)
        c = tuple(round(top[i] + (bot[i] - top[i]) * t) for i in range(3))
        for x in range(w):
            px[x, y] = c
    return img


def glow(base, xy, radius, color, alpha):
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([xy[0] - radius, xy[1] - radius, xy[0] + radius, xy[1] + radius],
              fill=color + (alpha,))
    layer = layer.filter(ImageFilter.GaussianBlur(radius // 2))
    return Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")


def grid(img, color=(255, 255, 255), step=108, alpha=10):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for x in range(0, img.size[0], step):
        d.line([(x, 0), (x, img.size[1])], fill=color + (alpha,), width=1)
    for y in range(0, img.size[1], step):
        d.line([(0, y), (img.size[0], y)], fill=color + (alpha,), width=1)
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def wrap(draw, text, font, max_w):
    out = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            trial = (cur + " " + word).strip()
            if draw.textlength(trial, font=font) <= max_w:
                cur = trial
            else:
                if cur:
                    out.append(cur)
                if draw.textlength(word, font=font) > max_w:
                    piece = ""
                    for ch in word:
                        if draw.textlength(piece + ch, font=font) <= max_w:
                            piece += ch
                        else:
                            out.append(piece); piece = ch
                    cur = piece
                else:
                    cur = word
        out.append(cur)
    return out


def draw_lines(draw, lines, font, x, y, fill, lh):
    asc, desc = font.getmetrics()
    step = round((asc + desc) * lh)
    for ln in lines:
        draw.text((x, y), ln, font=font, fill=fill)
        y += step
    return y


def cover_fit(im, w, h):
    im = im.convert("RGB")
    sr, dr = im.width / im.height, w / h
    if sr > dr:
        nh, nw = h, round(h * sr)
    else:
        nw, nh = w, round(w / sr)
    im = im.resize((nw, nh))
    x, y = (nw - w) // 2, 0  # 얼굴은 상단 정렬(눈·표정 살림)
    return im.crop((x, y, x + w, y + h))


def bg_dark():
    return grid(glow(vgrad(W, H, BG_TOP, BG_BOT), (170, 1040), 640, GOLD, 30),
                step=108, alpha=10)


def kicker_pill(d, x, y, text, color=GOLD):
    kf = f("Bold", 38)
    tw = d.textlength(text, font=kf)
    d.rounded_rectangle([x, y, x + tw + 68, y + 78], radius=39, outline=color, width=3)
    d.text((x + 34, y + 17), text, font=kf, fill=color)
    return y + 78


def footer(d, cta="넘겨서 확인하세요  →", fg=(210, 214, 224)):
    ff = f("SemiBold", 30)
    d.text((M, H - M - 30), "@mansuki101", font=ff, fill=fg)
    d.text((W - M - d.textlength(cta, font=ff), H - M - 30), cta, font=ff, fill=MUTE)


# ---------- 1. 표지 A — 얼굴 전면 (다크 프리미엄) ----------
def cover_face():
    face = cover_fit(Image.open(PEOPLE / "powell.jpg"), W, H)
    # 스크림: 상단 약하게(키커용) + 하단 강하게(헤드라인 앉힘)
    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    px = scrim.load()
    for y in range(H):
        t = y / (H - 1)
        if t < 0.28:
            a = int(150 * (1 - t / 0.28) + 60)      # 상단 60~210
        elif t < 0.55:
            a = 55
        else:
            a = int(55 + (t - 0.55) / 0.45 * 210)    # 하단 55~265
        a = min(a, 255)
        for x in range(W):
            px[x, y] = (8, 10, 16, a)
    img = Image.alpha_composite(face.convert("RGBA"), scrim).convert("RGB")
    d = ImageDraw.Draw(img)
    kicker_pill(d, M, M, "마켓 브리핑")
    d.text((M, M + 100), "2026년 07월 16일", font=f("Regular", 30), fill=(214, 218, 228))
    hf = f("Black", 110)
    lines = wrap(d, "파월의 한 마디,\n시장이 얼어붙었다", hf, W - 2 * M)
    asc, desc = hf.getmetrics(); step = round((asc + desc) * 1.2)
    y0 = H - M - 150 - step * len(lines)
    d.rectangle([M, y0 - 44, M + 116, y0 - 32], fill=GOLD)
    draw_lines(d, lines, hf, M, y0, WHITE, 1.2)
    footer(d)
    img.save(OUT / "01-cover-face.jpg", quality=90)


# ---------- 2. 표지 A — 그래픽(데이터 히어로), 얼굴 없을 때 ----------
def cover_graphic():
    img = bg_dark()
    d = ImageDraw.Draw(img)
    kicker_pill(d, M, M, "마켓 브리핑")
    d.text((M, M + 100), "2026년 07월 16일", font=f("Regular", 30), fill=MUTE)
    # 데이터 히어로: 폭락 라인 + 면적
    cx0, cx1, cy0, cy1 = M, W - M, 470, 760
    series = [1.0 - 0.62 * (0.5 - 0.5 * math.cos(math.pi * i / 40))
              - 0.05 * math.sin(i / 3.0) for i in range(41)]
    lo, hi = min(series), max(series)
    pts = [(cx0 + (cx1 - cx0) * i / (len(series) - 1),
            cy1 - (cy1 - cy0) * (v - lo) / (hi - lo)) for i, v in enumerate(series)]
    area = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(area).polygon(pts + [(cx1, cy1), (cx0, cy1)], fill=DOWN + (46,))
    img = Image.alpha_composite(img.convert("RGBA"), area).convert("RGB")
    d = ImageDraw.Draw(img)
    d.line(pts, fill=DOWN, width=6, joint="curve")
    d.ellipse([pts[-1][0] - 11, pts[-1][1] - 11, pts[-1][0] + 11, pts[-1][1] + 11], fill=DOWN)
    hf = f("Black", 118)
    lines = wrap(d, "코스피\n3만 붕괴", hf, W - 2 * M)
    asc, desc = hf.getmetrics(); step = round((asc + desc) * 1.16)
    y0 = H - M - 150 - step * len(lines)
    d.rectangle([M, y0 - 44, M + 116, y0 - 32], fill=GOLD)
    draw_lines(d, lines, hf, M, y0, WHITE, 1.16)
    footer(d)
    img.save(OUT / "02-cover-graphic.jpg", quality=90)


# ---------- 3. 표지 C — 버밀리언 풀블리드 (큰 이슈 변주) ----------
def cover_verm():
    img = Image.new("RGB", (W, H), VERM)
    d = ImageDraw.Draw(img)
    d.text((M, M), "MARKET BRIEFING", font=f("ExtraBold", 34), fill=(255, 255, 255))
    dt = "2026.07.16"
    d.text((W - M - d.textlength(dt, font=f("SemiBold", 32)), M + 2), dt,
           font=f("SemiBold", 32), fill=(255, 238, 232))
    d.line([(M, M + 64), (W - M, M + 64)], fill=(255, 255, 255), width=3)
    hf = f("Black", 122)
    lines = wrap(d, "연준\n쇼크", hf, W - 2 * M)
    asc, desc = hf.getmetrics(); step = round((asc + desc) * 1.1)
    y0 = (H - step * len(lines)) // 2 - 30
    draw_lines(d, lines, hf, M, y0, INK, 1.1)
    d.text((M, H - M - 150), "01", font=f("Black", 150), fill=(255, 255, 255))
    ff = f("SemiBold", 30); cta = "넘겨서 →"
    d.text((W - M - d.textlength(cta, font=ff), H - M - 42), cta, font=ff, fill=(255, 238, 232))
    img.save(OUT / "03-cover-vermilion.jpg", quality=90)


# ---------- 4·5. 본문 후크 카드 (단일 이슈 서사) ----------
def content(idx, total, num, title, body, source, role):
    img = grid(vgrad(W, H, (12, 15, 23), (16, 13, 26)), step=108, alpha=8)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 12, H], fill=GOLD)
    d.text((M, M - 8), num, font=f("Black", 128), fill=GOLD)
    pg = f"{idx} / {total}"
    d.text((W - M - d.textlength(pg, font=f("SemiBold", 34)), M + 40), pg,
           font=f("SemiBold", 34), fill=MUTE)
    d.text((W - M - d.textlength(role, font=f("Bold", 30)), M + 92), role,
           font=f("Bold", 30), fill=GOLD)
    # 세로 중앙 정렬 블록
    tf, bf = f("ExtraBold", 74), f("Regular", 46)
    tlines = wrap(d, title, tf, W - 2 * M)
    blines = wrap(d, body, bf, W - 2 * M)
    ta, td = tf.getmetrics(); tstep = round((ta + td) * 1.22)
    ba, bd = bf.getmetrics(); bstep = round((ba + bd) * 1.5)
    block = tstep * len(tlines) + 40 + 2 + 40 + bstep * len(blines)
    y = (H - block) // 2
    y = draw_lines(d, tlines, tf, M, y, WHITE, 1.22)
    y += 20
    d.line([(M, y), (W - M, y)], fill=RULE, width=2)
    y += 40
    draw_lines(d, blines, bf, M, y, BODY, 1.5)
    d.text((M, H - M - 26), f"출처 · {source}", font=f("Regular", 30), fill=MUTE)
    img.save(OUT / f"{idx+3:02d}-content-{idx}.jpg", quality=90)


# ---------- 6. 지표 카드 — 리스트 + 미니 스파크라인 ----------
def indicators():
    img = grid(vgrad(W, H, (12, 15, 23), (15, 12, 24)), step=108, alpha=8)
    d = ImageDraw.Draw(img)
    d.text((M, M), "오늘의 지표", font=f("ExtraBold", 64), fill=WHITE)
    d.text((M, M + 84), "2026.07.16 · 종가 기준", font=f("Regular", 30), fill=MUTE)
    rows = [
        ("코스피", "2,981.4", -2.14), ("코스닥", "842.7", -1.92),
        ("원/달러", "1,392.0", +0.58), ("나스닥", "20,114", -1.06),
        ("S&P 500", "6,204", -0.73), ("WTI", "68.4", +1.21),
        ("금", "3,412", +0.44), ("비트코인", "94,850", -3.05),
    ]
    top, bottom = M + 176, H - M - 60
    rh = (bottom - top) / len(rows)
    for i, (name, price, chg) in enumerate(rows):
        cy = top + rh * i
        col = UP if chg >= 0 else DOWN
        d.text((M, cy + rh / 2 - 30), name, font=f("SemiBold", 42), fill=WHITE)
        # 스파크라인 박스 (가운데)
        bx0, bx1 = M + 300, M + 300 + 300
        by0, by1 = cy + 16, cy + rh - 16
        n = 24
        pts = []
        for k in range(n):
            base = 0.5 + 0.42 * (k / (n - 1)) * (1 if chg >= 0 else -1)
            v = base + 0.12 * math.sin(k / 2.1 + i) + 0.06 * math.sin(k / 1.3)
            pts.append(v)
        lo, hi = min(pts), max(pts)
        P = [(bx0 + (bx1 - bx0) * k / (n - 1),
              by1 - (by1 - by0) * (v - lo) / (hi - lo)) for k, v in enumerate(pts)]
        area = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(area).polygon(P + [(bx1, by1), (bx0, by1)], fill=col + (40,))
        img_rgba = Image.alpha_composite(img.convert("RGBA"), area)
        img.paste(img_rgba.convert("RGB"))
        d = ImageDraw.Draw(img)
        d.line(P, fill=col, width=4, joint="curve")
        d.ellipse([P[-1][0] - 6, P[-1][1] - 6, P[-1][0] + 6, P[-1][1] + 6], fill=col)
        # 값 / 등락
        pv = f("Bold", 42)
        d.text((W - M - d.textlength(price, font=pv), cy + rh / 2 - 46), price,
               font=pv, fill=WHITE)
        ct = f"{chg:+.2f}%"
        cf = f("Bold", 32)
        d.text((W - M - d.textlength(ct, font=cf), cy + rh / 2 + 4), ct, font=cf, fill=col)
        if i < len(rows) - 1:
            d.line([(M, cy + rh), (W - M, cy + rh)], fill=(34, 39, 52), width=1)
    img.save(OUT / "07-indicators.jpg", quality=90)


# ---------- 7. 주간 릴스 키프레임 (9:16) ----------
def reel():
    RW, RH = 1080, 1920
    img = grid(glow(vgrad(RW, RH, BG_TOP, BG_BOT), (200, 1500), 720, GOLD, 26),
               step=120, alpha=9)
    d = ImageDraw.Draw(img)
    rm = 96
    kf = f("Bold", 40)
    tw = d.textlength("주간 브리핑", font=kf)
    d.rounded_rectangle([rm, 150, rm + tw + 72, 150 + 84], radius=42, outline=GOLD, width=3)
    d.text((rm + 36, 150 + 20), "주간 브리핑", font=kf, fill=GOLD)
    d.text((rm, 270), "7월 3주차 · 이번 주 최대 이슈", font=f("Regular", 36), fill=MUTE)
    hf = f("Black", 132)
    lines = wrap(d, "연준이\n쏘아올린\n공", hf, RW - 2 * rm)
    y = draw_lines(d, lines, hf, rm, 470, WHITE, 1.12)
    d.rectangle([rm, 460, rm + 130, 474], fill=GOLD)
    # 차트
    cx0, cx1, cy0, cy1 = rm, RW - rm, 1180, 1520
    series = [0.4 + 0.5 * (0.5 - 0.5 * math.cos(math.pi * i / 30)) +
              0.08 * math.sin(i / 2.5) for i in range(31)]
    lo, hi = min(series), max(series)
    pts = [(cx0 + (cx1 - cx0) * i / (len(series) - 1),
            cy1 - (cy1 - cy0) * (v - lo) / (hi - lo)) for i, v in enumerate(series)]
    area = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    ImageDraw.Draw(area).polygon(pts + [(cx1, cy1), (cx0, cy1)], fill=UP + (40,))
    img = Image.alpha_composite(img.convert("RGBA"), area).convert("RGB")
    d = ImageDraw.Draw(img)
    d.line(pts, fill=UP, width=6, joint="curve")
    d.text((rm, 1650), "@mansuki101", font=f("SemiBold", 34), fill=(210, 214, 224))
    swipe = "끝까지 보기  ↑"
    d.text((RW - rm - d.textlength(swipe, font=f("SemiBold", 34)), 1650),
           swipe, font=f("SemiBold", 34), fill=MUTE)
    img.save(OUT / "08-reel.jpg", quality=90)


cover_face()
cover_graphic()
cover_verm()
content(1, 5, "01", "연준, 기준금리를 동결했다",
        "제롬 파월 의장은 인플레이션이 아직 목표치 위에 있다며 금리 인하를 서두르지 않겠다고 밝혔다. 시장이 기대하던 연내 인하 시점이 뒤로 밀렸다.",
        "WSJ · 연합뉴스", "무슨 일")
content(2, 5, "02", "왜 시장이 놀랐나",
        "투자자들은 이미 두 차례 인하를 가격에 반영해 두고 있었다. 파월의 매파적 발언이 그 기대를 되돌리면서, 위험자산에서 돈이 빠르게 빠져나갔다.",
        "매일경제", "왜 / 배경")
indicators()
reel()
print("DONE ->", OUT)
