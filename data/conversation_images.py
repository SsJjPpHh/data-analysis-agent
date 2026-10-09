"""Session-scoped conversation images for DeepSeek Flash vision."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from agent.vision import (
    MAX_IMAGE_BYTES,
    MAX_IMAGES_PER_TURN,
    VISION_MIME_TYPES,
    VisionImage,
    sniff_image_mime,
)

__all__ = [
    "MAX_IMAGE_BYTES",
    "MAX_IMAGES_PER_TURN",
    "StoredConversationImage",
    "load_conversation_image",
    "load_conversation_images",
    "save_conversation_image",
]
from data.session import validate_session_id
from infrastructure.paths import data_path
from infrastructure.secure_files import write_private_bytes, write_private_text


_IMAGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@dataclass(frozen=True)
class StoredConversationImage:
    image_id: str
    path: Path
    mime: str
    name: str
    size: int

    def as_public_dict(self) -> dict[str, str | int]:
        return {
            "id": self.image_id,
            "name": self.name,
            "mime": self.mime,
            "size": self.size,
        }

    def as_vision(self) -> VisionImage:
        return VisionImage(
            path=self.path,
            name=self.name,
            mime=self.mime,
            image_id=self.image_id,
        )


def conversation_images_root(session_id: str) -> Path:
    sid = validate_session_id(session_id)
    if not sid:
        raise ValueError("Invalid session_id")
    root = data_path("outputs", "research", sid, "conversation_images")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _meta_path(root: Path, image_id: str) -> Path:
    return root / f"{image_id}.json"


def save_conversation_image(session_id: str, data: bytes, filename: str) -> StoredConversationImage:
    if not data:
        raise ValueError("图片内容为空")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("单张图片不能超过 8MB")
    mime = sniff_image_mime(data)
    if mime not in VISION_MIME_TYPES:
        raise ValueError("只支持 JPEG、PNG、GIF 或 WebP 图片")
    root = conversation_images_root(session_id)
    image_id = uuid.uuid4().hex
    suffix = VISION_MIME_TYPES[mime]
    path = root / f"{image_id}{suffix}"
    write_private_bytes(path, data)
    name = Path(str(filename or f"image{suffix}")).name[:180] or f"image{suffix}"
    record = {
        "id": image_id,
        "name": name,
        "mime": mime,
        "size": len(data),
        "file": path.name,
    }
    write_private_text(_meta_path(root, image_id), json.dumps(record, ensure_ascii=False))
    return StoredConversationImage(
        image_id=image_id,
        path=path,
        mime=mime,
        name=name,
        size=len(data),
    )


def load_conversation_image(session_id: str, image_id: str) -> StoredConversationImage | None:
    if not _IMAGE_ID_RE.fullmatch(str(image_id or "")):
        return None
    try:
        root = conversation_images_root(session_id)
    except ValueError:
        return None
    meta_file = _meta_path(root, image_id)
    if not meta_file.is_file():
        return None
    try:
        record = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    filename = str((record or {}).get("file") or "")
    path = (root / filename).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    mime = str((record or {}).get("mime") or "")
    if mime not in VISION_MIME_TYPES:
        return None
    return StoredConversationImage(
        image_id=image_id,
        path=path,
        mime=mime,
        name=str((record or {}).get("name") or path.name),
        size=int((record or {}).get("size") or path.stat().st_size),
    )


def load_conversation_images(session_id: str, image_ids: list[str]) -> list[VisionImage]:
    images: list[VisionImage] = []
    for image_id in image_ids[:MAX_IMAGES_PER_TURN]:
        stored = load_conversation_image(session_id, image_id)
        if stored is not None:
            images.append(stored.as_vision())
    return images
