"""Core dispatch coverage for multiple configured QQ service groups."""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kouhai_bot.handlers import process_event


def _group_command_event(group_id: int, message_id: str) -> dict:
    return {
        "type": "message",
        "message_type": "group",
        "group_id": group_id,
        "user_id": 456,
        "sender": {"nickname": "Tester", "card": "", "user_id": 456},
        "message_id": message_id,
        "raw_message": "/status",
        "message": [{"type": "text", "data": {"text": "/status"}}],
        "raw": {},
    }


def test_group_commands_run_in_each_configured_group_and_ignore_others(monkeypatch):
    handled_group_ids: list[int] = []
    cfg = SimpleNamespace(
        bot_qq=1,
        current_group=111111,
        current_groups=[111111, 222222],
    )

    async def fake_handler(**kwargs):
        handled_group_ids.append(kwargs["group_id"])

    monkeypatch.setattr("kouhai_bot.handlers.get_config", lambda: cfg)
    monkeypatch.setattr("kouhai_bot.echo.check_and_echo", lambda **_kwargs: _false())
    monkeypatch.setattr(
        "kouhai_bot.handlers.registry.get",
        lambda name: SimpleNamespace(name="status", handler=fake_handler)
        if name == "status"
        else None,
    )

    async def run():
        await process_event(
            _group_command_event(111111, "group-1"),
            spawn_handlers=False,
        )
        await process_event(
            _group_command_event(222222, "group-2"),
            spawn_handlers=False,
        )
        await process_event(
            _group_command_event(333333, "outside"),
            spawn_handlers=False,
        )

    asyncio.run(run())

    assert handled_group_ids == [111111, 222222]


async def _false() -> bool:
    return False


@pytest.mark.parametrize(
    ("memberships", "expected_group", "expected_lookups"),
    [({111111, 222222}, 111111, [111111]), ({222222}, 222222, [111111, 222222]),
     (set(), None, [111111, 222222])],
)
@pytest.mark.parametrize("spawn_handlers", [False, True])
def test_private_commands_choose_one_member_group_or_reject(
    monkeypatch, memberships, expected_group, expected_lookups, spawn_handlers,
):
    cfg = SimpleNamespace(bot_qq=1, current_group=111111, current_groups=[111111, 222222])
    handler = AsyncMock()
    send = AsyncMock(return_value=1)
    lookups = []

    async def is_member(group_id, user_id):
        assert user_id == 456
        lookups.append(group_id)
        return group_id in memberships

    monkeypatch.setattr("kouhai_bot.handlers.get_config", lambda: cfg)
    monkeypatch.setattr("kouhai_bot.handlers.is_service_group_member", is_member)
    monkeypatch.setattr("kouhai_bot.handlers.send_private_msg", send)
    monkeypatch.setattr("kouhai_bot.handlers.registry.get", lambda _name: SimpleNamespace(name="status", handler=handler))
    event = _group_command_event(0, "private-multigroup")
    event["message_type"] = "private"

    async def run():
        task = await process_event(event, spawn_handlers=spawn_handlers)
        if task is not None:
            await task

    asyncio.run(run())
    assert lookups == expected_lookups
    if expected_group is None:
        handler.assert_not_awaited()
        send.assert_awaited_once()
        assert send.await_args.args[0] == 456
        assert "服务群成员" in send.await_args.args[1][0]["data"]["text"]
    else:
        handler.assert_awaited_once()
        assert handler.await_args.kwargs["group_id"] == expected_group
        send.assert_not_awaited()


def test_current_problem_and_scoreboard_updates_stay_in_their_group(tmp_path, monkeypatch):
    from kouhai_bot.handlers import shared
    from kouhai_bot.handlers.cmd import newproblem, submit

    cfg = SimpleNamespace(data_dir=str(tmp_path), current_group=111111, current_groups=[111111, 222222])
    monkeypatch.setattr(shared, "get_config", lambda: cfg)
    monkeypatch.setattr(newproblem, "get_config", lambda: cfg)
    monkeypatch.setattr(submit, "_top5_entries", lambda *_args: [])
    monkeypatch.setattr(newproblem, "settle_dynamic_submit_wait_for_problem", lambda *_args: False)

    async def update(_group_id, write):
        write()

    monkeypatch.setattr(newproblem, "run_group_state_update", update)

    async def run():
        await newproblem._commit_problem_state(111111, {"today": "542D", "rating": 2600})
        await newproblem._commit_problem_state(222222, {"today": "100A", "rating": 2300})

    asyncio.run(run())
    first = submit._update_scoreboard_for_pid(111111, 456, "First solver", "542D", {"rating": 2600})
    assert first[0] is True and first[1] == 1
    assert shared.load_scoreboard(222222)["solves"] == []
    second = submit._update_scoreboard_for_pid(222222, 456, "Second solver", "100A", {"rating": 2300})
    assert second[0] is True and second[1] == 1
    assert [item["problem"] for item in shared.load_scoreboard(111111)["solves"]] == ["542D"]
    assert [item["problem"] for item in shared.load_scoreboard(222222)["solves"]] == ["100A"]
    asyncio.run(newproblem._commit_problem_state(111111, {"today": "200B"}))
    assert shared.get_today_problem(111111)["today"] == "200B"
    assert shared.get_today_problem(222222)["today"] == "100A"
    assert [item["problem"] for item in shared.load_scoreboard(222222)["solves"]] == ["100A"]


def test_interleaved_group_redirects_keep_each_submission_problem(tmp_path, monkeypatch):
    from kouhai_bot import private_judge
    from kouhai_bot.handlers import shared
    from kouhai_bot.handlers.cmd import submit

    cfg = SimpleNamespace(data_dir=str(tmp_path))
    problems = {111111: {"today": "542D", "rating": 2600},
                222222: {"today": "100A", "rating": 2300}}
    requests = []
    first_send_started = asyncio.Event()
    resume_first_send = asyncio.Event()
    private_messages = []

    async def send_private(user_id, message):
        assert user_id == 456
        private_messages.append(message)
        if "solution-A" in message[0]["data"]["text"]:
            first_send_started.set()
            await resume_first_send.wait()
        return len(private_messages)

    def record_request(self, request):
        requests.append(request)
        request.done_event.set()

    monkeypatch.setattr(private_judge, "get_config", lambda: cfg)
    monkeypatch.setattr(shared, "get_config", lambda: cfg)
    monkeypatch.setattr(submit, "get_today_problem", lambda group_id: problems[group_id])
    monkeypatch.setattr(submit, "load_private_problem_history", lambda *_args: [])
    monkeypatch.setattr(submit, "group_problem_history", lambda *_args: [])
    monkeypatch.setattr(submit, "has_group_problem_private_notified", lambda *_args: True)
    monkeypatch.setattr(submit, "mark_group_problem_private_notified", lambda *_args: True)
    monkeypatch.setattr(submit, "send_private_msg", send_private)
    group_send = AsyncMock()
    delete = AsyncMock()
    monkeypatch.setattr(submit, "send_group_msg", group_send)
    monkeypatch.setattr(submit, "delete_msg", delete)
    monkeypatch.setattr(submit.GroupCoordinator, "_enqueue_locked", record_request)

    async def run():
        first = asyncio.create_task(submit._redirect_blocked_group_submit_to_private(
            111111, 456, {"nickname": "Tester"}, "message-A", "solution-A",
        ))
        await asyncio.wait_for(first_send_started.wait(), timeout=1)
        assert await submit._redirect_blocked_group_submit_to_private(
            222222, 456, {"nickname": "Tester"}, "message-B", "solution-B",
        )
        assert private_judge.get_private_current_problem(456)["today"] == "100A"
        resume_first_send.set()
        assert await asyncio.wait_for(first, timeout=1)

    asyncio.run(run())
    assert [(req.group_id, req.submit_pid, req.payload, req.message_id) for req in requests] == [
        (222222, "100A", "solution-B", "message-B"),
        (111111, "542D", "solution-A", "message-A"),
    ]
    assert all(req.scope == submit.PRIVATE_SCOPE and req.target_pid == req.submit_pid for req in requests)
    assert all(not req.submit_score_candidate for req in requests)
    assert len(private_messages) == 2 and delete.await_count == 2
    group_send.assert_not_awaited()
    problems[111111]["today"] = "200B"
    assert requests[1].submit_problem["today"] == "542D"


def test_private_clear_blocks_late_history_from_other_service_group(tmp_path, monkeypatch):
    from kouhai_bot import private_judge
    from kouhai_bot.handlers.cmd import submit

    monkeypatch.setattr(private_judge, "get_config", lambda: SimpleNamespace(data_dir=str(tmp_path)))
    monkeypatch.setattr(submit, "_react_req", AsyncMock())

    def request(group_id, seq, kind="submit", pid="542D"):
        return submit.PendingRequest(
            kind=kind, group_id=group_id, user_id=456, sender={},
            message_id=f"message-{seq}", command=kind, nickname="Tester",
            scope=submit.PRIVATE_SCOPE, target_pid=pid, submit_pid=pid, seq=seq,
        )

    async def run():
        coord = submit._get_coordinator(222222, scope=submit.PRIVATE_SCOPE, user_id=456)
        assert submit._get_coordinator(111111, scope=submit.PRIVATE_SCOPE, user_id=456) is coord
        old = request(222222, 1)
        assert await coord._save_context_record(old, {"problem": "542D", "result": "pending"})
        await coord._finalize_clear(request(111111, 2, kind="clear"))
        assert private_judge.load_private_problem_history(456, "542D") == []
        assert not await coord._save_context_record(old, {"problem": "542D", "result": "correct"})
        assert private_judge.load_private_problem_history(456, "542D") == []
        assert await coord._save_context_record(request(222222, 3), {"problem": "542D", "result": "pending"})
        assert len(private_judge.load_private_problem_history(456, "542D")) == 1
        assert await coord._save_context_record(
            request(222222, 1, pid="100A"), {"problem": "100A", "result": "pending"},
        )
        assert len(private_judge.load_private_problem_history(456, "100A")) == 1

    asyncio.run(run())
