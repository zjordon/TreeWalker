"""CDP call timeout and retry helpers for the DOM pipeline.

Implements a two-phase timeout + retry pattern for parallel CDP requests:
  Phase 1: asyncio.wait(all_tasks, timeout=first_timeout) — generous initial window
  Phase 2: cancel pending, re-create only failed tasks, asyncio.wait(retry, timeout=retry_timeout)
  Phase 3: cancel remaining pending, report per-source status

Does NOT raise exceptions — callers inspect CDPBatchResult to apply degradation.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class CDPSourceStatus(str, Enum):
	OK = "ok"
	TIMEOUT = "timeout"
	FAILED = "failed"
	RETRIED_OK = "retried_ok"


@dataclass
class CDPSourceResult:
	"""Outcome for a single CDP source after the two-phase timeout+retry."""
	name: str
	status: CDPSourceStatus
	value: Any = None
	error: str | None = None
	first_attempt_ms: float = 0.0
	retry_attempt_ms: float = 0.0


@dataclass
class CDPBatchResult:
	"""Collects outcomes from all CDP sources in a single batch."""
	sources: dict[str, CDPSourceResult] = field(default_factory=dict)
	total_ms: float = 0.0

	@property
	def failed_names(self) -> list[str]:
		return [
			k for k, v in self.sources.items()
			if v.status in (CDPSourceStatus.TIMEOUT, CDPSourceStatus.FAILED)
		]

	def get(self, name: str, default: Any = None) -> Any:
		r = self.sources.get(name)
		if r and r.status in (CDPSourceStatus.OK, CDPSourceStatus.RETRIED_OK):
			return r.value
		return default


async def run_cdp_batch(
	factories: dict[str, Callable[[], Awaitable]],
	*,
	first_timeout: float = 10.0,
	retry_timeout: float = 2.0,
) -> CDPBatchResult:
	"""Run named CDP call factories with two-phase timeout and selective retry.

	Args:
		factories: Mapping of source name → async callable (factory) that creates
			a fresh coroutine each time. Used for both initial attempt and retry.
		first_timeout: Seconds for initial batch (default 10s).
		retry_timeout: Seconds for retry of failed/timed-out tasks (default 2s).

	Returns:
		CDPBatchResult with per-source status. Never raises.
	"""
	batch_start = time.monotonic()
	batch_result = CDPBatchResult()

	# Phase 1: launch all tasks with initial timeout
	name_to_task: dict[str, asyncio.Task] = {
		name: asyncio.create_task(factory()) for name, factory in factories.items()
	}

	done, pending = await asyncio.wait(
		name_to_task.values(), timeout=first_timeout,
	)

	task_to_name = {t: n for n, t in name_to_task.items()}

	# Collect results from Phase 1 done tasks
	pending_names: set[str] = set()
	for task in done:
		name = task_to_name[task]
		status, value, error, elapsed = _extract_result(task, batch_start)
		batch_result.sources[name] = CDPSourceResult(
			name=name, status=status, value=value, error=error,
			first_attempt_ms=elapsed,
		)
		if status != CDPSourceStatus.OK:
			pending_names.add(name)

	# Cancel pending tasks from Phase 1
	for task in pending:
		task.cancel()
		name = task_to_name[task]
		pending_names.add(name)
		# Add placeholder for sources that didn't even get to done
		if name not in batch_result.sources:
			batch_result.sources[name] = CDPSourceResult(
				name=name, status=CDPSourceStatus.TIMEOUT,
				error="timed out in Phase 1",
				first_attempt_ms=first_timeout * 1000,
			)

	if not pending_names:
		batch_result.total_ms = (time.monotonic() - batch_start) * 1000
		return batch_result

	# Phase 2: retry only failed/timed-out tasks with fresh coroutines
	retry_tasks: dict[str, asyncio.Task] = {}
	for name in pending_names:
		if name in factories:
			retry_tasks[name] = asyncio.create_task(factories[name]())

	if not retry_tasks:
		batch_result.total_ms = (time.monotonic() - batch_start) * 1000
		return batch_result

	logger.info("Retrying %d CDP sources: %s", len(retry_tasks), ", ".join(retry_tasks.keys()))

	done2, pending2 = await asyncio.wait(
		retry_tasks.values(), timeout=retry_timeout,
	)

	task_to_name_2 = {t: n for n, t in retry_tasks.items()}

	for task in done2:
		name = task_to_name_2[task]
		status, value, error, elapsed = _extract_result(task, batch_start)
		retry_ms = (time.monotonic() - batch_start) * 1000 - batch_result.sources[name].first_attempt_ms
		batch_result.sources[name] = CDPSourceResult(
			name=name,
			status=CDPSourceStatus.RETRIED_OK if status == CDPSourceStatus.OK else status,
			value=value, error=error,
			first_attempt_ms=batch_result.sources[name].first_attempt_ms,
			retry_attempt_ms=retry_ms,
		)

	for task in pending2:
		task.cancel()
		name = task_to_name_2[task]
		prev = batch_result.sources.get(name)
		batch_result.sources[name] = CDPSourceResult(
			name=name, status=CDPSourceStatus.TIMEOUT,
			error="timed out in retry Phase 2",
			first_attempt_ms=prev.first_attempt_ms if prev else first_timeout * 1000,
			retry_attempt_ms=retry_timeout * 1000,
		)

	batch_result.total_ms = (time.monotonic() - batch_start) * 1000

	failed = batch_result.failed_names
	if failed:
		logger.warning(
			"CDP batch completed in %.0fms, failed sources: %s",
			batch_result.total_ms, ", ".join(failed),
		)
	else:
		retried = [k for k, v in batch_result.sources.items() if v.status == CDPSourceStatus.RETRIED_OK]
		if retried:
			logger.info(
				"CDP batch completed in %.0fms, retried OK: %s",
				batch_result.total_ms, ", ".join(retried),
			)

	return batch_result


def _extract_result(
	task: asyncio.Task,
	batch_start: float,
) -> tuple[CDPSourceStatus, Any, str | None, float]:
	"""Extract result from a completed/cancelled task."""
	elapsed = (time.monotonic() - batch_start) * 1000

	if task.cancelled():
		return CDPSourceStatus.TIMEOUT, None, "timed out", elapsed

	exception = task.exception()
	if exception is not None:
		return CDPSourceStatus.FAILED, None, str(exception), elapsed

	return CDPSourceStatus.OK, task.result(), None, elapsed
