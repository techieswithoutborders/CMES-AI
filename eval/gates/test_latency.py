"""Gate: latency. Search P95 < 2000 ms and first-token P95 < 3000 ms ON TARGET HARDWARE. Numbers from any other machine are
reported as 'informational' and can never contribute to release_eligible."""
import time

from eval.gates.gate_common import IN_SCOPE, not_run, result

TARGETS = {"N150", "Pi5_16GB_NVMe"}


def _pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def run(client, questions, hardware, generation_enabled=False, n=100, search_thr=2000.0, token_thr=3000.0):
    qs = [q["question"] for q in questions if q["category"] in IN_SCOPE + ("oos",)]
    if not qs:
        return not_run("no questions to time", search_thr), not_run("no questions to time", token_thr)
    ts = []
    for i in range(n):
        t = time.time()
        client.search(qs[i % len(qs)], 10)
        ts.append((time.time() - t) * 1000)
    p95 = _pct(ts, 95)
    on_target = hardware in TARGETS
    s = result("ran" if on_target else "informational", p95, search_thr, on_target and p95 < search_thr,
               p50_ms=round(_pct(ts, 50), 1), p95_ms=round(p95, 1), p99_ms=round(_pct(ts, 99), 1), n=n, hardware=hardware,
               note=None if on_target else "not target hardware: informational only")
    if not generation_enabled:
        ft = result("not_applicable", None, token_thr, True, reason="generation disabled (default); gate applies only when enabled")
    else:
        ft = not_run("first-token timing for generation is not implemented in this harness", token_thr)
    return s, ft
