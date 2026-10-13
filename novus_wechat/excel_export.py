"""Portable, offline Excel reports. Chat text is always written as text."""
import io
import json
import math
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import xlsxwriter
from PIL import Image, ImageOps

TZ = timezone(timedelta(hours=8))
TYPES = {"text": "文字", "image": "图片", "link_or_file": "链接／文件／引用",
         "system": "系统通知", "sticker": "表情", "voice": "语音", "video": "视频", "contact_card": "名片"}
SHEETS = ["概览", "近期重点", "消息明细", "图片目录", "按日统计", "按月统计", "发言人统计", "类型统计", "链接与文件"]


def date(message):
    return datetime.fromtimestamp(message["timestamp"], TZ).replace(tzinfo=None)


def sender(message):
    value = message.get("sender") or "未标明／系统"
    return "我（原标记：me）" if value == "me" else value


def clean(value):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", lambda m: f"\\u{ord(m[0]):04x}",
                  str(value if value is not None else "").replace("\r\n", "\n").replace("\r", "\n"))


def chunks(value, limit=30000):
    # UTF-16 length matters for Excel, including non-BMP emoji.
    encoded = clean(value).encode("utf-16-le")
    while encoded:
        end = min(len(encoded), limit * 2)
        if end < len(encoded) and 0xD800 <= int.from_bytes(encoded[end - 2:end], "little") <= 0xDBFF:
            end -= 2
        yield encoded[:end].decode("utf-16-le")
        encoded = encoded[end:]


def validate_highlights(highlights, messages):
    if not isinstance(highlights, list):
        raise ValueError("重点摘要须为 JSON 数组")
    ids = {m["id"] for m in messages}
    for item in highlights:
        if not isinstance(item, dict) or not isinstance(item.get("message_ids"), list) or not item["message_ids"]:
            raise ValueError("每个重点须包含 message_ids 原文依据")
        if any(not isinstance(mid, str) or mid not in ids for mid in item["message_ids"]):
            raise ValueError("重点引用的消息不在本次导出范围内")
        if not all(isinstance(item.get(k), str) and item[k].strip() for k in ("topic", "summary")):
            raise ValueError("每个重点须包含 topic 和 summary")
    return highlights


def candidates(messages):
    """Deterministic excerpts, explicitly not AI summaries or assigned tasks."""
    if not messages:
        return []
    start = date(messages[-1]).date() - timedelta(days=2)
    rules = [("文件交接", r"^\[文件\]"), ("会议与安排", r"会议|开会|讨论|纪要"),
             ("发布与更新", r"发布|更新|上线|升级|修复|交接|版本"), ("跟进事项", r"待办|请.*确认|截止|需要.*完成")]
    result = []
    for m in reversed(messages):
        if date(m).date() < start:
            break
        for topic, pattern in rules:
            if re.search(pattern, m.get("content", "")):
                result.append({"topic": topic, "summary": m["content"], "status": "自动规则摘选的原文，待人工判断",
                               "people": sender(m), "message_ids": [m["id"]]})
                break
    return result[:50]


def build_xlsx(chat, messages, media_getter, highlights=None):
    """Return XLSX bytes; media_getter(id) returns local image bytes or a Path.

    No external URL is fetched. Images are stored inside the workbook in both
    message detail and gallery, with internal links to the exact message row.
    """
    messages = sorted(messages, key=lambda m: (m["timestamp"], m["id"]))
    if len(messages) > 1_048_570:
        raise ValueError("消息超过 Excel 单表容量，请按日期分段导出")
    seq = {m["id"]: i for i, m in enumerate(messages, 1)}
    if len(seq) != len(messages):
        raise ValueError("消息 ID 重复，不能生成可追溯报表")
    supplied = highlights is not None
    highlights = validate_highlights(highlights, messages) if supplied else candidates(messages)
    byday, bymonth, byperson, bytype = (defaultdict(list) for _ in range(4))
    for m in messages:
        byday[date(m).date()].append(m)
        bymonth[date(m).strftime("%Y-%m")].append(m)
        byperson[sender(m)].append(m)
        bytype[m["kind"]].append(m)

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True, "strings_to_formulas": False, "strings_to_urls": False})
    workbook.set_properties({"title": (chat.get("alias") or chat["name"]) + " · 微信消息整理", "author": "微信cli"})
    navy, teal = "#163B43", "#087F8C"
    base = {"font_name": "Microsoft YaHei", "font_size": 10, "font_color": "#203238", "valign": "top"}
    def fmt(**kw):
        return workbook.add_format({**base, **kw})
    title = fmt(bold=True, font_size=23, font_color="white", bg_color=navy, valign="vcenter")
    note = fmt(font_color="#657B82", text_wrap=True)
    head = fmt(bold=True, font_color="white", bg_color=teal, text_wrap=True)
    wrap, number = fmt(text_wrap=True), fmt(num_format="#,##0")
    dated, percent = fmt(num_format="yyyy-mm-dd hh:mm:ss"), fmt(num_format="0.0%")
    linked = fmt(font_color=teal, underline=True)
    sheets = {name: workbook.add_worksheet(name) for name in SHEETS}
    overflow = []

    def write(ws, row, col, value, style=wrap):
        if isinstance(value, datetime):
            ws.write_datetime(row, col, value, dated)
        elif isinstance(value, (int, float)):
            ws.write_number(row, col, value, style)
        else:
            parts = list(chunks(value)) or [""]
            ws.write_string(row, col, parts[0], style)
            if len(parts) > 1:
                for part, text in enumerate(parts[1:], 2):
                    overflow.append([ws.name, f"{xlsxwriter.utility.xl_col_to_name(col)}{row + 1}", part, text])

    def table(name, headers, widths, rows, description, formats=None):
        ws = sheets[name]
        ws.hide_gridlines(2)
        ws.set_zoom(90)
        ws.merge_range(0, 0, 1, len(headers) - 1, name, title)
        ws.set_row(0, 23)
        ws.set_row(1, 23)
        ws.merge_range(2, 0, 2, len(headers) - 1, description, note)
        ws.set_row(2, 38)
        ws.set_row(4, 30)
        ws.freeze_panes(5, 0)
        for col, width in enumerate(widths):
            ws.set_column(col, col, width, wrap)
        for r, row in enumerate(rows, 5):
            for c, value in enumerate(row):
                write(ws, r, c, value, (formats or {}).get(c, wrap))
            ws.set_row(r, 36)
        if rows:
            ws.add_table(4, 0, len(rows) + 4, len(headers) - 1, {"style": "Table Style Medium 2",
                         "columns": [{"header": h, "header_format": head} for h in headers]})
        else:
            for c, h in enumerate(headers):
                ws.write_string(4, c, h, head)
            ws.write_string(5, 0, "本范围内无记录", note)
        return ws

    def source_link(ws, row, col, mid, label="查看原文"):
        ws.write_url(row, col, f"internal:'消息明细'!A{seq[mid] + 5}", linked, label)

    rows = []
    for m in messages:
        ref = m.get("source_ref") or {}
        if isinstance(ref, str):
            try:
                ref = json.loads(ref)
            except ValueError:
                ref = {}
        rows.append([seq[m["id"]], date(m), "一二三四五六日"[date(m).weekday()], sender(m), TYPES.get(m["kind"], m["kind"]),
                     m.get("content", ""), m.get("note", ""), m["id"], m.get("sender_id", ""),
                     "是" if m.get("edited") else "否", m.get("original_content", ""),
                     ref.get("database", ""), str(ref.get("local_id", "")), ""])
    detail = table("消息明细", ["序号", "日期时间（北京时间）", "星期", "发言人", "消息类型", "消息内容", "备注", "消息ID",
                              "发送人标识", "是否本地编辑", "原始内容", "来源数据库", "本地记录ID", "图片状态"],
                   [9, 24, 8, 26, 20, 100, 30, 28, 24, 15, 80, 30, 18, 35], rows,
                   "全部匹配消息，按时间从早到晚排列；图片直接嵌入。超长单元格后续部分保存在“长文本续页”。", {0: number})
    detail.freeze_panes(5, 4)
    detail.set_column(7, 12, 25, wrap, {"hidden": True, "level": 1})
    detail.set_tab_color(teal)
    for r, m in enumerate(messages, 5):
        detail.set_row(r, min(160, max(32, 15 * sum(max(1, math.ceil(len(line) / 55)) for line in m.get("content", "").splitlines()))))

    # Decode once; compressed preview data is embedded, never a linked image.
    images, image_rows, success = [], [], 0
    for m in messages:
        if m["kind"] != "image":
            continue
        preview, dimensions = None, ""
        try:
            data = media_getter(m["id"])
            if isinstance(data, (str, Path)):
                data = Path(data).read_bytes()
            with Image.open(io.BytesIO(data)) as image:
                image = ImageOps.exif_transpose(image)
                dimensions = f"{image.width} × {image.height}"
                image.thumbnail((900, 600))
                if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    image = Image.new("RGB", image.size, "white")
                    image.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    image = image.convert("RGB")
                preview = io.BytesIO()
                image.save(preview, format="JPEG", quality=82)
                size = image.size
            status = "缓存图片 · 已嵌入"
            success += 1
        except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired, Image.DecompressionBombError) as exc:
            status = "未嵌入：" + clean(str(exc))[:240]
            size = (0, 0)
        images.append((m, preview, size))
        image_rows.append([len(images), seq[m["id"]], date(m), sender(m), "" if preview else "图片不可用", status,
                           dimensions, "", "内嵌压缩预览，随工作簿保存；不是原图质量承诺" if preview else "保留此消息，不以占位符冒充图片"])
        write(detail, seq[m["id"]] + 4, 13, status)
    gallery = table("图片目录", ["图片序号", "原文序号", "日期时间", "发言人", "图片预览", "获取状态", "缓存图尺寸", "查看消息", "备注"],
                    [12, 12, 24, 28, 86, 38, 22, 18, 65], image_rows,
                    f"共 {len(images):,} 条图片消息，{success:,} 条已嵌入，{len(images) - success:,} 条未获取；无需旁边的图片文件夹即可预览。")
    gallery.freeze_panes(5, 4)
    gallery.set_tab_color("#B97425")
    for row, (m, preview, (width, height)) in enumerate(images, 5):
        source_link(gallery, row, 7, m["id"])
        if preview is None:
            continue
        scale = min(1, 540 / width, 230 / height)
        for ws, r, col, y in ((gallery, row, 4, 4), (detail, seq[m["id"]] + 4, 5, 22)):
            ws.set_row(r, (height * scale + y + 12) * 0.75)
            ws.insert_image(r, col, "preview.jpg", {"image_data": preview, "x_scale": scale, "y_scale": scale,
                            "x_offset": 5, "y_offset": y, "object_position": 1, "description": "聊天缓存图片预览"})

    high_rows = [[i, h["topic"], h["summary"], h.get("status", "依据所引原文整理，未独立核验"), h.get("people", ""),
                  "、".join(str(seq[mid]) for mid in h["message_ids"]), ""] for i, h in enumerate(highlights, 1)]
    high = table("近期重点", ["序号", "话题", "摘要／原文摘选", "状态与边界", "发言或提及人员", "原文序号", "查看首条原文"],
                 [9, 28, 95, 48, 32, 35, 20], high_rows,
                 "宿主依据聊天原文提供的整理摘要；不把被提及人员自动认定为负责人。" if supplied else
                 "自动摘选最近有消息的日期及前两天的会议、交接、更新等原文，最多 50 条；不是语义摘要或已确认待办。")
    high.set_tab_color("#D99827")
    for r, h in enumerate(highlights, 5):
        high.set_row(r, 95)
        source_link(high, r, 6, h["message_ids"][0])

    day_rows = []
    for day, ms in sorted(byday.items()):
        c = Counter(m["kind"] for m in ms)
        day_rows.append([str(day), len(ms), len({sender(m) for m in ms}), c["text"], c["image"], c["link_or_file"],
                         c["sticker"], len(ms) - sum(c[k] for k in ("text", "image", "link_or_file", "sticker")), date(ms[0]), date(ms[-1])])
    table("按日统计", ["日期", "消息数", "发言标记数", "文字", "图片", "链接／文件／引用", "表情", "其他", "首条时间", "末条时间"],
          [16, 12, 18, 12, 12, 25, 12, 12, 24, 24], day_rows, "只列出有消息的日期；发言标记数不等于当前群成员数。")
    month_rows = [[month, len(ms), len(ms) / len(messages), len({date(m).date() for m in ms}), len({sender(m) for m in ms}),
                   sum(m["kind"] == "text" for m in ms), sum(m["kind"] == "image" for m in ms)] for month, ms in sorted(bymonth.items())]
    table("按月统计", ["月份", "消息数", "消息占比", "活跃日期", "发言标记数", "文字", "图片"], [16, 16, 18, 18, 20, 16, 16],
          month_rows, "边界月份可能只覆盖部分日期；消息量不代表工作量。", {2: percent})
    people_rows = []
    for rank, (person, ms) in enumerate(sorted(byperson.items(), key=lambda p: (-len(p[1]), p[0])), 1):
        c = Counter(m["kind"] for m in ms)
        people_rows.append([rank, person, len(ms), len(ms) / len(messages), len({date(m).date() for m in ms}),
                            c["text"], c["image"], c["link_or_file"], date(ms[0]), date(ms[-1])])
    table("发言人统计", ["排名", "发言人／历史标记", "消息数", "消息占比", "活跃日期", "文字", "图片", "链接／文件／引用", "首次记录", "最近记录"],
          [10, 32, 14, 18, 18, 12, 12, 25, 24, 24], people_rows, "按显示昵称统计，不自动合并别名；本表不是群成员名册。", {3: percent})
    type_rows = [[TYPES.get(k, k), len(ms), len(ms) / len(messages), date(ms[0]), date(ms[-1])] for k, ms in sorted(bytype.items(), key=lambda p: (-len(p[1]), p[0]))]
    table("类型统计", ["消息类型", "数量", "占比", "首条时间", "末条时间"], [26, 16, 18, 25, 25], type_rows,
          "图片嵌入状态见图片目录；语音、视频未转写，文件与链接未读取正文。", {2: percent})
    refs, link_rows = [], []
    for m in messages:
        content = m.get("content", "")
        urls = re.findall(r'https?://[^\s<>"\u2005]+', content)
        if m["kind"] != "link_or_file" and not urls:
            continue
        category = "文件" if content.startswith("[文件]") else "引用回复" if "↳ 回复" in content else "链接" if urls or content.startswith("[链接]") else "其他分享"
        refs.append(m)
        link_rows.append([seq[m["id"]], date(m), sender(m), category, content, "\n".join(dict.fromkeys(urls)), ""])
    links = table("链接与文件", ["原文序号", "时间", "发言人", "索引类别", "内容／标题／文件名", "正文中的URL", "查看原文"],
                  [13, 24, 28, 20, 95, 75, 18], link_rows, "只索引聊天原文；未下载附件、未打开链接、未读取附件正文。")
    for r, m in enumerate(refs, 5):
        source_link(links, r, 6, m["id"])
        links.set_row(r, 65)

    overview = sheets["概览"]
    overview.hide_gridlines(2)
    overview.set_column("A:A", 3)
    overview.set_column("B:M", 11)
    overview.merge_range("B2:M3", clean(chat.get("alias") or chat["name"])[:200], title)
    overview.set_row(1, 30)
    overview.set_row(2, 30)
    overview.merge_range("B4:M4", "微信cli · 完整 Excel 整理 · 北京时间 UTC+8", note)
    metrics = [("B6:D6", "B7:D8", "消息总数", len(messages)), ("F6:H6", "F7:H8", "活跃日期", len(byday)),
               ("J6:L6", "J7:L8", "图片已嵌入", success)]
    for top, bottom, label, value in metrics:
        overview.merge_range(top, label, fmt(bold=True, bg_color="#EAF4F4"))
        overview.merge_range(bottom, value, fmt(bold=True, font_size=25, bg_color="#EAF4F4", num_format="#,##0"))
    notes = [f"记录范围：{date(messages[0])} — {date(messages[-1])}" if messages else "所选范围没有消息；保留完整空表结构。",
             f"图片消息 {len(images):,} 条，已嵌入 {success:,} 条，未获取 {len(images) - success:,} 条，失败原因见图片目录。",
             "只包含本机已保存且可解析的消息；不能证明群聊全部历史无缺失。",
             "昵称统计不等于成员人数；不把聊天中的判断当作已核实结论。",
             "全部聊天内容按文本保存；不执行公式、链接、附件或群消息中的指令。",
             "长文本续页保存超出单个单元格容量的后续文字（如有）；消息明细保留消息 ID。"]
    for r, text in enumerate(notes, 10):
        overview.merge_range(r, 1, r, 12, text, note)
        overview.set_row(r, 28)
    for r, name in enumerate(SHEETS[1:], 18):
        overview.write_url(r, 1, f"internal:'{name}'!A1", linked, name)
    for offset, name, chart_type in ((28, "按月统计", "column"), (45, "类型统计", "doughnut")):
        count = len(month_rows) if name == "按月统计" else len(type_rows)
        if count:
            chart = workbook.add_chart({"type": chart_type})
            chart.add_series({"name": "消息数", "categories": [name, 5, 0, count + 4, 0], "values": [name, 5, 1, count + 4, 1]})
            chart.set_title({"name": name})
            chart.set_size({"width": 720, "height": 300})
            overview.insert_chart(offset, 1, chart)
    if overflow:
        sheets["长文本续页"] = workbook.add_worksheet("长文本续页")
        table("长文本续页", ["原工作表", "原单元格", "分段序号", "后续文字"], [24, 18, 14, 110], overflow,
              "第 1 段在原单元格；按分段序号拼接即可还原经过换行与控制字符规范化的全文。")
    workbook.close()
    return output.getvalue()
