from __future__ import annotations

import base64
import copy
import io
import json
from pathlib import Path

from agent.vision import (
    VisionImage,
    build_r_artifact_review,
    build_r_artifact_vision_message,
    build_user_message,
    hydrate_messages_for_llm,
    sniff_image_mime,
)
from agent.analysis.model_egress import ModelEgressGuard


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_sniff_image_mime_accepts_png_and_rejects_non_images():
    assert sniff_image_mime(PNG_1X1) == "image/png"
    assert sniff_image_mime(b"id,value\n1,2\n") == ""


def test_user_message_embeds_png_as_data_url(tmp_path):
    path = tmp_path / "paper.png"
    path.write_bytes(PNG_1X1)
    message = build_user_message(
        "请解读这张图",
        [VisionImage(path=path, name="paper.png", mime="image/png")],
    )

    assert message["role"] == "user"
    assert message["content"][0] == {"type": "text", "text": "请解读这张图"}
    assert message["content"][1]["type"] == "image_url"
    assert message["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_r_artifact_vision_attaches_session_png_and_skips_csv(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    from infrastructure.paths import data_path

    sid = "vision-session"
    root = data_path("outputs", "research", sid, "artifacts")
    root.mkdir(parents=True)
    figure = root / "km.png"
    table = root / "table1.csv"
    figure.write_bytes(PNG_1X1)
    table.write_text("arm,n\nA,10\n", encoding="utf-8")

    message = build_r_artifact_vision_message(
        {
            "artifact_previews": {
                "tables": [{"name": "counts", "preview_csv": "arm,n\nA,42\nB,58"}],
            },
            "result": {
                "ok": True,
                "figures": [
                    {
                        "name": "KM 曲线",
                        "path": str(figure),
                        "kind": "figure",
                        "mime_type": "image/png",
                    }
                ],
                "tables": [
                    {
                        "name": "Table 1",
                        "path": str(table),
                        "kind": "table",
                        "mime_type": "text/csv",
                    }
                ],
            }
        },
        sid,
        sharing_guard=ModelEgressGuard({"send_result_images": True}),
    )

    assert message is not None
    prompt = message["content"][0]["text"]
    assert message["content"][0]["type"] == "text"
    assert "请同时阅读本轮数值预览和附图" in prompt
    assert "arm,n\nA,42\nB,58" in prompt
    assert "不要说你看不到图" in prompt
    assert message["content"][1]["type"] == "image_url"
    assert len(message["content"]) == 2


def test_r_artifact_vision_prefers_png_preview_over_pdf_companion(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    from infrastructure.paths import data_path

    sid = "vision-session"
    root = data_path("outputs", "research", sid, "artifacts")
    root.mkdir(parents=True)
    png = root / "arm.png"
    pdf = root / "arm.pdf"
    orphan = root / "forest.pdf"
    png.write_bytes(PNG_1X1)
    pdf.write_bytes(b"%PDF-1.4\n%\n1 0 obj\n<<>>\nendobj\n%%EOF\n")
    orphan.write_bytes(b"%PDF-1.4\n%\n1 0 obj\n<<>>\nendobj\n%%EOF\n")

    message = build_r_artifact_vision_message(
        {
            "result": {
                "ok": True,
                "figures": [
                    {
                        "name": "arm_pdf",
                        "path": str(pdf),
                        "kind": "figure",
                        "mime_type": "application/pdf",
                        "metadata": {"format_variant": "pdf_vector"},
                    },
                    {
                        "name": "arm",
                        "path": str(png),
                        "kind": "figure",
                        "mime_type": "image/png",
                        "metadata": {"format_variant": "png_preview"},
                    },
                    {
                        "name": "forest",
                        "path": str(orphan),
                        "kind": "figure",
                        "mime_type": "application/pdf",
                    },
                ],
            }
        },
        sid,
        sharing_guard=ModelEgressGuard({"send_result_images": True}),
    )

    assert message is not None
    prompt = message["content"][0]["text"]
    assert message["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "arm_pdf" not in prompt
    assert "forest" in prompt


def test_chat_image_upload_and_turn_forwards_vision_images(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    from tests.core.test_research_api import _install_optional_import_stubs
    from api import create_app
    from api import chat as chat_module

    _install_optional_import_stubs(monkeypatch)
    seen = {}

    class FakeAgent:
        def run(self, *args, **kwargs):
            seen["vision"] = kwargs.get("vision_images")
            seen["message"] = args[0] if args else ""
            yield {"type": "text", "content": "ok"}

    monkeypatch.setattr(chat_module, "_build_agent", lambda *args, **kwargs: FakeAgent())
    client = create_app().test_client()
    sid = "vision-chat-session"

    uploaded = client.post(
        f"/api/session/{sid}/chat/images",
        data={"file": (io.BytesIO(PNG_1X1), "forest.png")},
        content_type="multipart/form-data",
    )
    assert uploaded.status_code == 200
    image_id = uploaded.get_json()["images"][0]["id"]

    rejected = client.post(
        f"/api/session/{sid}/chat/images",
        data={"file": (io.BytesIO(b"not-an-image"), "notes.txt")},
        content_type="multipart/form-data",
    )
    assert rejected.status_code == 400

    response = client.post(
        f"/api/session/{sid}/chat",
        json={"image_ids": [image_id]},
    )
    assert response.status_code == 200
    assert '"type": "text"' in response.get_data(as_text=True)
    assert seen["vision"]
    assert Path(seen["vision"][0].path).name.endswith(".png")
    assert "请解读这些图片" in seen["message"]


def test_artifact_review_history_keeps_refs_and_rehydrates_png(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    from infrastructure.paths import data_path

    sid = "vision-session"
    figure = data_path("outputs", "research", sid, "artifacts", "km.png")
    figure.parent.mkdir(parents=True)
    figure.write_bytes(PNG_1X1)
    review = build_r_artifact_review(
        {
            "artifact_previews": {
                "tables": [{"name": "counts", "preview_csv": "arm,n\nA,42\nB,58"}],
            },
            "result": {
                "ok": True,
                "figures": [
                    {
                        "name": "KM 曲线",
                        "path": str(figure),
                        "kind": "figure",
                        "mime_type": "image/png",
                    }
                ],
            },
        },
        sid,
        sharing_guard=ModelEgressGuard({"send_result_images": True}),
    )

    assert review is not None
    history = review.history_message
    assert history["hidden"] is True
    assert history["kind"] == "artifact_review"
    history_text = json.dumps(history, ensure_ascii=False)
    assert "data:image" not in history_text
    assert "relative_path" in history_text
    assert "A,42" in history_text

    original = copy.deepcopy(history)
    hydrated = hydrate_messages_for_llm([history], sid)
    assert original == history
    content = hydrated[0]["content"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "A,42" in content[0]["text"]


def test_hidden_artifact_review_is_not_shown_in_saved_session_history():
    from api.saved_sessions import _visible_history, _visible_msg_count

    history = [
        {"role": "user", "content": "画一张入组图"},
        {
            "role": "user",
            "hidden": True,
            "kind": "artifact_review",
            "content": "请同时阅读本轮数值预览和附图",
        },
        {"role": "assistant", "content": "B 组人数更高。"},
    ]
    visible = _visible_history(history)
    assert [item["content"] for item in visible] == ["画一张入组图", "B 组人数更高。"]
    assert _visible_msg_count(history) == 2
