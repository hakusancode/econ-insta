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
