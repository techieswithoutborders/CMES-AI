#!/usr/bin/env python3
"""Hardware benchmark for the edge box (N150 vs Pi 5). REAL MEASUREMENTS ONLY.

The first version of this script returned invented numbers (random cold-boot time, hard-coded 7.5 W, simulated clock speeds).
Numbers like that get pasted into a hardware decision, so now: anything that cannot be measured here is reported as null with a
reason under `not_measured` - never a placeholder.

  python3 -m benchmarks.run_hardware_benchmarks N150 --pack build/packs/kb-1.0.2-dev --minutes 30 [--power-watts 7.1] [--cold-boot-ms 5200] [--llama-model m.gguf]
"""
import argparse
import json
import platform
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from common.nim import OfflineBackend
from edge.core import Engine, Pack

QUERIES = ["phototherapy threshold day 2", "ketamine dose for intubation", "kangaroo mother care", "antibiotics for necrotizing enterocolitis",
           "vitamin K for newborns", "neonatal seizures treatment", "chlorhexidine cord care", "positive pressure ventilation newborn",
           "HIV exposed infant prophylaxis", "transport of critically ill patients"]


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def cpu_temp_c():
    for p in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            return int(p.read_text()) / 1000.0
        except Exception:  # noqa: BLE001
            pass
    return None


def cpu_mhz():
    p = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
    try:
        return int(p.read_text()) / 1000.0
    except Exception:  # noqa: BLE001
        return None


def run(hardware, pack, minutes, power_w, cold_boot_ms, llama_model):
    res = {"hardware": hardware, "platform": platform.platform(), "machine": platform.machine(), "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "measured": [], "not_measured": {}}
    t = time.time()
    eng = Engine(Pack(pack), OfflineBackend())
    eng.search(QUERIES[0])
    res["service_start_to_first_search_ms"] = round((time.time() - t) * 1000)
    res["measured"].append("service_start_to_first_search_ms")
    if cold_boot_ms is not None:
        res["cold_boot_to_first_search_ms"] = cold_boot_ms
        res["measured"].append("cold_boot_to_first_search_ms (operator stopwatch)")
    else:
        res["not_measured"]["cold_boot_to_first_search_ms"] = "needs a real power cycle: time it and pass --cold-boot-ms"

    lat = []
    for i in range(100):
        s = time.time()
        eng.search(QUERIES[i % len(QUERIES)])
        lat.append((time.time() - s) * 1000)
    res["search_latency_ms"] = {"p50": round(pct(lat, 50), 1), "p95": round(pct(lat, 95), 1), "p99": round(pct(lat, 99), 1), "n": 100,
                                "note": "offline lexical reranker; add the real on-box embedding/reranker models before treating this as the latency gate"}
    res["measured"].append("search_latency_ms")

    # thermal: sustained query load, real sensor readings (null if the OS exposes none)
    temps, mhz, end = [], [], time.time() + minutes * 60
    while time.time() < end:
        for q in QUERIES:
            eng.search(q)
        temps.append(cpu_temp_c())
        mhz.append(cpu_mhz())
        if minutes > 1:
            time.sleep(1)
    tv, mv = [x for x in temps if x is not None], [x for x in mhz if x is not None]
    if mv:
        res["thermal"] = {"minutes": minutes, "start_mhz": mv[0], "end_mhz": mv[-1], "min_mhz": min(mv), "max_temp_c": max(tv) if tv else None,
                          "throttling_suspected": mv[-1] < 0.9 * max(mv)}
        res["measured"].append("thermal")
    else:
        res["not_measured"]["thermal"] = "no cpufreq/thermal sensors exposed by this OS"

    # A/B slot swap cost: real copy + verify of this pack
    from edge.updater import Updater
    from indexer import signing
    tmp = Path(tempfile.mkdtemp(prefix="twb-bench-"))
    try:
        keys = signing.ensure_dev_keys("keys")
        u = Updater(tmp, signing.load_pub_b64(keys / "root.pub"), allow_dev=True)
        s = time.time()
        u.install(pack)
        swap = time.time() - s
        size = sum(f.stat().st_size for f in Path(pack).rglob("*") if f.is_file())
        res["slot_swap"] = {"seconds": round(swap, 2), "bytes_written": size, "note": "NVMe endurance must be computed from bytes_written x updates/year against the drive's rated TBW"}
        res["measured"].append("slot_swap")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if power_w is not None:
        res["power_draw_w"] = power_w
        res["measured"].append("power_draw_w (external meter)")
    else:
        res["not_measured"]["power_draw_w"] = "needs a USB/wall power meter: pass --power-watts"
    exe = shutil.which("llama-bench") or shutil.which("llama-cli")
    if exe and llama_model:
        p = subprocess.run([exe, "-m", llama_model, "-p", "64", "-n", "64"] if "bench" in exe else [exe, "-m", llama_model, "-n", "64", "-p", "Hello"], capture_output=True, text=True)
        res["llama_raw_output_tail"] = (p.stdout + p.stderr)[-600:]
        res["measured"].append("llama (raw output only; parse tokens/s from it)")
    else:
        res["not_measured"]["tokens_per_sec"] = "llama.cpp not found or --llama-model not given"
        res["not_measured"]["first_token_latency"] = "needs the generation model; generation is off by default"
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("hardware", choices=["N150", "Pi5_16GB_NVMe", "other"])
    ap.add_argument("--pack", required=True)
    ap.add_argument("--minutes", type=int, default=1)
    ap.add_argument("--power-watts", type=float)
    ap.add_argument("--cold-boot-ms", type=int)
    ap.add_argument("--llama-model")
    ap.add_argument("--output", default="benchmark_%s.json")
    a = ap.parse_args()
    r = run(a.hardware, a.pack, a.minutes, a.power_watts, a.cold_boot_ms, a.llama_model)
    out = a.output % a.hardware if "%s" in a.output else a.output
    Path(out).write_text(json.dumps(r, indent=1))
    print(json.dumps(r, indent=1))
