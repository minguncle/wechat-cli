"""Verify an exported workbook without Excel or third-party packages."""
import argparse
import json
import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "d": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"}
REQUIRED = ["概览", "近期重点", "消息明细", "图片目录", "按日统计", "按月统计", "发言人统计", "类型统计", "链接与文件"]


def verify(path, expected_json=None):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None, "损坏的 ZIP 内容"
        def xml(name):
            return ET.fromstring(archive.read(name))
        relationships = {r.attrib["Id"]: r.attrib["Target"] for r in xml("xl/_rels/workbook.xml.rels")}
        sheets = {}
        for sheet in xml("xl/workbook.xml").findall("s:sheets/s:sheet", NS):
            target = relationships[sheet.attrib["{" + NS["r"] + "}id"]]
            sheets[sheet.attrib["name"]] = "xl/" + target
        assert all(name in sheets for name in REQUIRED), "缺少基础工作表"
        strings = ["".join(t.text or "" for t in e.findall(".//s:t", NS)) for e in xml("xl/sharedStrings.xml")]
        def rows(name):
            return [r for r in xml(sheets[name]).findall("s:sheetData/s:row", NS) if int(r.attrib["r"]) >= 6]
        def value(cell):
            v = cell.find("s:v", NS)
            if v is None:
                return ""
            return strings[int(v.text)] if cell.attrib.get("t") == "s" else v.text
        def table_values(name):
            result = []
            for row in rows(name):
                data = {refless(c.attrib["r"]): value(c) for c in row}
                # Empty tables have a textual no-record notice rather than a numeric sequence.
                if data.get("A", "").isdigit():
                    result.append(data)
            return result
        detail = table_values("消息明细")
        gallery = table_values("图片目录")
        ids = [r["H"] for r in detail]
        assert len(ids) == len(set(ids)), "重复消息ID"
        successes = sum(r.get("F", "") == "缓存图片 · 已嵌入" for r in gallery)
        def picture_count(name):
            sheet = sheets[name]
            rel = posixpath.join(posixpath.dirname(sheet), "_rels", posixpath.basename(sheet) + ".rels")
            if rel not in archive.namelist():
                return 0
            count = 0
            for r in xml(rel):
                if r.attrib["Type"].endswith("/drawing"):
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(sheet), r.attrib["Target"]))
                    count += len(xml(target).findall(".//d:pic", NS))
            return count
        assert picture_count("消息明细") == successes, "消息明细图片数量不符"
        assert picture_count("图片目录") == successes, "图片目录图片数量不符"
        if expected_json:
            source = json.loads(Path(expected_json).read_text(encoding="utf-8-sig"))
            expected = source.get("messages", source.get("items", []))
            assert set(ids) == {m["id"] for m in expected} and len(ids) == len(expected), "与同范围 JSON 消息不一致"
        for name, sheet in sheets.items():
            assert not xml(sheet).findall(".//s:f", NS), "出现意外公式"
            for link in xml(sheet).findall("s:hyperlinks/s:hyperlink", NS):
                location = link.attrib.get("location", "")
                if location.startswith("'消息明细'!A") and name != "概览":
                    assert 6 <= int(location.split("!A")[1]) <= len(detail) + 5, "原消息链接越界"
        return {"file": str(Path(path).resolve()), "worksheets": list(sheets), "messages": len(detail),
                "image_messages": len(gallery), "images_embedded": successes, "images_unavailable": len(gallery) - successes,
                "embedded_media_files": len([n for n in archive.namelist() if n.startswith("xl/media/")]),
                "status": "PASS"}


def refless(reference):
    return reference.rstrip("0123456789")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--expect-json", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.workbook, args.expect_json), ensure_ascii=False, indent=2))
