"""
跨平台共享的内存扫描逻辑：HMAC 验证、DB 收集、hex 模式匹配与结果输出。

Windows / Linux 版分别实现进程发现和内存读取，共用此模块的核心算法。
"""
import hashlib
import hmac as hmac_mod
import json
import math
import os
import re
import struct
import time

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
PLAIN_HEX_RE = re.compile(rb"(?<![0-9a-fA-F])([0-9a-fA-F]{64})(?![0-9a-fA-F])")
UTF16LE_HEX64_RE = re.compile(rb"(?:[0-9a-fA-F]\x00){64}")


def verify_enc_key(enc_key, db_page1):
    """通过 HMAC-SHA512 校验 page 1 验证 enc_key 是否正确。"""
    salt = db_page1[:SALT_SZ]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)
    hmac_data = db_page1[SALT_SZ: PAGE_SZ - 80 + 16]
    stored_hmac = db_page1[PAGE_SZ - 64: PAGE_SZ]
    hm = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    return hm.digest() == stored_hmac


def derive_enc_key_from_passphrase(passphrase, db_page1):
    """WeChat 4.1+/WCDB: 从缓存 passphrase 和数据库 salt 派生 SQLCipher raw key。"""
    salt = db_page1[:SALT_SZ]
    return hashlib.pbkdf2_hmac("sha512", passphrase, salt, 256000, dklen=KEY_SZ)


def verify_passphrase(passphrase, db_page1):
    enc_key = derive_enc_key_from_passphrase(passphrase, db_page1)
    return verify_enc_key(enc_key, db_page1)


def collect_db_files(db_dir):
    """遍历 db_dir 收集所有 .db 文件及其 salt。

    返回 (db_files, salt_to_dbs):
      db_files: [(rel_path, abs_path, size, salt_hex, page1_bytes), ...]
      salt_to_dbs: {salt_hex: [rel_path, ...]}
    """
    db_files = []
    salt_to_dbs = {}
    for root, dirs, files in os.walk(db_dir):
        for name in files:
            if not name.endswith(".db") or name.endswith("-wal") or name.endswith("-shm"):
                continue
            path = os.path.join(root, name)
            size = os.path.getsize(path)
            if size < PAGE_SZ:
                continue
            with open(path, "rb") as f:
                page1 = f.read(PAGE_SZ)
            rel = os.path.relpath(path, db_dir)
            salt = page1[:SALT_SZ].hex()
            db_files.append((rel, path, size, salt, page1))
            salt_to_dbs.setdefault(salt, []).append(rel)
    return db_files, salt_to_dbs


def scan_memory_for_keys(data, hex_re, db_files, salt_to_dbs, key_map,
                         remaining_salts, base_addr, pid, print_fn,
                         diagnostics=None):
    """扫描一段内存数据，匹配 hex 模式并验证密钥。

    返回本次扫描匹配到的 hex 模式数量。
    """
    matches = 0
    _update_salt_diagnostics(data, remaining_salts, diagnostics, base_addr, pid, salt_to_dbs)

    for m in hex_re.finditer(data):
        hex_str = m.group(1).decode()
        addr = base_addr + m.start()
        matches += 1
        _diag_inc(diagnostics, "legacy_hex_candidates")
        hex_len = len(hex_str)

        if hex_len == 96:
            enc_key_hex = hex_str[:64]
            salt_hex = hex_str[64:]
            if salt_hex in remaining_salts:
                _try_key_for_salt(
                    enc_key_hex, salt_hex, db_files, salt_to_dbs,
                    key_map, remaining_salts, pid, addr, print_fn,
                    "legacy x'key+salt'", diagnostics,
                )

        elif hex_len == 64:
            if not remaining_salts:
                continue
            enc_key_hex = hex_str
            _try_key_for_any_salt(
                enc_key_hex, db_files, salt_to_dbs, key_map,
                remaining_salts, pid, addr, print_fn,
                "legacy x'key'", diagnostics,
            )

        elif hex_len > 96 and hex_len % 2 == 0:
            enc_key_hex = hex_str[:64]
            salt_hex = hex_str[-32:]
            if salt_hex in remaining_salts:
                _try_key_for_salt(
                    enc_key_hex, salt_hex, db_files, salt_to_dbs,
                    key_map, remaining_salts, pid, addr, print_fn,
                    f"legacy long hex {hex_len}", diagnostics,
                )

    matches += _scan_plain_hex_candidates(
        data, db_files, salt_to_dbs, key_map, remaining_salts,
        base_addr, pid, print_fn, diagnostics,
    )
    matches += _scan_utf16le_hex_candidates(
        data, db_files, salt_to_dbs, key_map, remaining_salts,
        base_addr, pid, print_fn, diagnostics,
    )
    matches += _scan_raw_keys_near_salts(
        data, db_files, salt_to_dbs, key_map, remaining_salts,
        base_addr, pid, print_fn, diagnostics,
    )
    return matches


def _diag_inc(diagnostics, key, amount=1):
    if diagnostics is not None:
        diagnostics[key] = diagnostics.get(key, 0) + amount


def _update_salt_diagnostics(data, remaining_salts, diagnostics, base_addr, pid, salt_to_dbs):
    if diagnostics is None:
        return
    for salt_hex in remaining_salts:
        raw_salt = bytes.fromhex(salt_hex)
        ascii_salt = salt_hex.encode("ascii")
        utf16_salt = salt_hex.encode("utf-16le")
        raw_positions = _find_all_bytes(data, raw_salt)
        diagnostics["raw_salt_hits"] = diagnostics.get("raw_salt_hits", 0) + len(raw_positions)
        for pos in raw_positions:
            if (base_addr + pos) % PAGE_SZ == 0:
                _diag_inc(diagnostics, "raw_salt_page_aligned_hits")
            else:
                _diag_inc(diagnostics, "raw_salt_non_page_aligned_hits")
            _record_salt_context_sample(data, pos, base_addr, pid, salt_hex, salt_to_dbs, diagnostics)
        diagnostics["ascii_salt_hits"] = diagnostics.get("ascii_salt_hits", 0) + _count_bytes(data, ascii_salt)
        diagnostics["utf16_salt_hits"] = diagnostics.get("utf16_salt_hits", 0) + _count_bytes(data, utf16_salt)


def _count_bytes(data, needle):
    return len(_find_all_bytes(data, needle))


def _find_all_bytes(data, needle):
    positions = []
    start = 0
    while True:
        pos = data.find(needle, start)
        if pos < 0:
            return positions
        positions.append(pos)
        start = pos + 1


def _record_salt_context_sample(data, pos, base_addr, pid, salt_hex, salt_to_dbs, diagnostics):
    samples = diagnostics.setdefault("salt_context_samples", [])
    limit = int(diagnostics.get("salt_context_sample_limit", 40))
    if len(samples) >= limit:
        return

    left = max(0, pos - 256)
    right = min(len(data), pos + SALT_SZ + 256)
    context = data[left:right]
    address = base_addr + pos
    markers = _context_markers(context)
    samples.append({
        "pid": int(pid),
        "address": hex(address),
        "salt": salt_hex,
        "dbs": list(salt_to_dbs.get(salt_hex, [])),
        "page_offset": int(address % PAGE_SZ),
        "context_size": len(context),
        "context_sha256": hashlib.sha256(context).hexdigest(),
        "entropy": round(_entropy(context), 3),
        "printable_ratio": round(_printable_ratio(context), 3),
        "pointer_like_qwords": _count_pointer_like_qwords(context),
        "markers": markers,
    })
    _record_pointer_scan_targets(context, left, base_addr, pid, address, salt_hex, salt_to_dbs, diagnostics)


def _record_pointer_scan_targets(context, context_start, base_addr, pid, source_address,
                                 salt_hex, salt_to_dbs, diagnostics):
    limit = int(diagnostics.get("pointer_scan_target_limit", 2000))
    targets = diagnostics.setdefault("pointer_scan_targets", [])
    seen = diagnostics.setdefault("_pointer_scan_target_seen", set())
    if len(targets) >= limit:
        return

    context_base = base_addr + context_start
    for off in range(0, max(0, len(context) - 7)):
        slot_addr = context_base + off
        if slot_addr % 8 != 0:
            continue
        value = struct.unpack_from("<Q", context, off)[0]
        if not _is_pointer_like(value):
            continue
        key = (int(pid), int(value), salt_hex)
        if key in seen:
            continue
        seen.add(key)
        targets.append({
            "pid": int(pid),
            "salt": salt_hex,
            "dbs": list(salt_to_dbs.get(salt_hex, [])),
            "source_address": hex(source_address),
            "slot_address": hex(slot_addr),
            "target": hex(value),
        })
        if len(targets) >= limit:
            return


def _context_markers(context):
    markers = []
    lower = context.lower()
    checks = {
        "SQLite": b"sqlite",
        "WCDB": b"wcdb",
        "sqlcipher": b"sqlcipher",
        "cipher": b"cipher",
        "key": b"key",
        "salt": b"salt",
        "message": b"message",
        ".db": b".db",
    }
    for label, needle in checks.items():
        if needle in lower:
            markers.append(label)
    return markers


def _entropy(data):
    if not data:
        return 0.0
    counts = {}
    for b in data:
        counts[b] = counts.get(b, 0) + 1
    total = len(data)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def _printable_ratio(data):
    if not data:
        return 0.0
    printable = sum(1 for b in data if b in (9, 10, 13) or 32 <= b <= 126)
    return printable / len(data)


def _count_pointer_like_qwords(data):
    count = 0
    for off in range(0, max(0, len(data) - 7)):
        value = struct.unpack_from("<Q", data, off)[0]
        if _is_pointer_like(value):
            count += 1
    return count


def _is_pointer_like(value):
    return 0x10000 <= value <= 0x00007FFFFFFFFFFF and value % 8 == 0


def _try_key_for_salt(enc_key_hex, salt_hex, db_files, salt_to_dbs, key_map,
                      remaining_salts, pid, addr, print_fn, source, diagnostics):
    if salt_hex not in remaining_salts:
        return False
    try:
        enc_key = bytes.fromhex(enc_key_hex)
    except ValueError:
        return False
    for rel, path, sz, s, page1 in db_files:
        if s == salt_hex and verify_enc_key(enc_key, page1):
            key_map[salt_hex] = enc_key_hex.lower()
            remaining_salts.discard(salt_hex)
            dbs = salt_to_dbs[salt_hex]
            _diag_inc(diagnostics, "verified_candidates")
            print_fn(f"\n  [FOUND] salt={salt_hex} ({source})")
            print_fn(f"    key_material: <redacted:{len(enc_key)} bytes>")
            print_fn(f"    PID={pid} 地址: 0x{addr:016X}")
            print_fn(f"    数据库: {', '.join(dbs)}")
            return True
    return False


def _try_key_for_any_salt(enc_key_hex, db_files, salt_to_dbs, key_map,
                          remaining_salts, pid, addr, print_fn, source,
                          diagnostics):
    try:
        enc_key = bytes.fromhex(enc_key_hex)
    except ValueError:
        return False
    for rel, path, sz, salt_hex_db, page1 in db_files:
        if salt_hex_db in remaining_salts and verify_enc_key(enc_key, page1):
            key_map[salt_hex_db] = enc_key_hex.lower()
            remaining_salts.discard(salt_hex_db)
            dbs = salt_to_dbs[salt_hex_db]
            _diag_inc(diagnostics, "verified_candidates")
            print_fn(f"\n  [FOUND] salt={salt_hex_db} ({source})")
            print_fn(f"    key_material: <redacted:{len(enc_key)} bytes>")
            print_fn(f"    PID={pid} 地址: 0x{addr:016X}")
            print_fn(f"    数据库: {', '.join(dbs)}")
            return True
    return False


def _scan_plain_hex_candidates(data, db_files, salt_to_dbs, key_map,
                               remaining_salts, base_addr, pid, print_fn,
                               diagnostics):
    matches = 0
    for m in PLAIN_HEX_RE.finditer(data):
        if not remaining_salts:
            break
        enc_key_hex = m.group(1).decode("ascii")
        matches += 1
        _diag_inc(diagnostics, "plain_hex_candidates")
        _try_key_for_any_salt(
            enc_key_hex, db_files, salt_to_dbs, key_map, remaining_salts,
            pid, base_addr + m.start(1), print_fn, "plain 64-hex",
            diagnostics,
        )
    return matches


def _scan_utf16le_hex_candidates(data, db_files, salt_to_dbs, key_map,
                                 remaining_salts, base_addr, pid, print_fn,
                                 diagnostics):
    matches = 0
    for m in UTF16LE_HEX64_RE.finditer(data):
        if not remaining_salts:
            break
        raw = m.group(0)
        enc_key_hex = raw[::2].decode("ascii")
        matches += 1
        _diag_inc(diagnostics, "utf16_hex_candidates")
        _try_key_for_any_salt(
            enc_key_hex, db_files, salt_to_dbs, key_map, remaining_salts,
            pid, base_addr + m.start(), print_fn, "UTF-16LE 64-hex",
            diagnostics,
        )
    return matches


def _scan_raw_keys_near_salts(data, db_files, salt_to_dbs, key_map,
                              remaining_salts, base_addr, pid, print_fn,
                              diagnostics, window=4096):
    matches = 0
    for salt_hex in list(remaining_salts):
        raw_salt = bytes.fromhex(salt_hex)
        start = 0
        while True:
            pos = data.find(raw_salt, start)
            if pos < 0:
                break
            left = max(0, pos - window)
            right = min(len(data), pos + SALT_SZ + window)
            for off in range(left, max(left, right - KEY_SZ + 1)):
                if salt_hex not in remaining_salts:
                    break
                candidate = data[off:off + KEY_SZ]
                if len(candidate) != KEY_SZ:
                    continue
                if candidate == b"\x00" * KEY_SZ:
                    continue
                matches += 1
                _diag_inc(diagnostics, "raw_near_salt_candidates")
                _try_key_for_salt(
                    candidate.hex(), salt_hex, db_files, salt_to_dbs,
                    key_map, remaining_salts, pid, base_addr + off,
                    print_fn, "raw 32-byte near salt", diagnostics,
                )
            start = pos + 1
    return matches


def scan_raw_key_candidates_for_any_salt(data, db_files, salt_to_dbs, key_map,
                                         remaining_salts, base_addr, pid,
                                         print_fn, source, diagnostics=None,
                                         stride=8):
    """扫描无 salt 伴随的 raw 32-byte key 候选。

    这个函数主要给 Windows 二级指针扫描使用：当 salt 结构体里保存的是
    key buffer 指针时，目标区域可能只有 raw key，没有 salt 或 hex 字符串。
    """
    matches = 0
    if not remaining_salts:
        return matches
    stride = max(1, int(stride))
    for off in range(0, max(0, len(data) - KEY_SZ + 1), stride):
        if not remaining_salts:
            break
        candidate = data[off:off + KEY_SZ]
        if len(candidate) != KEY_SZ or candidate == b"\x00" * KEY_SZ:
            continue
        matches += 1
        _diag_inc(diagnostics, "raw_anywhere_candidates")
        _try_key_for_any_salt(
            candidate.hex(), db_files, salt_to_dbs, key_map, remaining_salts,
            pid, base_addr + off, print_fn, source, diagnostics,
        )
    return matches


def cross_verify_keys(db_files, salt_to_dbs, key_map, print_fn):
    """用已找到的 key 交叉验证未匹配的 salt。"""
    missing_salts = set(salt_to_dbs.keys()) - set(key_map.keys())
    if not missing_salts or not key_map:
        return
    print_fn(f"\n还有 {len(missing_salts)} 个 salt 未匹配，尝试交叉验证...")
    for salt_hex in list(missing_salts):
        for rel, path, sz, s, page1 in db_files:
            if s == salt_hex:
                for known_salt, known_key_hex in key_map.items():
                    enc_key = bytes.fromhex(known_key_hex)
                    if verify_enc_key(enc_key, page1):
                        key_map[salt_hex] = known_key_hex
                        print_fn(f"  [CROSS] salt={salt_hex} 可用 key from salt={known_salt}")
                        missing_salts.discard(salt_hex)
                break


def save_results(db_files, salt_to_dbs, key_map, db_dir, out_file, print_fn):
    """输出扫描结果并保存 JSON。"""
    effective_key_map = dict(key_map)
    existing = {}
    if os.path.exists(out_file):
        try:
            with open(out_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except (OSError, json.JSONDecodeError):
            existing = {}

    for rel, path, sz, salt_hex, page1 in db_files:
        if salt_hex in effective_key_map:
            continue
        item = existing.get(rel)
        if not isinstance(item, dict):
            continue
        enc_key_hex = item.get("enc_key")
        if not isinstance(enc_key_hex, str):
            continue
        try:
            enc_key = bytes.fromhex(enc_key_hex)
        except ValueError:
            continue
        if len(enc_key) == KEY_SZ and verify_enc_key(enc_key, page1):
            effective_key_map[salt_hex] = enc_key_hex.lower()

    print_fn(f"\n{'=' * 60}")
    print_fn(f"结果: {len(effective_key_map)}/{len(salt_to_dbs)} salts 找到密钥")

    result = {}
    for rel, path, sz, salt_hex, page1 in db_files:
        if salt_hex in effective_key_map:
            result[rel] = {
                "enc_key": effective_key_map[salt_hex],
                "salt": salt_hex,
                "size_mb": round(sz / 1024 / 1024, 1)
            }
            print_fn(f"  OK: {rel} ({sz / 1024 / 1024:.1f}MB)")
        else:
            print_fn(f"  MISSING: {rel} (salt={salt_hex})")

    if not result:
        print_fn(f"\n[!] 未提取到任何密钥，保留已有的 {out_file}（如存在）")
        raise RuntimeError("未能从任何微信进程中提取到密钥")

    result["_db_dir"] = db_dir
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print_fn(f"\n密钥保存到: {out_file}")

    missing = [rel for rel, path, sz, salt_hex, page1 in db_files if salt_hex not in key_map]
    if missing:
        print_fn(f"\n未找到密钥的数据库:")
        for rel in missing:
            print_fn(f"  {rel}")
