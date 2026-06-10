"""Tests for tree_walker.tui.app — history persistence and advanced features."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tree_walker.tui.app import HISTORY_FILE, TW_LOGO, TreeWalkerApp


class TestLogo:
	def test_logo_not_empty(self):
		assert TW_LOGO.strip()
		assert "_____" in TW_LOGO or "____" in TW_LOGO


class TestLoadHistory:
	def test_load_from_existing_file(self, tmp_path: Path):
		history_file = tmp_path / "history.json"
		history_file.write_text(
			json.dumps(["task1", "task2"], ensure_ascii=False), encoding="utf-8",
		)
		app = _make_app()
		with patch("tree_walker.tui.app.HISTORY_FILE", history_file):
			app._load_history()
		assert app._task_history == ["task1", "task2"]
		assert app._history_index == 2

	def test_load_missing_file(self, tmp_path: Path):
		history_file = tmp_path / "nonexistent.json"
		app = _make_app()
		with patch("tree_walker.tui.app.HISTORY_FILE", history_file):
			app._load_history()
		assert app._task_history == []
		assert app._history_index == 0

	def test_load_corrupt_json(self, tmp_path: Path):
		history_file = tmp_path / "history.json"
		history_file.write_text("not valid json{{{", encoding="utf-8")
		app = _make_app()
		with patch("tree_walker.tui.app.HISTORY_FILE", history_file):
			app._load_history()
		assert app._task_history == []

	def test_load_unicode_entries(self, tmp_path: Path):
		history_file = tmp_path / "history.json"
		history_file.write_text(
			json.dumps(["打开百度搜索天气", "搜索Python教程"], ensure_ascii=False),
			encoding="utf-8",
		)
		app = _make_app()
		with patch("tree_walker.tui.app.HISTORY_FILE", history_file):
			app._load_history()
		assert app._task_history[0] == "打开百度搜索天气"


class TestSaveHistory:
	def test_save_creates_file(self, tmp_path: Path):
		history_file = tmp_path / "sub" / "history.json"
		app = _make_app()
		with patch("tree_walker.tui.app.HISTORY_FILE", history_file):
			app._save_history("task1")
		assert history_file.exists()
		data = json.loads(history_file.read_text(encoding="utf-8"))
		assert data == ["task1"]

	def test_save_appends_and_truncates_at_100(self, tmp_path: Path):
		history_file = tmp_path / "history.json"
		app = _make_app()
		with patch("tree_walker.tui.app.HISTORY_FILE", history_file):
			for i in range(105):
				app._save_history(f"task{i}")
		data = json.loads(history_file.read_text(encoding="utf-8"))
		assert len(data) == 100
		assert data[0] == "task5"
		assert data[-1] == "task104"

	def test_save_updates_index(self, tmp_path: Path):
		history_file = tmp_path / "history.json"
		app = _make_app()
		with patch("tree_walker.tui.app.HISTORY_FILE", history_file):
			app._save_history("a")
			assert app._history_index == 1
			app._save_history("b")
			assert app._history_index == 2

	def test_save_handles_write_error(self, tmp_path: Path):
		history_file = tmp_path / "readonly" / "history.json"
		app = _make_app()
		with patch("tree_walker.tui.app.HISTORY_FILE", history_file):
			# Should not raise, just log a warning
			app._save_history("task1")

	def test_save_preserves_unicode(self, tmp_path: Path):
		history_file = tmp_path / "history.json"
		app = _make_app()
		task = "搜索中文关键词"
		with patch("tree_walker.tui.app.HISTORY_FILE", history_file):
			app._save_history(task)
		data = json.loads(history_file.read_text(encoding="utf-8"))
		assert data == [task]


class TestSwitchToWorkingView:
	def test_switch_hides_welcome_shows_columns(self):
		app = _make_app()
		welcome_mock = MagicMock()
		welcome_mock.display = True
		columns_mock = MagicMock()

		def mock_query_one(selector, expect_type=None):
			if selector == "#welcome-panel":
				return welcome_mock
			return columns_mock

		app.query_one = mock_query_one

		app._switch_to_working_view()
		assert welcome_mock.display is False

	def test_switch_only_runs_once(self):
		app = _make_app()
		welcome_mock = MagicMock()
		welcome_mock.display = False
		columns_mock = MagicMock()

		def mock_query_one(selector, expect_type=None):
			if selector == "#welcome-panel":
				return welcome_mock
			return columns_mock

		app.query_one = mock_query_one
		app._switch_to_working_view()
		# columns_mock.display was NOT set because welcome was already hidden
		columns_mock.assert_not_called()


class TestHistoryFile:
	def test_history_file_path(self):
		assert HISTORY_FILE.name == "history.json"
		assert HISTORY_FILE.parent.name == ".treewalker"


# ── Helpers ──────────────────────────────────────────────────────────


def _make_app() -> TreeWalkerApp:
	"""Create a TreeWalkerApp with mocked dependencies."""
	llm = MagicMock()
	browser = MagicMock()
	return TreeWalkerApp(llm=llm, browser=browser)
