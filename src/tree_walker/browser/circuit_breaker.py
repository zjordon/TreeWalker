"""Circuit breaker for DOM pipeline CDP calls.

Prevents repeated CDP calls when the browser is unresponsive.
Three states: closed (normal) → open (tripped) → half_open (probing).
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class CircuitBreaker:
	"""Prevents repeated CDP calls when the browser is unresponsive.

	States:
	  - closed: all calls pass through, failures increment counter
	  - open: all calls rejected immediately, transitions to half_open after recovery_timeout
	  - half_open: one probe call allowed; success → closed, failure → open
	"""

	def __init__(
		self,
		failure_threshold: int = 3,
		recovery_timeout: float = 30.0,
	) -> None:
		self.failure_threshold = failure_threshold
		self.recovery_timeout = recovery_timeout
		self._consecutive_failures: int = 0
		self._last_failure_time: float = 0.0
		self._state: str = "closed"

	@property
	def is_open(self) -> bool:
		"""Whether calls should be rejected.

		Returns True if the breaker is open and recovery timeout hasn't elapsed.
		Transitions to half_open automatically if recovery_timeout has passed.
		"""
		if self._state == "closed":
			return False
		if self._state == "open":
			if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
				self._state = "half_open"
				logger.info("Circuit breaker transitioning to half_open")
				return False
			return True
		# half_open: allow one probe
		return False

	def record_success(self) -> None:
		"""Record a successful call. Resets to closed state."""
		if self._state != "closed":
			logger.info("Circuit breaker resetting to closed after success")
		self._consecutive_failures = 0
		self._state = "closed"

	def record_failure(self) -> None:
		"""Record a failed call. Opens breaker after threshold consecutive failures."""
		self._consecutive_failures += 1
		self._last_failure_time = time.monotonic()

		if self._state == "half_open":
			logger.warning("Circuit breaker probe failed, reopening")
			self._state = "open"
			return

		if self._consecutive_failures >= self.failure_threshold:
			self._state = "open"
			logger.warning(
				"Circuit breaker opened after %d consecutive failures",
				self._consecutive_failures,
			)

	def reset(self) -> None:
		"""Force reset to closed state (e.g., on browser reconnect)."""
		self._consecutive_failures = 0
		self._state = "closed"
		logger.info("Circuit breaker reset to closed")
