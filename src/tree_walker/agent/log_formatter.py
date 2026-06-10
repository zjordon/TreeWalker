"""Structured step log formatting — ANSI colors, emoji prefixes, aligned layout."""

from __future__ import annotations

import logging

# ── ANSI color constants ──────────────────────────────────────────────

RESET = "\033[0m"
GREEN = "\033[32m"
RED = "\033[31m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"


# ── Public API ────────────────────────────────────────────────────────


def format_action_params(
	params: dict,
	*,
	max_length: int = 150,
	colorize: bool = True,
) -> str:
	"""Format action parameters as ``key: value, key: value``.

	Param names are colored magenta when *colorize* is True.
	Values longer than *max_length* are truncated.
	"""
	if not params:
		return ""
	parts: list[str] = []
	for k, v in params.items():
		s = str(v)
		if len(s) > max_length:
			s = s[:max_length] + "..."
		if colorize:
			parts.append(f"{MAGENTA}{k}{RESET}: {s}")
		else:
			parts.append(f"{k}: {s}")
	return ", ".join(parts)


def log_response(
	evaluation: str,
	memory: str,
	next_goal: str,
	action_name: str,
	action_params: dict,
	step: int,
	*,
	logger: logging.Logger | None = None,
) -> None:
	"""Log a structured four-line block for the agent step.

	Lines: Eval, Memory, Next goal, Action — all at INFO level.
	"""
	if logger is None:
		logger = logging.getLogger(__name__)

	# 1. Eval — semantic color
	if evaluation:
		eval_lower = evaluation.lower()
		if "success" in eval_lower:
			logger.info("  \033[32m👍 Eval: %s\033[0m", evaluation)
		elif "fail" in eval_lower:
			logger.info("  \033[31m⚠️ Eval: %s\033[0m", evaluation)
		else:
			logger.info("  ❔ Eval: %s", evaluation)

	# 2. Memory
	if memory:
		logger.info("  🧠 Memory: %s", memory)

	# 3. Next goal
	if next_goal:
		logger.info("  \033[34m🎯 Next goal: %s\033[0m", next_goal)

	# 4. Action
	params_str = format_action_params(action_params)
	if params_str:
		logger.info(
			"  ▶️  %s%s%s: %s",
			BLUE, action_name, RESET,
			params_str,
		)
	else:
		logger.info("  ▶️  %s%s%s", BLUE, action_name, RESET)


def log_step_completion(
	step: int,
	duration: float,
	ok_count: int,
	err_count: int,
	*,
	logger: logging.Logger | None = None,
) -> None:
	"""Log step duration with color-coded success/failure marker."""
	if logger is None:
		logger = logging.getLogger(__name__)

	if err_count > 0:
		logger.info(
			"  \033[31m❌ Step %d: %.2fs [OK=%d ERR=%d]\033[0m",
			step, duration, ok_count, err_count,
		)
	else:
		logger.info(
			"  \033[32m✅ Step %d: %.2fs [OK=%d]\033[0m",
			step, duration, ok_count,
		)
