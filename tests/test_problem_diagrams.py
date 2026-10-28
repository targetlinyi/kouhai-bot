"""Original statement diagrams in QQ problem cards; all downloads are mocked."""

import asyncio
from unittest.mock import AsyncMock

from kouhai_bot.handlers import shared
from kouhai_bot.problem_content import statement_diagrams


def _image_files(messages):
    return [
        segment["data"]["file"]
        for message in messages for segment in message
        if segment.get("type") == "image"
    ]


def test_statement_diagrams_filters_formula_and_preserves_source_order():
    first = {"src": "https://example.com/first.png", "kind": "graphic"}
    legacy = {"src": "https://example.com/legacy.png", "class": "small tex-graphics"}
    second = {"src": "https://example.com/second.png", "kind": "graphic"}
    statement = {"images": [
        first,
        {"src": "https://example.com/formula.png", "kind": "formula"},
        legacy,
        {"src": "https://example.com/formula-legacy.png", "class": "tex-formula"},
        {"src": "https://example.com/conflict.png", "kind": "formula", "class": "tex-graphics"},
        {"src": "https://example.com/unknown.png"},
        {"kind": "graphic"},
        None,
        second,
    ]}
    assert statement_diagrams(statement) == [first, legacy, second]
    assert statement_diagrams({}) == []
    assert statement_diagrams(None) == []


def test_problem_diagrams_download_only_graphics_and_send_base64(monkeypatch):
    load = lambda pid, **kwargs: {"images": [
        {"src": "https://example.com/first.png", "kind": "graphic"},
        {"src": "https://example.com/formula.png", "kind": "formula"},
        {"src": "https://example.com/second.png", "kind": "graphic"},
    ]}
    monkeypatch.setattr(shared, "load_statement_json", load)
    download = AsyncMock(side_effect=[
        "data:image/png;base64,Zmlyc3Q=", "data:image/jpeg;base64,c2Vjb25k",
    ])
    monkeypatch.setattr(shared, "_download_image_data_url_async", download)

    messages = asyncio.run(shared.build_problem_diagram_messages("100A"))

    assert _image_files(messages) == ["base64://Zmlyc3Q=", "base64://c2Vjb25k"]
    assert [call.args[0] for call in download.await_args_list] == [
        "https://example.com/first.png", "https://example.com/second.png",
    ]
    assert "https://" not in str(messages)


def test_problem_diagram_failure_keeps_other_images_and_retry_notice(monkeypatch):
    monkeypatch.setattr(shared, "load_statement_json", lambda *args, **kwargs: {"images": [
        {"src": "https://example.com/broken.png", "kind": "graphic"},
        {"src": "https://example.com/ok.png", "kind": "graphic"},
    ]})
    monkeypatch.setattr(shared, "_download_image_data_url_async", AsyncMock(side_effect=[
        TimeoutError("offline"), "data:image/png;base64,b2s=",
    ]))

    messages = asyncio.run(shared.build_problem_diagram_messages("100A"))

    assert _image_files(messages) == ["base64://b2s="]
    notices = [
        segment["data"]["text"]
        for message in messages for segment in message
        if segment.get("type") == "text"
    ]
    assert any("/pb" in text or "/problem" in text for text in notices), messages
    assert "https://" not in str(messages)


def test_no_diagrams_skips_download_and_legacy_snake_setting(monkeypatch):
    monkeypatch.setattr(shared, "load_statement_json", lambda *args, **kwargs: {"images": [
        {"src": "https://example.com/formula.png", "kind": "formula"},
    ]})
    download = AsyncMock(side_effect=AssertionError("no diagram to download"))
    monkeypatch.setattr(shared, "_download_image_data_url_async", download)
    assert asyncio.run(shared.build_problem_diagram_messages("100A")) == []
    download.assert_not_awaited()

    cleaned, changed = shared.sanitize_cached_problem_card_payload({
        "pid": "100A", "post_msg": "题目", "sample_messages": [],
        "snake_enabled": True, "snake_msg_id": 123,
    })
    assert changed
    assert "snake_enabled" not in cleaned
    assert "snake_msg_id" not in cleaned
