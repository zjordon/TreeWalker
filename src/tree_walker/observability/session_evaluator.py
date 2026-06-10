"""Heuristic session evaluator — runs checks at session end."""
from __future__ import annotations

from statistics import mean


class SessionEvaluator:
    """Evaluates a completed session with heuristic checks."""

    def evaluate(self, has_done: bool, metrics_summary: dict) -> dict:
        checks = [
            {
                "name": "has_final_response",
                "passed": has_done,
                "detail": "Task completed with done action" if has_done else "No done action found",
            },
            {
                "name": "no_high_severity_anomaly",
                "passed": metrics_summary.get("anomaly_count_by_severity", {}).get("high", 0) == 0,
                "detail": f"High severity anomalies: {metrics_summary.get('anomaly_count_by_severity', {}).get('high', 0)}",
            },
            *self._check_tool_error_rate(metrics_summary),
            *self._check_avg_step_duration(metrics_summary),
        ]

        passed = all(c["passed"] for c in checks)
        summary = self._build_summary(passed, checks, metrics_summary)

        return {
            "passed": passed,
            "checks": checks,
            "summary": summary,
        }

    def _check_tool_error_rate(self, metrics: dict) -> list[dict]:
        calls = metrics.get("tool_calls", 0)
        if calls == 0:
            return []
        errors = metrics.get("tool_errors", 0)
        rate = errors / calls
        passed = rate <= 0.3
        return [{
            "name": "tool_error_rate",
            "passed": passed,
            "detail": f"Error rate: {rate:.0%} ({errors}/{calls})",
        }]

    def _check_avg_step_duration(self, metrics: dict) -> list[dict]:
        durations = metrics.get("step_durations", [])
        if not durations:
            return []
        avg = mean(durations)
        passed = avg <= 30.0
        return [{
            "name": "avg_step_duration",
            "passed": passed,
            "detail": f"Average step duration: {avg:.1f}s",
        }]

    def _build_summary(self, passed: bool, checks: list[dict], metrics: dict) -> str:
        status = "PASSED" if passed else "FAILED"
        total_steps = len(metrics.get("step_durations", []))
        tool_info = f"tools={metrics.get('tool_calls', 0)} errors={metrics.get('tool_errors', 0)}"
        failed_checks = [c["name"] for c in checks if not c["passed"]]
        detail = f" failed_checks={failed_checks}" if failed_checks else ""
        return f"Session evaluation {status}: steps={total_steps} {tool_info}{detail}"
