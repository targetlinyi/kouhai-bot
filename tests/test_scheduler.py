"""Tests for scheduler control behavior."""

import asyncio
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kouhai_bot.scheduler.engine import (
    DEFAULT_ENABLED_JOBS,
    _normalize_enabled_jobs,
    load_group_configs,
    scheduler_loop,
)


def test_scheduler_loop_stops_when_stop_event_is_set():
    seen: list[int] = []

    async def _fake_run(group_id: int, _now) -> None:
        seen.append(group_id)

    async def _run() -> None:
        stop_event = asyncio.Event()
        stop_event.set()
        with patch("kouhai_bot.scheduler.engine.get_config", return_value=SimpleNamespace(current_group=2468)), \
                patch("kouhai_bot.scheduler.engine._run_jobs_for_group", side_effect=_fake_run):
            await scheduler_loop(stop_event=stop_event, tick_seconds=0.01)

    asyncio.run(_run())
    assert seen == [2468]


def test_scheduler_loop_runs_every_configured_group():
    seen: list[int] = []

    async def _fake_run(group_id: int, _now) -> None:
        seen.append(group_id)

    async def _run() -> None:
        stop_event = asyncio.Event()
        stop_event.set()
        cfg = SimpleNamespace(
            current_group=2468,
            current_groups=[2468, 9753],
        )
        with patch("kouhai_bot.scheduler.engine.get_config", return_value=cfg), \
                patch("kouhai_bot.scheduler.engine._run_jobs_for_group", side_effect=_fake_run):
            await scheduler_loop(stop_event=stop_event, tick_seconds=0.01)

    asyncio.run(_run())
    assert seen == [2468, 9753]


def test_scheduler_defaults_to_contest_check_only():
    assert DEFAULT_ENABLED_JOBS == ["contest_check"]


def test_scheduler_preserves_existing_settings_and_defaults_new_group(tmp_path, monkeypatch):
    original = {
        "2468": {"group_id": 2468, "enabled_jobs": [], "min_rating": 2300,
                 "max_rating": 2700, "job_schedules": {"contest_check": "18:45"}},
    }
    path = tmp_path / "scheduler_config.json"
    path.write_text(json.dumps(original), encoding="utf-8")
    cfg = SimpleNamespace(data_dir=str(tmp_path), current_group=2468, current_groups=[2468, 9753])
    monkeypatch.setattr("kouhai_bot.scheduler.engine.get_config", lambda: cfg)

    loaded = load_group_configs()

    assert loaded[2468].enabled_jobs == []
    assert (loaded[2468].min_rating, loaded[2468].max_rating) == (2300, 2700)
    assert loaded[2468].job_schedules == {"contest_check": "18:45"}
    assert loaded[9753].enabled_jobs == DEFAULT_ENABLED_JOBS
    assert loaded[9753].min_rating is None and loaded[9753].max_rating is None
    assert loaded[9753].job_schedules == {}
    assert json.loads(path.read_text(encoding="utf-8"))["2468"] == original["2468"]
    loaded[9753].enabled_jobs.clear()
    assert load_group_configs()[9753].enabled_jobs == ["contest_check"]


def test_scheduler_initializes_defaults_for_all_groups(tmp_path, monkeypatch):
    cfg = SimpleNamespace(data_dir=str(tmp_path), current_group=2468, current_groups=[2468, 9753])
    monkeypatch.setattr("kouhai_bot.scheduler.engine.get_config", lambda: cfg)

    loaded = load_group_configs()
    saved = json.loads((tmp_path / "scheduler_config.json").read_text(encoding="utf-8"))

    assert set(loaded) == {2468, 9753}
    assert set(saved) == {"2468", "9753"}
    assert all(config.enabled_jobs == ["contest_check"] for config in loaded.values())


def test_scheduler_filters_removed_daily_jobs_from_old_configs():
    jobs = _normalize_enabled_jobs(["daily_achievements", "daily_post", "contest_check"])
    assert jobs == ["contest_check"]

    disabled = _normalize_enabled_jobs(["daily_post", "contest_check"], ["contest_check"])
    assert disabled == []
