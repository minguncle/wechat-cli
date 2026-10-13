"""
从微信进程内存中提取所有数据库的缓存raw key

WCDB为每个DB缓存: x'<64hex_enc_key><32hex_salt>'
salt嵌在hex字符串中，可以直接匹配DB文件的salt
"""
import ctypes
import ctypes.wintypes as wt
import json
import os, sys, time, re

import functools
print = functools.partial(print, flush=True)

from key_scan_common import (
    collect_db_files, scan_memory_for_keys, cross_verify_keys, save_results,
    scan_raw_key_candidates_for_any_salt,
)

kernel32 = ctypes.windll.kernel32
MEM_COMMIT = 0x1000
READABLE = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_uint64), ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", wt.DWORD), ("_pad1", wt.DWORD),
        ("RegionSize", ctypes.c_uint64), ("State", wt.DWORD),
        ("Protect", wt.DWORD), ("Type", wt.DWORD), ("_pad2", wt.DWORD),
    ]


def get_pids():
    """返回所有 Weixin.exe 进程的 (pid, mem_kb) 列表，按内存降序"""
    import subprocess
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
                       capture_output=True, text=True)
    pids = []
    for line in r.stdout.strip().split('\n'):
        if not line.strip():
            continue
        p = line.strip('"').split('","')
        if len(p) >= 5:
            pid = int(p[1])
            mem = int(p[4].replace(',', '').replace(' K', '').strip() or '0')
            pids.append((pid, mem))
    if not pids:
        raise RuntimeError("Weixin.exe 未运行")
    pids.sort(key=lambda x: x[1], reverse=True)
    for pid, mem in pids:
        print(f"[+] Weixin.exe PID={pid} ({mem // 1024}MB)")
    return pids


def read_mem(h, addr, sz):
    buf = ctypes.create_string_buffer(sz)
    n = ctypes.c_size_t(0)
    if kernel32.ReadProcessMemory(h, ctypes.c_uint64(addr), buf, sz, ctypes.byref(n)):
        return buf.raw[:n.value]
    return None


def enum_regions(h):
    regs = []
    addr = 0
    mbi = MBI()
    while addr < 0x7FFFFFFFFFFF:
        if kernel32.VirtualQueryEx(h, ctypes.c_uint64(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        if mbi.State == MEM_COMMIT and mbi.Protect in READABLE and 0 < mbi.RegionSize < 500 * 1024 * 1024:
            regs.append((mbi.BaseAddress, mbi.RegionSize))
        nxt = mbi.BaseAddress + mbi.RegionSize
        if nxt <= addr:
            break
        addr = nxt
    return regs


def _select_pointer_scan_targets(diagnostics, pid, seen, limit=100):
    selected = []
    for target in diagnostics.get("pointer_scan_targets", []):
        if int(target.get("pid", -1)) != int(pid):
            continue
        try:
            target_addr = int(str(target.get("target", "0")), 16)
        except ValueError:
            continue
        key = (int(pid), target_addr)
        if key in seen:
            continue
        seen.add(key)
        selected.append(target)
        if len(selected) >= limit:
            break
    return selected


def _read_pointer_window(h, target_addr, window=1024):
    mbi = MBI()
    if kernel32.VirtualQueryEx(h, ctypes.c_uint64(target_addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
        return None
    if mbi.State != MEM_COMMIT or mbi.Protect not in READABLE:
        return None

    region_start = int(mbi.BaseAddress)
    region_end = int(mbi.BaseAddress + mbi.RegionSize)
    start = max(region_start, int(target_addr) - int(window))
    end = min(region_end, int(target_addr) + int(window) + 32)
    if end <= start:
        return None
    data = read_mem(h, start, end - start)
    if not data:
        return None
    return start, data


def _scan_pointer_targets_for_pid(h, pid, diagnostics, hex_re, db_files, salt_to_dbs,
                                  key_map, remaining_salts, pointer_scan_seen,
                                  max_rounds=3, batch_limit=120):
    total_matches = 0
    total_targets = 0
    for round_idx in range(max_rounds):
        if not remaining_salts:
            break
        targets = _select_pointer_scan_targets(
            diagnostics, pid, pointer_scan_seen, limit=batch_limit
        )
        if not targets:
            break
        total_targets += len(targets)
        print(f"  [ptr] 第 {round_idx + 1} 轮扫描 {len(targets)} 个指针目标")

        for idx, target in enumerate(targets, 1):
            if not remaining_salts:
                break
            target_addr = int(str(target["target"]), 16)
            window = _read_pointer_window(h, target_addr)
            if not window:
                diagnostics["pointer_target_read_fail"] = diagnostics.get("pointer_target_read_fail", 0) + 1
                continue
            base_addr, data = window
            diagnostics["pointer_target_read_ok"] = diagnostics.get("pointer_target_read_ok", 0) + 1
            total_matches += scan_memory_for_keys(
                data, hex_re, db_files, salt_to_dbs, key_map,
                remaining_salts, base_addr, pid, print,
                diagnostics=diagnostics,
            )
            total_matches += scan_raw_key_candidates_for_any_salt(
                data, db_files, salt_to_dbs, key_map, remaining_salts,
                base_addr, pid, print, "raw 32-byte pointer target",
                diagnostics=diagnostics, stride=8,
            )
            if idx % 50 == 0:
                print(
                    f"    [ptr] {idx}/{len(targets)} targets, "
                    f"{len(key_map)}/{len(salt_to_dbs)} salts matched"
                )

    if total_targets:
        print(
            f"  [ptr] 完成 {total_targets} 个指针目标, "
            f"新增/复用候选 {total_matches}"
        )
    return total_matches


def main():
    from config import load_config
    _cfg = load_config()
    db_dir = _cfg["db_dir"]
    out_file = _cfg["keys_file"]

    print("=" * 60)
    print("  提取所有微信数据库密钥")
    print("=" * 60)

    # 1. 收集所有DB文件及其salt
    db_files, salt_to_dbs = collect_db_files(db_dir)

    print(f"\n找到 {len(db_files)} 个数据库, {len(salt_to_dbs)} 个不同的salt")
    for salt_hex, dbs in sorted(salt_to_dbs.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"  salt {salt_hex}: {', '.join(dbs)}")

    # 2. 打开所有微信进程
    pids = get_pids()

    hex_re = re.compile(b"x'([0-9a-fA-F]{64,192})'")
    key_map = {}
    remaining_salts = set(salt_to_dbs.keys())
    all_hex_matches = 0
    diagnostics = {}
    pointer_scan_seen = set()
    t0 = time.time()

    for pid, mem_kb in pids:
        h = kernel32.OpenProcess(0x0010 | 0x0400, False, pid)
        if not h:
            print(f"[WARN] 无法打开进程 PID={pid}，跳过")
            continue

        try:
            regions = enum_regions(h)
            total_bytes = sum(s for _, s in regions)
            total_mb = total_bytes / 1024 / 1024
            print(f"\n[*] 扫描 PID={pid} ({total_mb:.0f}MB, {len(regions)} 区域)")

            scanned_bytes = 0
            for reg_idx, (base, size) in enumerate(regions):
                data = read_mem(h, base, size)
                scanned_bytes += size
                if not data:
                    continue

                all_hex_matches += scan_memory_for_keys(
                    data, hex_re, db_files, salt_to_dbs,
                    key_map, remaining_salts, base, pid, print,
                    diagnostics=diagnostics,
                )

                if (reg_idx + 1) % 200 == 0:
                    elapsed = time.time() - t0
                    progress = scanned_bytes / total_bytes * 100 if total_bytes else 100
                    print(
                        f"  [{progress:.1f}%] {len(key_map)}/{len(salt_to_dbs)} salts matched, "
                        f"{all_hex_matches} candidates, {elapsed:.1f}s"
                    )

            if remaining_salts:
                all_hex_matches += _scan_pointer_targets_for_pid(
                    h, pid, diagnostics, hex_re, db_files, salt_to_dbs,
                    key_map, remaining_salts, pointer_scan_seen,
                )
        finally:
            kernel32.CloseHandle(h)

        if not remaining_salts:
            print(f"\n[+] 所有密钥已找到，跳过剩余进程")
            break

    elapsed = time.time() - t0
    print(f"\n扫描完成: {elapsed:.1f}s, {len(pids)} 个进程, {all_hex_matches} 个候选模式")
    print("\n诊断统计:")
    print(f"  legacy x'...' 候选: {diagnostics.get('legacy_hex_candidates', 0)}")
    print(f"  裸 64-hex 候选: {diagnostics.get('plain_hex_candidates', 0)}")
    print(f"  UTF-16LE 64-hex 候选: {diagnostics.get('utf16_hex_candidates', 0)}")
    print(f"  raw salt 附近 32-byte 候选: {diagnostics.get('raw_near_salt_candidates', 0)}")
    print(f"  指针目标 raw 32-byte 候选: {diagnostics.get('raw_anywhere_candidates', 0)}")
    print(f"  指针目标读取成功: {diagnostics.get('pointer_target_read_ok', 0)}")
    print(f"  指针目标读取失败: {diagnostics.get('pointer_target_read_fail', 0)}")
    print(f"  raw salt 命中: {diagnostics.get('raw_salt_hits', 0)}")
    print(f"    其中页对齐 raw salt: {diagnostics.get('raw_salt_page_aligned_hits', 0)}")
    print(f"    其中非页对齐 raw salt: {diagnostics.get('raw_salt_non_page_aligned_hits', 0)}")
    print(f"  ASCII salt 命中: {diagnostics.get('ascii_salt_hits', 0)}")
    print(f"  UTF-16LE salt 命中: {diagnostics.get('utf16_salt_hits', 0)}")
    print(f"  验证通过候选: {diagnostics.get('verified_candidates', 0)}")
    if diagnostics.get("raw_salt_hits", 0) and not diagnostics.get("verified_candidates", 0):
        if diagnostics.get("raw_salt_non_page_aligned_hits", 0):
            print("  提示: 存在非页对齐 raw salt，可继续分析 salt 周边结构体。")
        else:
            print("  提示: raw salt 多数来自页对齐数据库页缓存，key 可能不与 salt 同结构缓存。")
    _write_salt_context_report(diagnostics, db_dir)

    cross_verify_keys(db_files, salt_to_dbs, key_map, print)
    save_results(db_files, salt_to_dbs, key_map, db_dir, out_file, print)


def _write_salt_context_report(diagnostics, db_dir):
    samples = diagnostics.get("salt_context_samples", [])
    if not samples:
        return
    marker_counts = {}
    for sample in samples:
        for marker in sample.get("markers", []):
            marker_counts[marker] = marker_counts.get(marker, 0) + 1
    report = {
        "db_dir": db_dir,
        "sample_count": len(samples),
        "marker_counts": marker_counts,
        "samples": samples,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "salt_context_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"  salt 上下文诊断报告: {out_path}")
    if marker_counts:
        print(f"  salt 上下文 marker 统计: {marker_counts}")


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)
