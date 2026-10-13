import json
import pytest
from PIL import Image

from novus_wechat.excel_export import build_xlsx
from scripts.verify_excel import verify


def test_full_excel_preserves_messages_images_and_formula_text(tmp_path):
    image = tmp_path / "synthetic.png"
    Image.new("RGB", (24, 24), "blue").save(image)
    messages = [{"id": str(i), "timestamp": 1700000000 + i, "sender": "演示用户",
                 "kind": "text", "content": "=1+1" if i == 0 else "合成消息"}
                for i in range(510)]
    messages.append({"id": "image", "timestamp": 1700000600, "sender": "演示用户",
                     "kind": "image", "content": "[图片]"})
    workbook = tmp_path / "export.xlsx"
    workbook.write_bytes(build_xlsx({"name": "演示群"}, messages, lambda _: image))
    source = tmp_path / "expected.json"
    source.write_text(json.dumps({"messages": messages}), encoding="utf-8")
    report = verify(workbook, source)
    assert report["messages"] == 511
    assert len(report["worksheets"]) == 9
    assert report["images_embedded"] == 1


def test_excel_rejects_duplicate_message_ids():
    message = {"id": "same", "timestamp": 1700000000, "kind": "text", "content": "测试"}
    with pytest.raises(ValueError, match="重复"):
        build_xlsx({"name": "演示群"}, [message, message], lambda _: None)
