from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Callable, Any

_PROVIDER_COST: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.00015, 0.00060),
    "gemini-2.0-flash": (0.00010, 0.00040),
    "gemini-2.0-flash-lite": (0.000075, 0.00030),
    "gemini-flash-latest": (0.00015, 0.00060),
    "llama-3.3-70b-versatile": (0.00059, 0.00079),  # Groq
    "llama-3.1-70b-versatile": (0.00059, 0.00079),  # Groq
    "qwen/qwen3.6-plus:free": (0.0, 0.0),  # OpenRouter free tier
    "qwen/qwen2.5-72b-instruct:free": (0.0, 0.0),
    "meta-llama/llama-3.3-70b-instruct:free": (0.0, 0.0),
    "ollama/local": (0.0, 0.0),  # local — no cost
}


_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = _PROVIDER_COST.get(model, (0.002, 0.002))  # default: GPT-3.5 ballpark
    return (input_tokens / 1000) * prices[0] + (output_tokens / 1000) * prices[1]


@dataclass
class ProviderSpan:
    """One AI provider call inside an agent invocation."""

    provider: str  # "gemini" | "groq" | "openrouter" | "ollama"
    model: str
    status: str  # "ok" | "error" | "skipped"
    latency_s: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: str = ""


@dataclass
class AgentSpan:
    """One full agent invocation (scraper / qa / screenshot / reporter)."""

    agent: str
    started_at: str
    latency_s: float
    status: str  # "ok" | "error" | "partial"
    services_in: int  # items consumed from input queue
    services_out: int  # items produced for next queue
    issues_found: int
    retries: int
    provider_spans: list[ProviderSpan] = field(default_factory=list)
    error: str = ""

    @property
    def total_tokens(self) -> int:
        return sum(s.input_tokens + s.output_tokens for s in self.provider_spans)

    @property
    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.provider_spans)


class AgentIQ:
    """
    Lightweight agent profiler.

    Thread-safe. Can be shared across concurrent pipeline workers.
    """

    def __init__(self, trace_file: str = ""):
        self._spans: list[AgentSpan] = []
        self._lock = threading.Lock()
        self._trace_file = trace_file
        self._run_started = time.time()
        self._run_start_label = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def run(
        self,
        agent_name: str,
        agent_fn: Callable[[dict], dict],
        state: dict,
        retries: int = 0,
    ) -> dict:
        """
        Call agent_fn(state), record the span, and return the result.

        Counts services_in from the relevant pending queue, services_out from
        the queue the agent fills, and issues_found from any 'issues' field in
        returned results.
        """
        input_queue = _input_queue_key(agent_name)
        output_queue = _output_queue_key(agent_name)

        services_in = len(state.get(input_queue, []))
        t0 = time.perf_counter()
        started_at = datetime.now().strftime("%H:%M:%S")

        try:
            result = agent_fn(state)
            latency = time.perf_counter() - t0
            services_out = len(result.get(output_queue, []))
            issues_found = _count_issues(result)
            status = "ok"
            error = ""
        except Exception as exc:
            latency = time.perf_counter() - t0
            result = {}
            services_out = 0
            issues_found = 0
            status = "error"
            error = str(exc)[:200]

        span = AgentSpan(
            agent=agent_name,
            started_at=started_at,
            latency_s=round(latency, 2),
            status=status,
            services_in=services_in,
            services_out=services_out,
            issues_found=issues_found,
            retries=retries,
            error=error,
        )

        with self._lock:
            self._spans.append(span)

        return result

    def record_provider_call(
        self,
        agent_name: str,
        provider: str,
        model: str,
        status: str,
        latency_s: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
        error: str = "",
    ) -> None:
        """
        Record a single AI provider call.

        Call this from inside ai.py after each provider attempt so the trace
        captures which model was actually used and what it cost.
        """
        cost = _estimate_cost(model, input_tokens, output_tokens)
        provider_span = ProviderSpan(
            provider=provider,
            model=model,
            status=status,
            latency_s=round(latency_s, 2),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost, 6),
            error=error,
        )
        with self._lock:
            for span in reversed(self._spans):
                if span.agent == agent_name:
                    span.provider_spans.append(provider_span)
                    return
            placeholder = AgentSpan(
                agent=agent_name,
                started_at=datetime.now().strftime("%H:%M:%S"),
                latency_s=0,
                status="running",
                services_in=0,
                services_out=0,
                issues_found=0,
                retries=0,
            )
            placeholder.provider_spans.append(provider_span)
            self._spans.append(placeholder)

    def report(self) -> None:
        """Print a formatted profiling report and optionally write the trace file."""
        with self._lock:
            spans = list(self._spans)

        total_wall = time.time() - self._run_started
        total_tokens = sum(s.total_tokens for s in spans)
        total_cost = sum(s.total_cost_usd for s in spans)
        total_issues = sum(s.issues_found for s in spans)

        print("\n" + "=" * 64)
        print("  Agent IQ — Pipeline Profiling Report")
        print(f"  Run started : {self._run_start_label}")
        print(f"  Wall time   : {total_wall:.1f}s")
        print(f"  Total tokens: {total_tokens:,}")
        print(f"  Est. cost   : ${total_cost:.4f} USD")
        print(f"  Issues found: {total_issues}")
        print("=" * 64)

        for span in spans:
            status_label = {"ok": "OK", "error": "FAIL", "partial": "PART"}.get(
                span.status, span.status.upper()
            )
            retry_label = f" (+{span.retries} retries)" if span.retries else ""
            print(
                f"\n  [{span.started_at}] {span.agent:<18} {status_label}{retry_label}"
                f"\n    latency    : {span.latency_s}s"
                f"\n    throughput : {span.services_in} in -> {span.services_out} out"
            )
            if span.issues_found:
                print(f"    issues     : {span.issues_found}")
            if span.error:
                print(f"    error      : {span.error}")
            if span.provider_spans:
                print("    providers  :")
                for ps in span.provider_spans:
                    token_label = f"{ps.input_tokens}+{ps.output_tokens} tok"
                    cost_label = f"${ps.cost_usd:.5f}" if ps.cost_usd else "free"
                    err_label = f" [{ps.error[:50]}]" if ps.error else ""
                    print(
                        f"      {ps.status:<6} {ps.provider}/{ps.model:<35} "
                        f"{ps.latency_s:.1f}s  {token_label}  {cost_label}{err_label}"
                    )

        provider_totals: dict[str, dict] = {}
        for span in spans:
            for ps in span.provider_spans:
                key = ps.provider
                if key not in provider_totals:
                    provider_totals[key] = {
                        "calls": 0,
                        "ok": 0,
                        "tokens": 0,
                        "cost": 0.0,
                    }
                provider_totals[key]["calls"] += 1
                if ps.status == "ok":
                    provider_totals[key]["ok"] += 1
                provider_totals[key]["tokens"] += ps.input_tokens + ps.output_tokens
                provider_totals[key]["cost"] += ps.cost_usd

        if provider_totals:
            print("\n  Provider summary:")
            for provider, totals in sorted(provider_totals.items()):
                success_rate = (
                    f"{totals['ok']}/{totals['calls']}" if totals["calls"] else "0/0"
                )
                print(
                    f"    {provider:<14} {success_rate} ok  "
                    f"{totals['tokens']:>8,} tok  ${totals['cost']:.4f}"
                )

        print("=" * 64)

        if self._trace_file:
            self._write_trace(spans, total_wall, total_tokens, total_cost)

    def benchmark(self, ground_truth: list[dict], actual: list[dict]) -> dict:
        """
        Compare pipeline output against a ground-truth issue set.

        ground_truth: list of dicts with at least {"psid", "check", "field"}
        actual:       list of issue dicts from the pipeline

        Returns precision, recall, F1, and per-check breakdown.
        """

        def _key(issue: dict) -> tuple:
            return (
                issue.get("psid", ""),
                issue.get("check", issue.get("check_id", "")),
                issue.get("field", ""),
            )

        gt_keys = {_key(i) for i in ground_truth}
        ac_keys = {_key(i) for i in actual}

        tp = len(gt_keys & ac_keys)
        fp = len(ac_keys - gt_keys)
        fn = len(gt_keys - ac_keys)

        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall)
            else 0.0
        )

        print("\n  Agent IQ — Benchmark Results")
        print(f"    Ground truth issues : {len(gt_keys)}")
        print(f"    Pipeline issues     : {len(ac_keys)}")
        print(f"    True positives      : {tp}")
        print(f"    False positives     : {fp}")
        print(f"    False negatives     : {fn}")
        print(f"    Precision           : {precision:.2%}")
        print(f"    Recall              : {recall:.2%}")
        print(f"    F1 score            : {f1:.2%}")

        return {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "tp": tp,
            "fp": fp,
            "fn": fn,
        }

    def _write_trace(
        self,
        spans: list[AgentSpan],
        wall_time: float,
        total_tokens: int,
        total_cost: float,
    ) -> None:
        os.makedirs(os.path.dirname(self._trace_file) or ".", exist_ok=True)
        payload = {
            "run_started": self._run_start_label,
            "wall_time_s": round(wall_time, 2),
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "spans": [asdict(s) for s in spans],
        }
        try:
            with open(self._trace_file, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            print(f"\n  [agent_iq] trace written -> {self._trace_file}")
        except Exception as exc:
            print(f"\n  [agent_iq] trace write failed: {exc}")


def _input_queue_key(agent_name: str) -> str:
    return {
        "scraper": "pending_scrape",
        "qa": "pending_audit",
        "screenshot": "pending_screenshot",
        "reporter": "pending_report",
    }.get(agent_name, "")


def _output_queue_key(agent_name: str) -> str:
    return {
        "scraper": "pending_audit",
        "qa": "pending_screenshot",
        "screenshot": "pending_report",
        "reporter": "completed",
    }.get(agent_name, "")


def _count_issues(result: dict) -> int:
    """Count issues from whatever shape the agent returns."""
    total = 0
    for key in ("pending_screenshot", "pending_report", "completed"):
        for item in result.get(key, []):
            total += len(item.get("issues", []))
    total += result.get("total_issues", 0)
    return total
