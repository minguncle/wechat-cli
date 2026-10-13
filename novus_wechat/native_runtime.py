"""Discover the exact Windows client build used by the native adapter.

Discovery does not attach instrumentation, call client functions or operate UI.
Process creation time must be checked again before using a discovered PID.
"""
import hashlib
from pathlib import Path
import re
import struct
import sys


WINDOWS_IMAGE_BUILD = '8f7406a8a465e851ee10eaecccb572d0e5b4e00d480c770938c714c396097b74'


def verified_entries(path, rvas):
    """Read entry bytes only from the exact audited PE, without extra libraries."""
    with Path(path).open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != WINDOWS_IMAGE_BUILD:
            raise ValueError('Unrecognized client build')
        source.seek(0x3c)
        pe_offset = struct.unpack('<I',source.read(4))[0]
        source.seek(pe_offset)
        if source.read(4) != b'PE\0\0':
            raise ValueError('Invalid PE signature')
        header = source.read(20)
        count = struct.unpack_from('<H',header,2)[0]
        optional_size = struct.unpack_from('<H',header,16)[0]
        source.seek(pe_offset + 24 + optional_size)
        sections = [struct.unpack('<8sIIII16x',source.read(40)) for _ in range(count)]
        result = []
        for rva in rvas:
            matches = [raw + rva - virtual for _,size,virtual,raw_size,raw in sections
                       if virtual <= rva and rva + 32 <= virtual + min(size,raw_size)]
            if len(matches) != 1:
                raise ValueError('Entry is not contained in one PE section')
            source.seek(matches[0])
            result.append({'rva':rva,'prefix':source.read(32).hex()})
        return result


def discover():
    if sys.platform != 'win32':
        raise ValueError('当前原生运行时发现仅适配 Windows')
    import psutil
    candidates = []
    inaccessible = []
    for process in psutil.process_iter(['pid', 'name', 'cmdline']):
        if (process.info['name'] or '').lower() != 'weixin.exe':
            continue
        if any(arg.startswith('--type=') for arg in process.info['cmdline'] or []):
            continue
        try:
            created = process.create_time()
            modules = {m.path for m in process.memory_maps() if Path(m.path).name.lower() == 'weixin.dll'}
            if len(modules) != 1:
                continue
            dll = Path(modules.pop()).resolve()
            with dll.open('rb') as source:
                digest = hashlib.file_digest(source, 'sha256').hexdigest()
            if not process.is_running() or process.create_time() != created:
                continue
            candidates.append({'pid':process.pid, 'created':created, 'dll':str(dll),
                               'sha256':digest, 'supported':digest == WINDOWS_IMAGE_BUILD})
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError):
            inaccessible.append(process.pid)
    if inaccessible:
        raise ValueError('无法检查所有微信主进程，不能确认发送实例')
    if len(candidates) != 1:
        raise ValueError('需要恰好一个已加载微信模块的主进程')
    result = candidates[0]
    if not result['supported']:
        raise ValueError('当前微信构建未适配原生发送')
    return result


def verify_identity(runtime):
    """Reject PID reuse and client exit; this does not establish account identity."""
    import psutil
    try:
        process = psutil.Process(runtime['pid'])
        if not process.is_running() or process.create_time() != runtime['created']:
            raise ValueError('微信进程已经变化，请重新发现运行时')
    except psutil.NoSuchProcess as exc:
        raise ValueError('微信进程已经退出，请重新发现运行时') from exc


def verify_resource_account(source_dir, resource, resolved_path):
    """Match the client's resolved cache to the configured account, without writes."""
    source = Path(source_dir).resolve(strict=True)
    if not source.is_dir() or source.name.lower() != 'db_storage':
        raise ValueError('需要配置实际微信 db_storage 目录')
    if not resource or '\0' in resource:
        raise ValueError('资源标识不能为空或包含 NUL')
    cache = (source.parent / 'cache').resolve(strict=True)
    resolved = Path(resolved_path).resolve()
    try:
        relative = resolved.relative_to(cache)
    except ValueError as exc:
        raise ValueError('当前微信账号与配置的数据目录不一致') from exc
    parts = relative.parts
    if (len(parts) != 3 or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', parts[0])
            or parts[1] != 'HttpResource'
            or parts[2] != hashlib.md5(resource.encode('utf-8')).hexdigest()):
        raise ValueError('微信返回的资源路径结构或标识不匹配')
    return resolved
