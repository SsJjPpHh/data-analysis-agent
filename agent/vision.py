"""DeepSeek Flash vision: user-attached images and R figure review.

After run_r_code, one user-role follow-up carries numeric artifact_previews
and PNG plots together. Tool results stay text. Stored history keeps
image_ref ids or session-relative paths, not base64.
"""

from __future__ import annotations

import base64
import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data.session import validate_session_id
from infrastructure.paths import data_path
from agent.analysis.model_egress import ModelEgressGuard
from agent.analysis.model_projection import ModelResultProjector


VISION_MIME_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGES_PER_TURN = 6
IMAGE_TOKEN_ESTIMATE = 1800
_PREVIEW_EXCERPT_CHARS = 4000
_DEFAULT_IMAGE_PROMPT = "请解读这些图片，并说明它们与当前分析的关系。"


@dataclass(frozen=True)
class VisionImage:
    path: Path
    name: str = ""
    mime: str = ""
    image_id: str = ""


@dataclass(frozen=True)
class ArtifactReview:
    llm_message: dict[str, Any]
    history_message: dict[str, Any]


def sniff_image_mime(data: bytes) -> str:
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 6 and data[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def default_image_prompt() -> str:
    return _DEFAULT_IMAGE_PROMPT


def _session_output_root(session_id: str) -> Path | None:
    try:
        sid = validate_session_id(session_id)
    except ValueError:
        return None
    if not sid:
        return None
    return data_path("outputs", "research", sid).resolve()


def is_session_readable_image(path: Path, session_id: str) -> bool:
    root = _session_output_root(session_id)
    if root is None:
        return False
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return False
    return resolved.is_file()


def encode_image_part(image: VisionImage) -> dict[str, Any] | None:
    path = Path(image.path)
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not data or len(data) > MAX_IMAGE_BYTES:
        return None
    mime = image.mime or sniff_image_mime(data)
    if mime not in VISION_MIME_TYPES:
        return None
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{encoded}"},
        "name": image.name or path.name,
    }


def session_relative_image_path(path: Path, session_id: str) -> str:
    root = _session_output_root(session_id)
    if root is None:
        return ""
    try:
        resolved = path.resolve(strict=True)
        relative = resolved.relative_to(root)
    except (OSError, ValueError):
        return ""
    return relative.as_posix()


def image_ref_part(image: VisionImage, session_id: str = "") -> dict[str, Any]:
    part: dict[str, Any] = {
        "type": "image_ref",
        "name": image.name or Path(image.path).name,
        "mime": image.mime,
    }
    if image.image_id:
        part["image_id"] = image.image_id
    relative = session_relative_image_path(image.path, session_id) if session_id else ""
    if relative:
        part["relative_path"] = relative
    return part


def build_user_message(text: str, images: list[VisionImage] | None = None) -> dict[str, Any]:
    prompt = str(text or "").strip() or (default_image_prompt() if images else "")
    selected = list(images or [])[:MAX_IMAGES_PER_TURN]
    if not selected:
        return {"role": "user", "content": prompt}
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    attached = 0
    skipped: list[str] = []
    for image in selected:
        part = encode_image_part(image)
        if part is None:
            skipped.append(image.name or str(image.path))
            continue
        content.append(part)
        attached += 1
    if skipped:
        content[0]["text"] += "\n\n未能附带的图片：" + "、".join(skipped[:MAX_IMAGES_PER_TURN])
    if attached == 0:
        return {"role": "user", "content": content[0]["text"]}
    return {"role": "user", "content": content}


def history_user_content(
    text: str,
    images: list[VisionImage] | None = None,
    session_id: str = "",
) -> str | list[dict[str, Any]]:
    prompt = str(text or "").strip() or (default_image_prompt() if images else "")
    selected = list(images or [])[:MAX_IMAGES_PER_TURN]
    refs = [
        image_ref_part(image, session_id=session_id)
        for image in selected
        if image.image_id or (session_id and session_relative_image_path(image.path, session_id))
    ]
    if not refs:
        return prompt
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    content.extend(refs)
    return content


_COMPANION_SUFFIXES = ("_pdf", "_tiff", "_png", "_jpeg", "_jpg")


def _figure_family(name: str) -> str:
    stem = Path(str(name or "")).stem
    lowered = stem.lower()
    for suffix in _COMPANION_SUFFIXES:
        if lowered.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def _companion_skip(skipped_label: str, attached_families: set[str]) -> bool:
    name = str(skipped_label or "").split("（", 1)[0].strip()
    family = _figure_family(name)
    return bool(family) and family in attached_families


def _figure_vision_rank(item: dict[str, Any]) -> tuple[int, str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    variant = str(metadata.get("format_variant") or "")
    mime = str(item.get("mime_type") or "")
    suffix = Path(str(item.get("path") or "")).suffix.lower()
    name = str(item.get("name") or Path(str(item.get("path") or "")).name or "")
    if variant == "png_preview" or mime == "image/png" or suffix == ".png":
        return (0, name)
    if mime in VISION_MIME_TYPES or suffix in {".jpg", ".jpeg", ".gif", ".webp"}:
        return (1, name)
    return (2, name)


def _artifact_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    records: list[dict[str, Any]] = []
    for group in ("figures", "artifacts", "tables"):
        items = result.get(group)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                records.append(item)
    return records


def vision_images_from_r_payload(payload: dict[str, Any], session_id: str) -> tuple[list[VisionImage], list[str]]:
    images: list[VisionImage] = []
    skipped: list[str] = []
    seen: set[str] = set()
    records = sorted(_artifact_records(payload), key=_figure_vision_rank)
    for item in records:
        kind = str(item.get("kind") or item.get("type") or "")
        mime = str(item.get("mime_type") or "")
        name = str(item.get("name") or Path(str(item.get("path") or "")).name or "artifact")
        raw_path = str(item.get("path") or "")
        if not raw_path:
            continue
        path = Path(raw_path)
        suffix = path.suffix.lower()
        looks_like_image = mime in VISION_MIME_TYPES or suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
        if kind in {"table", "text", "model"} and not looks_like_image:
            continue
        if kind not in {"figure", "plot", "image"} and not looks_like_image:
            continue
        if mime and mime not in VISION_MIME_TYPES and suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            skipped.append(f"{name}（格式不受支持）")
            continue
        if not is_session_readable_image(path, session_id):
            skipped.append(f"{name}（不在本会话产物目录）")
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        sniffed = sniff_image_mime(data)
        if sniffed not in VISION_MIME_TYPES:
            skipped.append(name)
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        if len(images) >= MAX_IMAGES_PER_TURN:
            skipped.append(name)
            continue
        images.append(VisionImage(path=path, name=name, mime=sniffed))
    return images, skipped


def _figure_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    figures = result.get("figures")
    if not isinstance(figures, list):
        return []
    return [item for item in figures if isinstance(item, dict)]


def preview_excerpt_from_payload(payload: dict[str, Any], max_chars: int = _PREVIEW_EXCERPT_CHARS) -> str:
    previews = payload.get("artifact_previews")
    if not isinstance(previews, dict):
        return ""
    chunks: list[str] = []
    for group, body_key, label in (
        ("tables", "preview_csv", "表"),
        ("texts", "preview_text", "文本"),
        ("models", "preview_text", "模型"),
    ):
        items = previews.get(group)
        if not isinstance(items, list):
            continue
        for item in items[:3]:
            if not isinstance(item, dict):
                continue
            body = str(item.get(body_key) or "").strip()
            if not body:
                continue
            name = str(item.get("name") or group)
            chunks.append(f"【{label} {name}】\n{body}")
    text = "\n\n".join(chunks).strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n…"
    return text


def build_r_artifact_review(
    payload: dict[str, Any], session_id: str, *, sharing_guard: ModelEgressGuard | None = None,
) -> ArtifactReview | None:
    guard = sharing_guard or ModelEgressGuard()
    if guard.policy.send_result_images:
        images, skipped = vision_images_from_r_payload(payload, session_id)
    else:
        images, skipped = [], []
    figures = _figure_records(payload)
    # The text beside an image uses the same view as the tool message. Never
    # bypass the selected policy by re-reading unfiltered artifact previews.
    excerpt = preview_excerpt_from_payload(ModelResultProjector().project(guard.project(payload)))
    if not images and not figures:
        return None
    attached_families = {_figure_family(image.name) for image in images}
    skipped = [item for item in skipped if not _companion_skip(item, attached_families)]
    lines: list[str]
    if images:
        lines = [
            "请同时阅读本轮数值预览和附图，再写结果解释。",
            "精确数字、表格和模型摘要以本条中的预览为准，不要用目测柱高或轴刻度代替这些数字。",
            "附图用于读标签、分组、分布形态、是否被裁切，以及图与表是否一致；二者冲突时以表格数字为准，并写明图上看到的差异。",
            "不要说你看不到图。",
        ]
    else:
        lines = [
            "本轮 R 产物含有图，但未发送：当前外发设置关闭，或图片格式/路径不可读取。",
            "请根据下面的数值预览解释，不要声称你看到了图。",
        ]
    if excerpt:
        lines.extend(["", excerpt])
    if skipped:
        lines.append("未以图像形式附带：" + "、".join(skipped[:12]))
    prompt = "\n".join(lines).strip()
    llm_message = build_user_message(prompt, images)
    history_content = history_user_content(prompt, images, session_id=session_id)
    return ArtifactReview(
        llm_message=llm_message,
        history_message={
            "role": "user",
            "hidden": True,
            "kind": "artifact_review",
            "content": history_content,
        },
    )


def build_r_artifact_vision_message(
    payload: dict[str, Any], session_id: str, *, sharing_guard: ModelEgressGuard | None = None,
) -> dict[str, Any] | None:
    review = build_r_artifact_review(payload, session_id, sharing_guard=sharing_guard)
    return None if review is None else review.llm_message


def visible_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        if block_type == "text":
            text = str(block.get("text") or "").strip()
            if text:
                parts.append(text)
        elif block_type in {"image_ref", "image_url", "image"}:
            name = str(block.get("name") or "图片").strip() or "图片"
            parts.append(f"[图片: {name}]")
    return "\n".join(parts).strip()


def content_token_estimate(content: Any, chars_per_token: float = 3.5) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return max(1, int(len(content) / chars_per_token)) if content else 0
    if not isinstance(content, list):
        return max(1, int(len(str(content)) / chars_per_token))
    text_chars = 0
    images = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        if block_type == "text":
            text_chars += len(str(block.get("text") or ""))
        elif block_type in {"image_url", "image_ref", "image"}:
            images += 1
    return max(1, int(text_chars / chars_per_token) + images * IMAGE_TOKEN_ESTIMATE)


def strip_images_for_compaction(content: Any) -> Any:
    if not isinstance(content, list):
        return content
    cleaned: list[Any] = []
    for block in content:
        if not isinstance(block, dict):
            cleaned.append(block)
            continue
        block_type = str(block.get("type") or "")
        if block_type in {"image", "image_url", "image_ref"}:
            name = str(block.get("name") or "图片")
            cleaned.append({"type": "text", "text": f"[image removed: {name}]"})
        else:
            cleaned.append(block)
    return cleaned


def resolve_image_ref(block: dict[str, Any], session_id: str) -> VisionImage | None:
    image_id = str(block.get("image_id") or "").strip()
    if image_id:
        from data.conversation_images import load_conversation_image

        stored = load_conversation_image(session_id, image_id)
        if stored is not None:
            return stored.as_vision()
    relative = str(block.get("relative_path") or "").strip()
    root = _session_output_root(session_id)
    if not relative or root is None:
        return None
    path = (root / relative).resolve()
    if not is_session_readable_image(path, session_id):
        return None
    return VisionImage(
        path=path,
        name=str(block.get("name") or path.name),
        mime=str(block.get("mime") or ""),
        image_id=image_id,
    )


def sanitize_content_for_history(content: Any) -> Any:
    if not isinstance(content, list):
        return content
    cleaned: list[Any] = []
    for block in content:
        if not isinstance(block, dict):
            cleaned.append(block)
            continue
        block_type = str(block.get("type") or "")
        if block_type == "image_url":
            name = str(block.get("name") or "图片")
            if block.get("image_id") or block.get("relative_path"):
                ref = {"type": "image_ref", "name": name, "mime": str(block.get("mime") or "")}
                if block.get("image_id"):
                    ref["image_id"] = block["image_id"]
                if block.get("relative_path"):
                    ref["relative_path"] = block["relative_path"]
                cleaned.append(ref)
            else:
                cleaned.append({"type": "text", "text": f"[图片: {name}]"})
            continue
        cleaned.append(block)
    return cleaned


def sanitize_message_for_history(message: dict[str, Any]) -> dict[str, Any]:
    entry = {
        "role": message.get("role") or "user",
        "content": sanitize_content_for_history(message.get("content")),
    }
    if message.get("hidden"):
        entry["hidden"] = True
    kind = str(message.get("kind") or "")
    if kind:
        entry["kind"] = kind
    return entry


def _image_slots(messages: list[dict[str, Any]]) -> list[tuple[int, int, dict[str, Any]]]:
    slots: list[tuple[int, int, dict[str, Any]]] = []
    for message_index, message in enumerate(messages):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block_index, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "") in {"image_ref", "image_url", "image"}:
                slots.append((message_index, block_index, block))
    return slots


def hydrate_messages_for_llm(
    messages: list[dict[str, Any]],
    session_id: str,
    image_budget: int = MAX_IMAGES_PER_TURN,
) -> list[dict[str, Any]]:
    cloned = copy.deepcopy(list(messages or []))
    budget = max(0, int(image_budget))
    slots = _image_slots(cloned)
    keep: set[tuple[int, int]] = set()
    used = 0
    for message_index, block_index, block in reversed(slots):
        if used >= budget:
            break
        block_type = str(block.get("type") or "")
        if block_type == "image_url":
            keep.add((message_index, block_index))
            used += 1
            continue
        image = resolve_image_ref(block, session_id)
        if image is None:
            continue
        part = encode_image_part(image)
        if part is None:
            continue
        cloned[message_index]["content"][block_index] = part
        keep.add((message_index, block_index))
        used += 1
    for message_index, block_index, block in slots:
        if (message_index, block_index) in keep:
            continue
        if str(block.get("type") or "") != "image_ref":
            continue
        name = str(block.get("name") or "图片")
        cloned[message_index]["content"][block_index] = {
            "type": "text",
            "text": f"[图片: {name}]",
        }
    return cloned
