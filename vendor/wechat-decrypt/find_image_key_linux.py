#!/usr/bin/env python3
"""Linux 版图片 AES 密钥提取（参考 find_image_key.py 的 Windows 版改写）

原理:
  1. 从 V2 .dat 文件提取 AES 密文块 (16 字节)
  2. 读 /proc/pid/mem 搜索微信进程内存中的 16/32 字符字母数字串
  3. 用候选字符串尝试 AES-ECB 解密, 解密出 JPEG/PNG 头即为正确 key

用法:
  sudo python3 find_image_key_linux.py
"""
import os
import re
import sys
import json
import glob
import struct
import ctypes.util

# pycryptodome
from Crypto.Cipher import AES
from Crypto.Util import Padding

V2_MAGIC = b'\x07\x08V2\x08\x07'
RE_KEY32 = re.compile(rb'(?<![a-zA-Z0-9])[a-zA-Z0-9]{32}(?![a-zA-Z0-9])')
RE_KEY16 = re.compile(rb'(?<![a-zA-Z0-9])[a-zA-Z0-9]{16}(?![a-zA-Z0-9])')


def find_wechat_pid():
    """找微信进程 PID"""
    import subprocess
    result = subprocess.run(
        ['pgrep', '-f', '/opt/wechat/wechat'],
        capture_output=True, text=True
    )
    pids = [int(p) for p in result.stdout.strip().split('\n') if p.strip()]
    if not pids:
        # 也试 wechat 进程名
        result = subprocess.run(['pgrep', '-x', 'wechat'], capture_output=True, text=True)
        pids = [int(p) for p in result.stdout.strip().split('\n') if p.strip()]
    return pids


def find_v2_ciphertext(attach_dir):
    """从 V2 .dat 文件提取第一个 AES 密文块 (16 bytes)"""
    pattern = os.path.join(attach_dir, "*", "*", "Img", "*_t.dat")
    dat_files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not dat_files:
        # 也试其他可能的路径模式
        pattern = os.path.join(attach_dir, "**", "*.dat")
        dat_files = sorted(glob.glob(pattern, recursive=True), key=os.path.getmtime, reverse=True)

    for f in dat_files[:100]:
        try:
            with open(f, 'rb') as fp:
                header = fp.read(31)
            if header[:6] == V2_MAGIC and len(header) >= 31:
                return header[15:31], os.path.basename(f)
        except:
            continue
    return None, None


def find_xor_key(attach_dir):
    """从缩略图末尾推导 XOR key (JPEG 结尾 FF D9)"""
    pattern = os.path.join(attach_dir, "*", "*", "Img", "*_t.dat")
    dat_files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not dat_files:
        pattern = os.path.join(attach_dir, "**", "*_t.dat")
        dat_files = sorted(glob.glob(pattern, recursive=True), key=os.path.getmtime, reverse=True)

    tail_counts = {}
    for f in dat_files[:32]:
        try:
            sz = os.path.getsize(f)
            with open(f, 'rb') as fp:
                head = fp.read(6)
                fp.seek(sz - 2)
                tail = fp.read(2)
            if head == V2_MAGIC and len(tail) == 2:
                key = (tail[0], tail[1])
                tail_counts[key] = tail_counts.get(key, 0) + 1
        except:
            continue

    if not tail_counts:
        return None
    most_common = max(tail_counts, key=tail_counts.get)
    return most_common[0] ^ 0xFF


def try_key(key_bytes, ciphertext):
    """尝试解密, 返回格式名或 None"""
    try:
        cipher = AES.new(key_bytes[:16], AES.MODE_ECB)
        dec = cipher.decrypt(ciphertext)
        if dec[:3] == b'\xFF\xD8\xFF':
            return 'JPEG'
        if dec[:4] == bytes([0x89, 0x50, 0x4E, 0x47]):
            return 'PNG'
        if dec[:4] == b'RIFF':
            return 'WEBP'
        if dec[:4] == b'wxgf':
            return 'WXGF'
        if dec[:3] == b'GIF':
            return 'GIF'
    except:
        pass
    return None


def scan_proc_mem(pid, ciphertext):
    """扫描 /proc/pid/mem 寻找 AES key"""
    maps_path = f"/proc/{pid}/maps"
    mem_path = f"/proc/{pid}/mem"

    try:
        with open(maps_path, 'r') as f:
            maps = f.readlines()
    except PermissionError:
        print(f"  权限不足, 请用 sudo 运行")
        return None

    regions = []
    for line in maps:
        parts = line.split()
        addr_range = parts[0]
        perms = parts[1] if len(parts) > 1 else ''
        if 'r' not in perms:
            continue
        start_s, end_s = addr_range.split('-')
        start = int(start_s, 16)
        end = int(end_s, 16)
        size = end - start
        if size > 100 * 1024 * 1024:  # 跳过超大区域
            continue
        # 只扫描可写区域 (key 字符串最可能在这里)
        is_rw = 'w' in perms
        regions.append((start, size, is_rw))

    rw_regions = [r for r in regions if r[2]]
    other_regions = [r for r in regions if not r[2]]
    print(f"  RW 区域: {len(rw_regions)}, 只读区域: {len(other_regions)}")

    try:
        mem_fd = os.open(mem_path, os.O_RDONLY)
    except PermissionError:
        print(f"  无法读取 /proc/{pid}/mem, 请用 sudo 运行")
        return None

    import time
    t0 = time.time()
    candidates_32 = 0
    candidates_16 = 0

    # Phase 1: RW 区域
    print("  === Phase 1: 扫描 RW 内存 ===")
    for phase_regions in [rw_regions, other_regions]:
        for idx, (start, size, _rw) in enumerate(phase_regions):
            if idx % 200 == 0 and idx > 0:
                print(f"  扫描 {idx}/{len(phase_regions)} ({time.time()-t0:.1f}s)", end='\r')

            try:
                os.lseek(mem_fd, start, os.SEEK_SET)
                data = os.read(mem_fd, size)
            except:
                continue

            if len(data) < 32:
                continue

            # 搜索 32 字符字母数字串
            for m in RE_KEY32.finditer(data):
                key_bytes = m.group()
                candidates_32 += 1
                fmt = try_key(key_bytes[:16], ciphertext)
                if fmt:
                    key_str = key_bytes.decode('ascii')
                    print(f"\n*** 找到 AES key (32-char)! -> {fmt} ***")
                    print(f"  完整: {key_str}")
                    print(f"  AES key: {key_str[:16]}")
                    os.close(mem_fd)
                    return key_str[:16]

            # 搜索 16 字符字母数字串
            for m in RE_KEY16.finditer(data):
                key_bytes = m.group()
                candidates_16 += 1
                fmt = try_key(key_bytes, ciphertext)
                if fmt:
                    key_str = key_bytes.decode('ascii')
                    print(f"\n*** 找到 AES key (16-char)! -> {fmt} ***")
                    print(f"  AES key: {key_str}")
                    os.close(mem_fd)
                    return key_str

        if phase_regions == rw_regions:
            print(f"\n  Phase 1 完成: {candidates_32} x 32char + {candidates_16} x 16char")
            print("  === Phase 2: 扫描只读内存 ===")
        else:
            print(f"\n  Phase 2 完成: {candidates_32} x 32char + {candidates_16} x 16char")

    os.close(mem_fd)
    print(f"  总耗时: {time.time()-t0:.1f}s")
    return None


def verify_decrypt(attach_dir, aes_key_str, xor_key):
    """验证解密一个 V2 文件"""
    key = aes_key_str.encode('ascii')[:16]
    pattern = os.path.join(attach_dir, "*", "*", "Img", "*_t.dat")
    dat_files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not dat_files:
        pattern = os.path.join(attach_dir, "**", "*.dat")
        dat_files = sorted(glob.glob(pattern, recursive=True), key=os.path.getmtime, reverse=True)

    for f in dat_files[:10]:
        try:
            with open(f, 'rb') as fp:
                data = fp.read()
            if data[:6] != V2_MAGIC:
                continue
            aes_size, xor_size = struct.unpack_from('<LL', data, 6)
            aligned = aes_size - ~(~aes_size % 16)
            offset = 15
            cipher = AES.new(key, AES.MODE_ECB)
            dec_aes = Padding.unpad(cipher.decrypt(data[offset:offset+aligned]), AES.block_size)
            offset += aligned
            raw_data = data[offset:len(data) - xor_size]
            offset += len(raw_data)
            xor_data = data[offset:]
            dec_xor = bytes(b ^ xor_key for b in xor_data) if xor_key is not None else xor_data
            result = dec_aes + raw_data + dec_xor

            fmt, ext = "unknown", ".bin"
            if result[:3] == b'\xFF\xD8\xFF':
                fmt, ext = "JPEG", ".jpg"
            elif result[:4] == bytes([0x89, 0x50, 0x4E, 0x47]):
                fmt, ext = "PNG", ".png"
            elif result[:4] == b'RIFF':
                fmt, ext = "WEBP", ".webp"

            if fmt != "unknown":
                out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decoded_images")
                os.makedirs(out_dir, exist_ok=True)
                out_path = os.path.join(out_dir, os.path.splitext(os.path.basename(f))[0] + ext)
                with open(out_path, 'wb') as fp:
                    fp.write(result)
                print(f"  {os.path.basename(f)} -> {fmt} ({len(result):,}B)")
                print(f"  saved: {out_path}")
                return True
        except:
            continue
    return False


def main():
    # 加载配置
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
    except:
        print("无法读取 config.json, 请先运行 find_all_keys_linux.py")
        return

    db_dir = os.path.expanduser(os.path.expandvars(config.get('db_dir', '')))
    base_dir = os.path.dirname(db_dir)
    attach_dir = os.path.join(base_dir, 'msg', 'attach')

    print(f"attach_dir: {attach_dir}")

    # 1. XOR key
    print("\n=== XOR Key ===")
    xor_key = find_xor_key(attach_dir)
    if xor_key is not None:
        print(f"XOR key: 0x{xor_key:02x}")

    # 2. V2 密文
    print("\n=== V2 ciphertext ===")
    ciphertext, ct_file = find_v2_ciphertext(attach_dir)
    if ciphertext is None:
        print("未找到 V2 .dat 文件")
        print("提示: 在微信里打开聊天, 点开 2-3 张图片, 然后立即重新运行此脚本")
        return
    print(f"File: {ct_file}")
    print(f"Cipher: {ciphertext.hex()}")

    # 3. 检查已有 key
    existing_key = config.get('image_aes_key')
    if existing_key:
        print(f"\n已有 image_aes_key: {existing_key}")
        fmt = try_key(existing_key.encode('ascii')[:16], ciphertext)
        if fmt:
            print(f"Key 有效! -> {fmt}")
            print("\n=== 验证解密 ===")
            verify_decrypt(attach_dir, existing_key, xor_key)
            return
        else:
            print("已保存的 key 无效, 重新扫描...")

    # 4. 扫描内存
    print("\n=== 扫描微信进程内存 ===")
    pids = find_wechat_pid()
    if not pids:
        print("微信未运行! 请先启动微信并登录")
        return
    print(f"PIDs: {pids}")
    print("提示: 先在微信里点开 2-3 张图片, 然后立即运行此脚本\n")

    aes_key = None
    for pid in pids:
        print(f"扫描 PID {pid}...")
        aes_key = scan_proc_mem(pid, ciphertext)
        if aes_key:
            break

    if aes_key:
        print(f"\n=== 结果 ===")
        print(f"AES key: {aes_key}")
        if xor_key is not None:
            print(f"XOR key: 0x{xor_key:02x}")

        config['image_aes_key'] = aes_key
        if xor_key is not None:
            config['image_xor_key'] = xor_key
        with open(config_path, 'w', encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"已保存到 {config_path}")

        print("\n=== 验证解密 ===")
        verify_decrypt(attach_dir, aes_key, xor_key)
    else:
        print("\n未找到 AES key!")
        print("步骤:")
        print("  1. 启动微信并登录")
        print("  2. 打开聊天或朋友圈, 点开 2-3 张图片 (点开大图)")
        print("  3. 立即重新运行: sudo python3 find_image_key_linux.py")


if __name__ == '__main__':
    main()
