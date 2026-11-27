#!/usr/bin/env python3
"""Search one shard inside a private Kaggle session.

CPU: goodpjw2008's triwalk annealer (Apache-2.0), alternating alternative lineages and the current best programs.
GPU (optional): rosswade's exact-repair LNS (CC0), one process per GPU, each in its own workspace.
Every result is replayed by the engines; the local repository replays everything again before using it.

CONFIG is rewritten per kernel by kaggle_search.py. The SEARCH_* variables exist only for local tests.
"""
import csv
import glob
import json
import os
import shutil
import subprocess
import sys
import time

CONFIG = {"shard": 0, "shards": 1, "hours": 11.0, "gpu": False, "reserve": 240}
CONFIG.update(json.loads(os.environ.get("SEARCH_CONFIG", "{}")))
INPUT = os.environ.get("SEARCH_INPUT", "/kaggle/input")
OUTPUT = os.environ.get("SEARCH_OUTPUT", "/kaggle/working")
START = time.time()
DEADLINE = START + CONFIG["hours"] * 3600
csv.field_size_limit(1 << 28)


def say(text):
    print(f"[{(time.time() - START) / 3600:5.2f} h] {text}", flush=True)


def find(name):
    hits = sorted(glob.glob(os.path.join(INPUT, "**", name), recursive=True))
    if not hits:
        raise FileNotFoundError(f"{name} not found under {INPUT}")
    return hits[0]


def programs(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return {row["id"]: json.loads(row["program"]) for row in csv.DictReader(handle)}


def total(path):
    return sum(len(program) for program in programs(path).values())


def workspace(name, ids):
    root = os.path.join(OUTPUT, name)
    for folder in ("src", "bin", "work", "data"):
        os.makedirs(os.path.join(root, folder), exist_ok=True)
    for source in ("elf.py", "pfmt.py", "tri_driver.py"):
        shutil.copy(find(source), os.path.join(root, "src", source))
    shutil.copy(find("puzzles.csv"), os.path.join(root, "data", "puzzles.csv"))
    shutil.copy(find("best.csv"), os.path.join(root, "work", "best.csv"))
    shutil.copy(find("lineages.jsonl"), os.path.join(root, "work", "lineages.jsonl"))
    with open(os.path.join(root, "work", "ids.txt"), "w") as handle:
        handle.write("".join(f"{pid}\n" for pid in ids))
    return root


def start_gpus(all_ids):
    try:
        listing = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        say("no GPU visible; continuing on CPU only")
        return []
    count = sum(line.startswith("GPU ") for line in listing.splitlines())
    say(f"{count} GPU(s)")
    running = []
    for device in range(count):
        root = workspace(f"ws_gpu{device}", all_ids[device::count])
        third_party = os.path.join(root, "third_party", "rosswade")
        os.makedirs(third_party)
        for source in ("lns_gpu.py", "gpu_mitm2.py"):
            shutil.copy(find(source), os.path.join(third_party, source))
        shutil.copy(find("gpu_lns.py"), os.path.join(root, "src", "gpu_lns.py"))
        open(os.path.join(root, "work", "archive.jsonl"), "w").close()
        log = open(os.path.join(OUTPUT, f"gpu{device}.log"), "w")
        minutes = max(1.0, (DEADLINE - time.time() - CONFIG["reserve"]) / 60)
        process = subprocess.Popen(
            [sys.executable, os.path.join(root, "src", "gpu_lns.py"), os.path.join(root, "work", "ids.txt"), "60",
             f"{minutes:.0f}"],
            cwd=root, stdout=log, stderr=subprocess.STDOUT, env={**os.environ, "CUDA_VISIBLE_DEVICES": str(device)})
        running.append((device, root, process, log))
    return running


def gpu_status(gpus):
    for device, root, process, _ in gpus:
        with open(os.path.join(OUTPUT, f"gpu{device}.log")) as handle:
            lines = handle.read().strip().splitlines()
        state = "running" if process.poll() is None else f"exit {process.returncode}"
        say(f"gpu{device} {state}: total {total(os.path.join(root, 'work', 'best.csv'))} | {lines[-1][-160:] if lines else '-'}")


def main():
    base = programs(find("best.csv"))
    all_ids = sorted((pid for pid in base if len(base[pid]) >= 20), key=lambda pid: (-len(base[pid]), pid))
    shard = all_ids[CONFIG["shard"]::CONFIG["shards"]]
    root = workspace("ws_cpu", shard)
    binary = os.path.join(root, "bin", "triwalk")
    subprocess.run([os.environ.get("SEARCH_CXX", "g++"), "-std=c++17", "-O3", "-march=native", "-DNDEBUG",
                    *os.environ.get("SEARCH_CXXFLAGS", "").split(), find("triwalk.cpp"), "-o", binary], check=True)
    best = os.path.join(root, "work", "best.csv")
    initial = total(best)
    jobs = os.cpu_count() or 4
    say(f"config {CONFIG} | {len(shard)} puzzles in this shard | {jobs} CPU jobs | start total {initial}")
    gpus = start_gpus(all_ids) if CONFIG["gpu"] else []
    if gpus:  # each GPU process also needs host CPU time
        jobs = max(2, jobs - len(gpus))
        say(f"{jobs} CPU jobs alongside {len(gpus)} GPU process(es)")
    round_number = 0
    while DEADLINE - time.time() > CONFIG["reserve"] + 30:
        budget = min(3300, DEADLINE - time.time() - CONFIG["reserve"])
        if round_number % 2 == 0:
            mode = ["--mode", "lineage", "--secs", "45", "--max-excess", "6"]
        else:
            seconds = min(240, 60 + 30 * (round_number // 2))
            mode = ["--mode", "best", "--secs", str(seconds), "--variant-prob", "0.6", "--repeat", "20"]
        command = [sys.executable, os.path.join(root, "src", "tri_driver.py"), *mode, "--only",
                   os.path.join(root, "work", "ids.txt"), "--budget", f"{budget:.0f}", "--jobs", str(jobs),
                   "--tag", f"r{round_number}"]
        say(f"round {round_number}: {' '.join(mode)} budget {budget:.0f}s")
        process = subprocess.Popen(command, cwd=root)
        last_report = time.time()
        while process.poll() is None:
            time.sleep(5)
            if time.time() - last_report >= 300:
                last_report = time.time()
                say(f"round {round_number} running: cpu total {total(best)} (saved {initial - total(best)})")
                gpu_status(gpus)
        shutil.copy(best, os.path.join(OUTPUT, "best_cpu.csv"))
        say(f"round {round_number} done, exit {process.returncode}: cpu total {total(best)} (saved {initial - total(best)})")
        gpu_status(gpus)
        if process.returncode:
            break
        round_number += 1
    summary = {"config": CONFIG, "start_total": initial, "cpu_total": total(best), "rounds": round_number}
    shutil.copy(best, os.path.join(OUTPUT, "best_cpu.csv"))
    shutil.copy(os.path.join(root, "work", "tri_stats.csv"), os.path.join(OUTPUT, "tri_stats.csv"))
    for device, gpu_root, process, log in gpus:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=60)
        log.close()
        shutil.copy(os.path.join(gpu_root, "work", "best.csv"), os.path.join(OUTPUT, f"best_gpu{device}.csv"))
        summary[f"gpu{device}_total"] = total(os.path.join(OUTPUT, f"best_gpu{device}.csv"))
        shutil.rmtree(gpu_root)
    shutil.rmtree(root)
    with open(os.path.join(OUTPUT, "summary.json"), "w") as handle:
        json.dump(summary, handle, indent=2)
    say(f"done: {summary}")


if __name__ == "__main__":
    main()
