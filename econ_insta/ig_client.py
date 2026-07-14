"""Instagram 콘텐츠 발행 클라이언트 (Instagram Login / graph.instagram.com).

발행은 항상 2단계다:
    1) 미디어 컨테이너 생성 (Instagram이 image_url에서 이미지를 가져간다)
    2) 컨테이너를 publish

따라서 이미지는 반드시 공개 URL로 접근 가능해야 한다. 바이너리 직접 업로드는 지원되지 않는다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from .config import GRAPH_BASE, StoredToken, load_token

TIMEOUT = 60

CAPTION_MAX_CHARS = 2200
CAPTION_MAX_HASHTAGS = 30
CAROUSEL_MIN_ITEMS = 2
CAROUSEL_MAX_ITEMS = 10

# 컨테이너가 FINISHED 되기를 기다리는 기본 설정
POLL_INTERVAL_SECONDS = 3
POLL_TIMEOUT_SECONDS = 180

# 영상은 인스타 쪽 트랜스코딩이 필요해 이미지보다 오래 걸린다.
REEL_POLL_TIMEOUT_SECONDS = 600


class InstagramError(RuntimeError):
    """Graph API 호출 실패."""


class ContainerNotReady(InstagramError):
    """컨테이너가 제한 시간 안에 FINISHED에 도달하지 못함."""


@dataclass(frozen=True)
class PublishResult:
    media_id: str
    container_id: str
    permalink: str | None = None


def validate_caption(caption: str) -> None:
    if len(caption) > CAPTION_MAX_CHARS:
        raise InstagramError(f"캡션이 {len(caption)}자로 한도({CAPTION_MAX_CHARS}자)를 넘습니다.")
    hashtags = caption.count("#")
    if hashtags > CAPTION_MAX_HASHTAGS:
        raise InstagramError(f"해시태그가 {hashtags}개로 한도({CAPTION_MAX_HASHTAGS}개)를 넘습니다.")


class InstagramClient:
    def __init__(self, token: StoredToken | None = None, session: requests.Session | None = None):
        self._token = token or load_token()
        if self._token.is_expired:
            raise InstagramError("토큰이 만료되었습니다. `python -m econ_insta.ig_auth refresh` 를 실행하세요.")
        self._session = session or requests.Session()
        self._user_id: str | None = self._token.user_id or None

    # --- 저수준 --------------------------------------------------------

    def _call(self, method: str, path: str, **params) -> dict:
        params["access_token"] = self._token.access_token
        url = f"{GRAPH_BASE}/{path.lstrip('/')}"

        if method == "GET":
            response = self._session.get(url, params=params, timeout=TIMEOUT)
        else:
            response = self._session.post(url, data=params, timeout=TIMEOUT)

        try:
            payload = response.json()
        except ValueError:
            raise InstagramError(f"JSON이 아닌 응답 (HTTP {response.status_code}): {response.text[:300]}") from None

        if "error" in payload:
            error = payload["error"]
            raise InstagramError(
                f"[{error.get('code')}/{error.get('error_subcode')}] {error.get('message')} "
                f"(type={error.get('type')})"
            )
        if not response.ok:
            raise InstagramError(f"HTTP {response.status_code}: {payload}")
        return payload

    # --- 계정 ----------------------------------------------------------

    def me(self) -> dict:
        return self._call("GET", "me", fields="user_id,username,account_type")

    @property
    def user_id(self) -> str:
        if not self._user_id:
            self._user_id = str(self.me()["user_id"])
        return self._user_id

    # --- 컨테이너 -------------------------------------------------------

    def create_image_container(
        self,
        image_url: str,
        caption: str | None = None,
        alt_text: str | None = None,
        is_carousel_item: bool = False,
    ) -> str:
        """이미지 컨테이너를 만들고 컨테이너 ID를 반환한다. JPEG만 지원된다."""
        params: dict[str, str] = {"image_url": image_url}
        if caption is not None:
            validate_caption(caption)
            params["caption"] = caption
        if alt_text:
            params["alt_text"] = alt_text
        if is_carousel_item:
            params["is_carousel_item"] = "true"

        return str(self._call("POST", f"{self.user_id}/media", **params)["id"])

    def create_carousel_container(self, children: list[str], caption: str) -> str:
        if not CAROUSEL_MIN_ITEMS <= len(children) <= CAROUSEL_MAX_ITEMS:
            raise InstagramError(
                f"캐러셀은 {CAROUSEL_MIN_ITEMS}~{CAROUSEL_MAX_ITEMS}장이어야 합니다 (현재 {len(children)}장)."
            )
        validate_caption(caption)
        payload = self._call(
            "POST",
            f"{self.user_id}/media",
            media_type="CAROUSEL",
            children=",".join(children),
            caption=caption,
        )
        return str(payload["id"])

    def container_status(self, container_id: str) -> str:
        """EXPIRED | ERROR | FINISHED | IN_PROGRESS | PUBLISHED"""
        return self._call("GET", container_id, fields="status_code")["status_code"]

    def wait_for_container(
        self,
        container_id: str,
        timeout: float = POLL_TIMEOUT_SECONDS,
        interval: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        deadline = time.monotonic() + timeout
        while True:
            status = self.container_status(container_id)
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise InstagramError(f"컨테이너 {container_id} 처리 실패: status_code={status}")
            if time.monotonic() >= deadline:
                raise ContainerNotReady(
                    f"컨테이너 {container_id} 가 {timeout:.0f}초 안에 준비되지 않았습니다 (마지막 상태={status})."
                )
            time.sleep(interval)

    # --- 발행 ----------------------------------------------------------

    def publish_container(self, creation_id: str) -> str:
        return str(self._call("POST", f"{self.user_id}/media_publish", creation_id=creation_id)["id"])

    def permalink(self, media_id: str) -> str | None:
        try:
            return self._call("GET", media_id, fields="permalink").get("permalink")
        except InstagramError:
            return None

    def create_reel_container(
        self,
        video_url: str,
        caption: str,
        cover_url: str | None = None,
        share_to_feed: bool = True,
    ) -> str:
        """릴스 컨테이너를 만든다. media_type=REELS 이고 video_url을 받는다.

        cover_url을 주면 그 이미지가 썸네일이 된다(thumb_offset보다 우선한다).
        """
        validate_caption(caption)
        params: dict[str, str] = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true" if share_to_feed else "false",
        }
        if cover_url:
            params["cover_url"] = cover_url
        return str(self._call("POST", f"{self.user_id}/media", **params)["id"])

    def publish_reel(
        self,
        video_url: str,
        caption: str,
        cover_url: str | None = None,
        share_to_feed: bool = True,
        timeout: float = REEL_POLL_TIMEOUT_SECONDS,
    ) -> PublishResult:
        """릴스를 발행한다.

        영상은 인스타 쪽 트랜스코딩이 필요해 이미지보다 오래 걸린다 — 폴링 한도를
        따로 둔다(이미지 기준으로 두면 아직 IN_PROGRESS인데 포기한다).
        """
        container_id = self.create_reel_container(
            video_url, caption, cover_url=cover_url, share_to_feed=share_to_feed
        )
        self.wait_for_container(container_id, timeout=timeout)
        media_id = self.publish_container(container_id)
        return PublishResult(
            media_id=media_id,
            container_id=container_id,
            permalink=self.permalink(media_id),
        )

    def publish_images(
        self,
        image_urls: list[str],
        caption: str,
        alt_texts: list[str] | None = None,
        wait: bool = True,
    ) -> PublishResult:
        """이미지 1장이면 단일 게시물, 2장 이상이면 캐러셀로 발행한다."""
        if not image_urls:
            raise InstagramError("이미지가 최소 1장 필요합니다.")
        if alt_texts and len(alt_texts) != len(image_urls):
            raise InstagramError("alt_texts 개수가 image_urls 개수와 다릅니다.")

        def alt_for(index: int) -> str | None:
            return alt_texts[index] if alt_texts else None

        if len(image_urls) == 1:
            container_id = self.create_image_container(
                image_urls[0], caption=caption, alt_text=alt_for(0)
            )
        else:
            children = [
                self.create_image_container(url, alt_text=alt_for(i), is_carousel_item=True)
                for i, url in enumerate(image_urls)
            ]
            if wait:
                for child in children:
                    self.wait_for_container(child)
            container_id = self.create_carousel_container(children, caption)

        if wait:
            self.wait_for_container(container_id)

        media_id = self.publish_container(container_id)
        return PublishResult(
            media_id=media_id,
            container_id=container_id,
            permalink=self.permalink(media_id),
        )
