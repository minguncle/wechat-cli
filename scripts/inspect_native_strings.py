"""Find x64 RIP-relative references to diagnostic strings without loading a DLL.

Research output only. References and callers do not establish a callable API.
"""
import argparse
import bisect
import json
import re
import struct

from inspect_native_send import load, function_instructions


def inspect(path, literals):
    pe, ranges = load(path)
    starts = [a for a, _ in ranges]
    def parent(address):
        index = bisect.bisect_right(starts, address) - 1
        return ranges[index][0] if index >= 0 and address < ranges[index][1] else None
    # Decode only candidate functions to reject raw byte matches inside instructions.
    decoded = {}
    def instruction_at(address):
        begin = parent(address)
        if begin is None:
            return None
        if begin not in decoded:
            decoded[begin] = {i.address: i for i in function_instructions(pe, ranges, begin)}
        return decoded[begin].get(address)
    targets = {}
    raw = pe.__data__
    for literal in literals:
        for match in re.finditer(re.escape(literal.encode() + b"\0"), raw):
            targets[pe.get_rva_from_offset(match.start())] = literal
    sections = [(s.VirtualAddress, s.get_data()) for s in pe.sections if s.Characteristics & 0x20000000]
    references = []
    for base, data in sections:
        for match in re.finditer(rb"[\x48-\x4f][\x8d\x8b][\x05\x0d\x15\x1d\x25\x2d\x35\x3d]", data):
            offset = match.start()
            if offset + 7 > len(data):
                continue
            address = base + offset
            target = address + 7 + struct.unpack_from("<i", data, offset + 3)[0]
            if target in targets:
                instruction = instruction_at(address)
                if instruction and instruction.size == 7 and instruction.mnemonic in ("lea", "mov"):
                    references.append({"literal": targets[target], "instruction": hex(address), "function": hex(parent(address))})
    functions = {int(row["function"], 16) for row in references}
    callers = []
    for base, data in sections:
        for match in re.finditer(b"\xe8", data):
            offset = match.start()
            if offset + 5 > len(data):
                continue
            address = base + offset
            target = address + 5 + struct.unpack_from("<i", data, offset + 1)[0]
            if target in functions:
                instruction = instruction_at(address)
                if instruction and instruction.mnemonic == "call" and instruction.op_str == hex(target):
                    callers.append({"instruction": hex(address), "target": hex(target), "function": hex(parent(address))})
    return {"scope": "offline static references; no process access", "references": references, "direct_callers": callers, "native_send_verified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dll")
    parser.add_argument("--literal", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = inspect(args.dll, args.literal)
    with open(args.output, "w", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(json.dumps(result, ensure_ascii=False))
