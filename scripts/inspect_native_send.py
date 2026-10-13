"""Compare wxhook's six known entries against local PE files, without loading them.

Research only: matching instruction prefixes do not establish callable functions.
Requires pefile and capstone; neither is added to the application runtime.
"""
import argparse
import hashlib
import json
import re
import struct
from difflib import SequenceMatcher
from pathlib import Path

import capstone
import pefile


OFFSETS = {
    "GetCoroCtx": 0x42010,
    "GetMsgSvc": 0x334780,
    "GetMsgCtx": 0x6C7A80,
    "DoSend": 0x1788AF0,
    "SendEntry": 0x17419E0,
    "MsgCtor": 0x72E830,
}


def load(path):
    pe = pefile.PE(str(path), fast_load=True)
    if pe.FILE_HEADER.Machine != 0x8664:
        raise ValueError("Only Windows x64 PE files are supported")
    directory = pe.OPTIONAL_HEADER.DATA_DIRECTORY[3]
    data = pe.get_data(directory.VirtualAddress, directory.Size)
    ranges = [(a, b) for a, b, _ in struct.iter_unpack("<III", data)]
    return pe, ranges


def containing(ranges, rva):
    return [{"start": hex(a), "end": hex(b)} for a, b in ranges if a <= rva < b]


def signature(pe, address):
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    decoder.detail = True
    pattern = []
    instructions = []
    for ins in decoder.disasm(pe.get_data(address, 160), address):
        masked = set()
        # Relative addresses, constants and displacement values may move between builds.
        for offset, size in ((ins.imm_offset, ins.imm_size), (ins.disp_offset, ins.disp_size)):
            masked.update(range(offset, offset + size))
        pattern.extend(b"." if i in masked else re.escape(bytes([v])) for i, v in enumerate(ins.bytes))
        instructions.append(f"{ins.mnemonic} {ins.op_str}")
        if len(instructions) >= 16 or ins.mnemonic == "ret":
            break
    if len(instructions) < 4:
        raise ValueError(f"Insufficient instructions at {address:#x}")
    return re.compile(b"".join(pattern), re.DOTALL), instructions


def function_instructions(pe, ranges, address):
    end = next((b for a, b in ranges if a == address), None)
    if end is None:
        return []  # Leaf functions may have no exception-table record.
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    return list(decoder.disasm(pe.get_data(address, end - address), address))


def compare_functions(reference, old_ranges, old_address, current, ranges, address):
    old = function_instructions(reference, old_ranges, old_address)
    new = function_instructions(current, ranges, address)
    if not old or not new:
        return None
    def calls(instructions):
        return [int(i.op_str, 16) for i in instructions if i.mnemonic == "call" and i.op_str.startswith("0x")]
    old_calls, new_calls = calls(old), calls(new)
    # Positional pairing is evidence for review, not a semantic mapping.
    pairs = []
    labels = {value: name for name, value in OFFSETS.items()}
    for before, after in zip(old_calls, new_calls):
        if before in labels:
            pairs.append({"reference_label": labels[before], "current_target": hex(after)})
    return {
        "reference_instruction_count": len(old),
        "current_instruction_count": len(new),
        "mnemonic_similarity": round(SequenceMatcher(None, [i.mnemonic for i in old], [i.mnemonic for i in new], autojunk=False).ratio(), 6),
        "direct_call_count": [len(old_calls), len(new_calls)],
        "positional_call_pairs": pairs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("current", type=Path)
    parser.add_argument("--reference", type=Path, help="Verified 4.1.11.52 Weixin.dll")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    current, ranges = load(args.current)
    reference, old_ranges = load(args.reference) if args.reference else (None, [])
    result = {
        "scope": "offline candidate discovery only; no process access or execution",
        "current_sha256": hashlib.file_digest(args.current.open("rb"), "sha256").hexdigest(),
        "reference_sha256": hashlib.file_digest(args.reference.open("rb"), "sha256").hexdigest() if args.reference else None,
        "entries": [],
        "native_send_verified": False,
    }
    sections = [(s.VirtualAddress, s.get_data()) for s in current.sections if s.Characteristics & 0x20000000]
    for name, address in OFFSETS.items():
        entry = {"name": name, "old_rva": hex(address), "current_containing_ranges": containing(ranges, address)}
        if reference:
            pattern, instructions = signature(reference, address)
            candidates = [base + m.start() for base, data in sections for m in pattern.finditer(data)]
            entry.update(reference_instructions=instructions, candidates=[{
                "rva": hex(a), "ranges": containing(ranges, a),
                "function_comparison": compare_functions(reference, old_ranges, address, current, ranges, a),
            } for a in candidates])
        result["entries"].append(entry)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "entries": len(result["entries"]), "native_send_verified": False}))


if __name__ == "__main__":
    main()
