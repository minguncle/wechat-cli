"""Browser acceptance using synthetic records; never reads a user's WeChat account."""
import json
import time
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:18766"


def request(path, data):
    req = urllib.request.Request(BASE + "/api/" + path, data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json", "X-Novus-WeChat": "1"})
    with urllib.request.urlopen(req) as response:
        return json.load(response)


now = int(time.time()) - 3600
for name, messages in {
    "演示 · 产品设计组": [("陈一", "新版首页的设计稿已经整理好，大家可以查看。"),
                        ("林溪", "我会在明天下午三点前确认文案。"),
                        ("我", "好的，我负责整理移动端的修改建议。"),
                        ("周宁", "请保留上一版的导出文件，周五评审时一起比较。")],
    "演示 · 项目协作群": [("叶青", "本周的项目进度已经更新。"), ("我", "收到，下午统一确认。")],
    "演示 · 林溪": [("林溪", "明天的会提前到十点，可以吗？"), ("我", "可以，我已经调整日程。")],
}.items():
    request("import", {"chat": {"id": name, "name": name, "kind": "group" if name != "演示 · 林溪" else "private"},
        "messages": [{"id": str(i), "timestamp": now + i * 120, "sender": sender, "content": text} for i, (sender, text) in enumerate(messages)]})

with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 980}, device_scale_factor=1)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(BASE)
    page.get_by_role("button", name="＋", exact=True).click()
    page.locator("#new-name").fill("浏览器验收会话")
    page.locator("#create-chat").click()
    page.locator("#compose-text").fill("验收：新增本地记录")
    page.locator("#add-record").click()
    page.get_by_text("验收：新增本地记录", exact=True).wait_for()
    page.get_by_role("button", name="编辑 / 备注").click()
    page.locator("#edit-content").fill("验收：编辑后的记录")
    page.locator("#edit-note").fill("浏览器测试备注")
    page.locator("#save-message").click()
    page.get_by_text("验收：编辑后的记录", exact=True).wait_for()
    page.get_by_role("button", name="删除副本", exact=True).click()
    page.get_by_text("这个范围内没有消息").wait_for()
    page.get_by_role("button", name="已删除", exact=True).click()
    page.get_by_role("button", name="恢复", exact=True).click()
    page.get_by_text("验收：编辑后的记录", exact=True).wait_for()
    page.get_by_role("button", name="全部", exact=True).click()
    page.locator("#export").click()
    with page.expect_download() as download:
        page.get_by_role("button", name="JSON", exact=True).click()
    target = ROOT / ".build/browser-export.json"
    download.value.save_as(target)
    assert json.loads(target.read_text(encoding="utf-8"))["messages"][0]["note"] == "浏览器测试备注"
    page.locator("#modal-close").click()
    page.locator(".chat").filter(has_text="演示 · 产品设计组").click()
    page.get_by_text("请保留上一版的导出文件，周五评审时一起比较。", exact=True).wait_for()
    page.screenshot(path=str(ROOT / ".build/web-console.png"), full_page=True)
    page.locator("#settings").click()
    page.locator("#source-dir").wait_for()
    page.locator("#modal-close").click()
    assert not errors, errors
    print("BROWSER_PASS: create, read, edit, delete, restore, export, settings; no JS errors")
    browser.close()
