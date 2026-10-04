#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
award-tracker 独立运行入口

用法：
    award-tracker.exe                    # 双击运行：启动检查 + 交互模式
    award-tracker.exe --text "通知内容"   # 解析一段通知文本
    award-tracker.exe --file note.txt    # 解析一个文本文件
    award-tracker.exe --config           # 交互式修改学院/年级/专业
    award-tracker.exe --show             # 查看当前配置
    award-tracker.exe --history          # 查看历史记录
    award-tracker.exe --monthly 2026 10  # 查看某月日程
    award-tracker.exe --json --text "..."# 以 JSON 输出结果（供程序调用）

配置文件 config.json 生成在「可执行文件所在目录」（不可写时退回 ~/.award-tracker/）。
历史记录 history.jsonl 追加写入 ~/.award-tracker/，重装 exe 也不丢。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# 默认配置（打包分享前，把 grade / major 改成你自己的信息）
# 首次运行时会以此为模板，在 exe 同目录生成 config.json
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "name": "",
    "college": "经济管理学院",
    "grade": "大二",
    "major": "知识产权",
    "reminder_days": 3,
    "config_version": 2,
}

CONFIG_NAME = "config.json"

GRADE_ORDER = {
    "大一": 1, "大二": 2, "大三": 3, "大四": 4, "大五": 5,
    "研究生": 6, "硕士": 6, "博士": 7,
}

# 同类专业归组：先判断所属大类，大类一致才算匹配
MAJOR_GROUPS = [
    ("影视传媒类", ["摄影", "摄像", "影视", "传媒", "新闻", "传播", "广告", "编导",
                    "广播电视", "戏文", "戏剧", "动画", "数字媒体", "视觉传达"]),
    ("艺术设计类", ["设计", "艺术", "美术", "视觉传达"]),
    ("文法政法类", ["中文", "文学", "外语", "英语", "法学", "法律", "知识产权", "知产", "教育", "心理"]),
    ("理工科类", ["计算机", "软件", "人工智能", "理工", "工科", "理科",
                  "机械", "土木", "建筑", "电子", "自动化"]),
    ("经管类", ["经管", "经济", "管理", "金融", "会计", "工商"]),
]

MAJOR_KEYWORDS = [k for _, kws in MAJOR_GROUPS for k in list(dict.fromkeys(kws))]

FUZZY_TIME_WORDS = ["近期", "尽快", "另行通知", "额满即止", "招满即止", "择期", "待定"]

# ---------------------------------------------------------------------------
# 配置读写
# ---------------------------------------------------------------------------


def base_dir() -> str:
    """配置文件存放目录：打包后取 exe 所在目录，源码运行时取技能根目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_path() -> str:
    return os.path.join(base_dir(), CONFIG_NAME)


def ensure_config() -> tuple[dict, bool]:
    """读取配置；不存在或损坏时按默认值重建。返回 (config, 是否新建)。"""
    path = config_path()
    created = False
    cfg = None

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError):
            cfg = None

    if not isinstance(cfg, dict):
        cfg = dict(DEFAULT_CONFIG)
        save_config(cfg)
        created = True
    else:
        # 补齐新增字段，保证旧配置升级后不缺键
        missing = {k: v for k, v in DEFAULT_CONFIG.items() if k not in cfg}
        if missing:
            cfg.update(missing)
            save_config(cfg)

    return cfg, created


def save_config(cfg: dict) -> str:
    """写回配置；exe 所在目录不可写时退回用户目录（如装在 Program Files）。"""
    path = config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return path
    except OSError:
        fallback = os.path.join(user_dir(), CONFIG_NAME)
        os.makedirs(user_dir(), exist_ok=True)
        with open(fallback, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return fallback


# ---------------------------------------------------------------------------
# 历史记录 / 月程 / 启动检查
# ---------------------------------------------------------------------------


def user_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".award-tracker")


HISTORY_FILE = os.path.join(user_dir(), "history.jsonl")


def load_history() -> list[dict]:
    if not os.path.exists(HISTORY_FILE):
        return []
    records = []
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def save_record(record: dict) -> bool:
    """追加一条记录；同名且同截止日期视为重复，不重复写。返回是否写入。"""
    history = load_history()
    for old in history:
        if old.get("name") == record.get("name") and old.get("deadline") == record.get("deadline"):
            return False

    # 同一秒内可能写入多条，撞号时加后缀
    used = {old.get("id") for old in history}
    now = datetime.now()
    rid = now.strftime("%Y%m%d%H%M%S")
    n = 1
    while rid in used:
        n += 1
        rid = f"{now.strftime('%Y%m%d%H%M%S')}-{n}"

    rec = dict(record)
    rec["id"] = rid
    rec["added"] = now.strftime("%Y-%m-%d")
    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def clean_text(s: str) -> str:
    """去掉编码错乱产生的孤立代理字符，避免写文件时崩。"""
    try:
        return s.encode("utf-8", "replace").decode("utf-8")
    except Exception:
        return s


def rows_to_records(rows: list[dict]) -> list[dict]:
    """提取结果 → 历史记录结构。deadline 只留日期部分，方便按月分组。"""
    out = []
    for r in rows:
        name = r.get("名称", "未注明")
        if name == "未注明":
            continue
        dl = r.get("报名截止", "")
        known = dl and dl != "未注明"
        rec = {
            "name": name,
            "deadline": dl[:10] if known else "",
            "deadline_time": dl[11:16] if known and len(dl) >= 16 else "",
            "method": r.get("报名方式", ""),
            "link": r.get("_链接", ""),
            "materials": r.get("_材料", ""),
            "major_limit": r.get("专业限制", "不限专业"),
            "grade_limit": r.get("适合年级", "未注明"),
            "fit": r.get("适配", ""),
        }
        rec = {k: (clean_text(v) if isinstance(v, str) else v) for k, v in rec.items()}
        out.append(rec)
    return out


def format_record(r: dict, show_fit: bool = False) -> list[str]:
    """一条记录的详情行，月程和临近提醒共用。"""
    dl = r.get("deadline") or "未注明"
    now = datetime.now()
    lines = []
    if dl != "未注明":
        left = (datetime.strptime(dl, "%Y-%m-%d") - now).days
        if left < 0:
            span, flag = f"已过{-left}天", " [已过期]"
        elif left == 0:
            span, flag = "今天", " [今天截止]"
        elif left <= 3:
            span, flag = f"{left}天后", " [即将截止]"
        else:
            span, flag = f"{left}天后", ""
    else:
        span, flag = "", ""
    tm = r.get("deadline_time") or ""
    lines.append(f"  * {r['name']}{flag}")
    lines.append(f"      截止：{dl} {tm}  ({span})" if span else f"      截止：{dl} {tm}")
    if r.get("method"):
        lines.append(f"      报名：{r['method']}")
    if r.get("link"):
        lines.append(f"      链接：{r['link']}")
    if r.get("materials"):
        lines.append(f"      材料：{r['materials']}")
    if r.get("major_limit"):
        lines.append(f"      专业：{r['major_limit']}")
    if r.get("grade_limit"):
        lines.append(f"      年级：{r['grade_limit']}")
    if show_fit and r.get("fit"):
        lines.append(f"      适配：{r['fit']}")
    return lines


def monthly_view(year: int | None = None, month: int | None = None) -> None:
    now = datetime.now()
    year = year or now.year
    month = month or now.month
    prefix = f"{year}-{month:02d}"
    records = [r for r in load_history() if r.get("deadline", "").startswith(prefix)]
    records.sort(key=lambda r: r["deadline"])

    print(f"\n{year}年{month}月日程")
    print("=" * 50)
    if not records:
        print(f"  {year}年{month}月没有已记录的截止事项")
        return
    for r in records:
        print()
        for line in format_record(r, show_fit=True):
            print(line)
        print("  " + "-" * 46)


def startup_check(cfg: dict, show_monthly: bool = True) -> None:
    """启动时检查：临近截止（带完整报名信息）+ 当月月程。不常驻后台，打开就能看到。"""
    days = int(cfg.get("reminder_days", 3) or 3)
    now = datetime.now()
    upcoming = []
    for r in load_history():
        dl = r.get("deadline", "")
        if not dl:
            continue
        try:
            left = (datetime.strptime(dl, "%Y-%m-%d") - now).days
        except ValueError:
            continue
        if 0 <= left <= days:
            upcoming.append((left, r))

    if upcoming:
        print("\n临近截止提醒")
        print("=" * 50)
        for left, r in sorted(upcoming, key=lambda x: x[0]):
            print()
            for line in format_record(r):
                print(line)
    if show_monthly:
        monthly_view(now.year, now.month)


# ---------------------------------------------------------------------------
# 通知信息提取
# ---------------------------------------------------------------------------


def extract_name(text: str) -> str:
    body = re.sub(r"^\s*\d{1,2}[\.、)）]\s*", "", text.strip())

    m = re.search(r"关于[开展举办组织评选]{0,4}?(.{4,40}?)(?:的)?(?:通知|公告|邀请函)", body)
    if m:
        return m.group(1).strip()

    m = re.search(
        r"([\u4e00-\u9fa5A-Za-z0-9·\-]{2,30}"
        r"(?:大赛|竞赛|比赛|挑战杯|杯赛|评选|奖学金|招募|征集|计划|活动|讲座|论坛|志愿者))",
        body,
    )
    if m:
        name = m.group(1)
        name = re.sub(r"^(举办|开展|组织|参加|关于)", "", name)
        return name[:40]

    for line in body.splitlines():
        line = line.strip().strip("【】[]「」 ")
        line = re.sub(r"^\d{1,2}[\.、)）]\s*", "", line)
        if len(line) >= 4 and not re.match(r"^(各位|同学|通知|大家好)", line):
            return line[:30]
    return "未注明"


def extract_deadline(text: str) -> tuple[str, str]:
    """返回 (截止日期字符串, 备注)。"""
    now = datetime.now()

    m = re.search(r"(20\d{2})[年\-/](\d{1,2})[月\-/](\d{1,2})[日号]?", text)
    if not m:
        m = re.search(r"(\d{1,2})[月\-/](\d{1,2})[日号]", text)
        year_infer = True
    else:
        year_infer = False

    if not m:
        if any(w in text for w in FUZZY_TIME_WORDS):
            return "未注明", "原文仅给出模糊时间（近期/尽快等）"
        return "未注明", ""

    if year_infer:
        year, month, day = now.year, int(m.group(1)), int(m.group(2))
    else:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))

    try:
        dt = datetime(year, month, day)
    except ValueError:
        return "未注明", "原文日期格式无法解析"

    if year_infer and dt < now - timedelta(days=30):
        dt = dt.replace(year=year + 1)

    note = ""
    tm = re.search(r"(\d{1,2})[:：](\d{2})", text) or re.search(r"(\d{1,2})\s*点", text)
    if tm:
        dt = dt.replace(hour=int(tm.group(1)), minute=int(tm.group(2) or 0))
    else:
        dt = dt.replace(hour=23, minute=59)
        note = "原文未注明时刻，按当日 23:59 计"

    return dt.strftime("%Y-%m-%d %H:%M"), note


def extract_link(text: str) -> str:
    m = re.search(r"https?://[^\s，。、）)\]】]+", text)
    return m.group(0) if m else ""


def extract_materials(text: str) -> str:
    """报名材料清单，没写就留空。"""
    for p in (r"材料[需为]?[:：]?\s*([^。；\n]{2,40})",
              r"需(?:提交|准备|提供)\s*([^。；\n]{2,40})",
              r"提交[:：]\s*([^。；\n]{2,40})"):
        m = re.search(p, text)
        if m:
            return m.group(1).strip("，, ")
    return ""


def extract_method(text: str) -> str:
    """报名方式的可执行动作；链接单独走 extract_link。"""
    if extract_link(text):
        return "官网在线报名"
    mail = re.search(r"[\w.\-]+@[\w.\-]+\.\w+", text)
    if mail:
        return "邮件报名：" + mail.group(0)
    for kw in ("扫码", "二维码", "扫描"):
        if kw in text:
            return "扫码报名（二维码见原文）"
    for kw in ("进群", "加群", "群内", "群里"):
        if kw in text:
            return "群内报名"
    if "私聊" in text:
        return "私聊联系人报名"
    for kw in ("官网", "网站", "系统", "平台", "表单", "问卷"):
        if kw in text:
            return f"通过{kw}提交（原文未给具体地址）"
    if re.search(r"(交|报)至|交给|提交给", text):
        m = re.search(r"(?:(?:交|报)至|交给|提交给)([^\s，。]{2,20})", text)
        if m:
            return "线下提交至：" + m.group(1)
    return "未注明"


def extract_grade(text: str) -> str:
    for kw in ("全体年级", "全体学生", "全体在校生", "全校学生", "所有年级", "在校生"):
        if kw in text:
            return "全体年级"
    found = [g for g in GRADE_ORDER if g in text]
    classes = re.findall(r"(20\d{2})级", text)
    if classes:
        now = datetime.now()
        for c in classes:
            n = now.year - int(c) + (1 if now.month >= 9 else 0)
            label = {1: "大一", 2: "大二", 3: "大三", 4: "大四", 5: "大五"}.get(n)
            if label and label not in found:
                found.append(label)
    if "本科生" in text and not found:
        return "本科生（未细分年级）"

    # 「大一到大三」这类区间要展开
    rng = re.search(r"(大一|大二|大三|大四|大五)\s*(?:至|到|-|—|~)\s*(大一|大二|大三|大四|大五)", text)
    if rng:
        lo, hi = GRADE_ORDER[rng.group(1)], GRADE_ORDER[rng.group(2)]
        if lo > hi:
            lo, hi = hi, lo
        found = [g for g, v in GRADE_ORDER.items() if lo <= v <= hi and v <= 5]

    if not found:
        return "未注明"
    if re.search(r"(及以上|以上|起)", text) and len(found) == 1:
        return f"{found[0]}及以上"
    order = sorted(found, key=lambda g: GRADE_ORDER[g])
    return "、".join(order)


MAJOR_LIMIT_PATTERNS = [
    r"仅限[^，。；\n]{0,20}专业",
    r"(?<!不)限[^，。；\n]{0,20}专业",
    r"面向[^，。；\n]{0,20}专业",
    r"要求[^，。；\n]{0,20}专业",
    r"[^，。；\n]{0,10}专业(?:学生|同学)?(?:可报|均可|优先)",
    r"(?<!不)限[^，。；\n]{0,12}(?:类|科|方向)(?:专业|学生|同学)?",
]


def parse_major_limit(text: str) -> str:
    """提取专业限制。通知没写限制就是「不限专业」，不做任何推断。"""
    for kw in ("不限专业", "专业不限", "各专业", "所有专业"):
        if kw in text:
            return "不限专业"
    for p in MAJOR_LIMIT_PATTERNS:
        m = re.search(p, text)
        if m:
            return m.group(0).strip()
    return "不限专业"


def is_junk_block(block: str) -> bool:
    """过滤「同学们，两个事：」这类无信息量的引导句。"""
    if len(block.strip()) < 20:
        return True
    if not re.search(r"截止|截止|报名|参赛|招募|征集|评选|申请|20\d{2}|\d{1,2}月", block):
        return True
    return False


def extract_items(text: str) -> list[dict]:
    """把一段通知拆成若干条记录并提取字段。"""
    parts = re.split(r"\n\s*\n+|(?=\d{1,2}[\.、)）]\s*\S)", text)
    blocks = [b.strip() for b in parts if b.strip() and not is_junk_block(b)]
    if not blocks:
        blocks = [text.strip()]

    items = []
    for block in blocks[:10]:
        deadline, note = extract_deadline(block)
        items.append({
            "名称": extract_name(block),
            "报名截止": deadline,
            "报名方式": extract_method(block),
            "适合年级": extract_grade(block),
            "专业限制": parse_major_limit(block),
            "_链接": extract_link(block),
            "_材料": extract_materials(block),
            "_备注": note,
        })
    return items


# ---------------------------------------------------------------------------
# 用户画像匹配
# ---------------------------------------------------------------------------


def match_grade(cfg_grade: str, item_grade: str) -> str:
    if item_grade in ("未注明", ""):
        return "未知"
    if any(k in item_grade for k in ("全体年级", "不限")):
        return "匹配"
    base = GRADE_ORDER.get(str(cfg_grade).strip())
    if base is None:
        return "未知"
    if "本科生" in item_grade:
        return "匹配" if base <= 4 else "不匹配"
    found = [GRADE_ORDER[g] for g in GRADE_ORDER if g in item_grade]
    if not found:
        return "未知"
    if "及以上" in item_grade:
        return "匹配" if base >= min(found) else "不匹配"
    return "匹配" if base in found else "不匹配"


def major_group(major_text: str) -> set[str]:
    groups = set()
    for name, kws in MAJOR_GROUPS:
        if any(k in major_text for k in kws):
            groups.add(name)
    return groups


def match_major_label(cfg_major: str, limit: str) -> str:
    """专业标签。只标注，不用于过滤。"""
    if not limit or limit == "不限专业":
        return "所有专业可报"
    mj = str(cfg_major).strip()
    if mj and (mj in limit or any(k in limit for k in MAJOR_KEYWORDS if k in mj)):
        return "符合你的专业"
    for name, kws in MAJOR_GROUPS:
        if any(k in limit for k in kws) and any(k in mj for k in kws):
            return "符合你的专业"
    return "专业可能不符，请自行确认"


def judge(items: list[dict], cfg: dict) -> list[dict]:
    """画像只生成标签和排序权重，不删任何条目。"""
    out = []
    for it in items:
        g = match_grade(cfg.get("grade", ""), it["适合年级"])
        m = match_major_label(cfg.get("major", ""), it["专业限制"])
        if m == "专业可能不符，请自行确认":
            verdict = "待确认"
        elif g == "不匹配":
            verdict = "年级不符"
        elif g == "未知":
            verdict = "待确认"
        else:
            verdict = "符合"

        row = dict(it)  # 保留 _链接 / _材料 等下划线字段，打印表格时再挑选列
        row["适配"] = verdict
        row["专业标签"] = m
        if it.get("_备注"):
            row["备注"] = it["_备注"]
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def disp_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - disp_width(s))


def render_table(rows: list[dict], headers: list[str]) -> str:
    widths = {h: disp_width(h) for h in headers}
    for r in rows:
        for h in headers:
            widths[h] = max(widths[h], disp_width(str(r.get(h, ""))))

    def line(cells):
        return "| " + " | ".join(pad(str(c), widths[h]) for c, h in zip(cells, headers)) + " |"

    sep = "|" + "|".join("-" * (widths[h] + 2) for h in headers) + "|"
    return "\n".join([line(headers), sep] + [line([r.get(h, "") for h in headers]) for r in rows])


def print_result(rows: list[dict], cfg: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"config": cfg, "items": rows}, ensure_ascii=False, indent=2))
        return

    headers = ["#", "名称", "报名截止", "报名方式", "适合年级", "专业限制", "适配"]
    table_rows = []
    for i, r in enumerate(rows, 1):
        table_rows.append({
            "#": i,
            "名称": r["名称"],
            "报名截止": r["报名截止"],
            "报名方式": r["报名方式"],
            "适合年级": r["适合年级"],
            "专业限制": r["专业限制"],
            "适配": r["适配"],
        })
    print()
    print(render_table(table_rows, headers))
    print()
    profile = " ".join(x for x in (cfg.get("college", ""), cfg.get("grade", "")) if x)
    print(f"当前画像：{profile} / {cfg.get('major', '')}（画像只排序打标，不过滤）")
    counts = {k: sum(1 for r in rows if r["适配"] == k) for k in ("符合", "待确认", "年级不符")}
    print("适配情况：符合 {} 条 / 待确认 {} 条 / 年级不符 {} 条".format(
        counts["符合"], counts["待确认"], counts["年级不符"]))

    for r in rows:
        if r.get("专业标签") == "专业可能不符，请自行确认":
            print(f"  - {r['名称']}：{r['专业限制']}，需自行确认")

    notes = [r.get("备注") for r in rows if r.get("备注")]
    if notes:
        print("补充说明：")
        for n in dict.fromkeys(notes):
            print("  - " + n)

    hit = [r for r in rows if r["报名截止"] != "未注明"]
    if hit:
        print()
        print("提示：以上有明确截止日期的项目，可让我帮你设置报名提醒。")


# ---------------------------------------------------------------------------
# 剪贴板（Windows，纯标准库）
# ---------------------------------------------------------------------------


def read_clipboard() -> str:
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        ctypes.windll.user32.OpenClipboard(None)
        try:
            handle = ctypes.windll.user32.GetClipboardData(13)  # CF_UNICODETEXT
            if handle:
                ptr = ctypes.windll.kernel32.GlobalLock(handle)
                try:
                    return ctypes.wstring_at(ptr)
                finally:
                    ctypes.windll.kernel32.GlobalUnlock(handle)
        finally:
            ctypes.windll.user32.CloseClipboard()
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------


def ask(label: str, cur: str = "") -> str | None:
    """安全读取一行；输入被中断时返回 None（保持原值）。"""
    try:
        return input(f"{label} [{cur}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def cmd_config(cfg: dict) -> None:
    print("修改配置（直接回车表示保持原值）")
    print("-" * 40)
    for key, label in (("name", "姓名"), ("college", "学院"), ("grade", "年级"), ("major", "专业")):
        val = ask(label, str(cfg.get(key, "")))
        if val:
            cfg[key] = val
    val = ask("截止前提醒天数", str(cfg.get("reminder_days", 3)))
    if val:
        try:
            cfg["reminder_days"] = int(val)
        except ValueError:
            print("天数不是整数，保持原值")
    path = save_config(cfg)
    print()
    print("已保存：")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    print(f"配置文件位置：{path}")


def cmd_show(cfg: dict) -> None:
    print("当前配置：")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    print(f"配置文件位置：{config_path()}")


def cmd_history() -> None:
    records = load_history()
    if not records:
        print("还没有历史记录。解析一条带截止日期的通知后会自动记录。")
        return
    with_dl = [r for r in records if r.get("deadline")]
    without = [r for r in records if not r.get("deadline")]
    with_dl.sort(key=lambda r: r["deadline"])
    print(f"\n全部记录（共 {len(records)} 条，文件：{HISTORY_FILE}）")
    print("=" * 50)
    for r in with_dl + without:
        print()
        for line in format_record(r, show_fit=True):
            print(line)
        print(f"      记录于 {r.get('added', '')}")
        print("  " + "-" * 46)


def analyze(text: str, cfg: dict, match_only: bool = False) -> list[dict]:
    rows = judge(extract_items(text), cfg)
    order = {"符合": 0, "待确认": 1, "年级不符": 2}
    rows.sort(key=lambda r: order.get(r["适配"], 3))
    if match_only:
        rows = [r for r in rows if r["适配"] != "年级不符"] or rows
    return rows


def analyze_and_print(text: str, cfg: dict, as_json: bool = False,
                      match_only: bool = False, save: bool = True) -> list[dict]:
    rows = analyze(text, cfg, match_only)
    print_result(rows, cfg, as_json)
    if save and not as_json:
        saved = [rec for rec in rows_to_records(rows) if save_record(rec)]
        if saved:
            print()
            for rec in saved:
                print(f"已记录：{rec['name']}")
                print(f"   截止：{rec['deadline'] or '未注明'} {rec.get('deadline_time', '')}".rstrip())
                print(f"   报名：{rec.get('method') or '未注明'}")
                if rec.get("link"):
                    print(f"   链接：{rec['link']}")
                print(f"   专业：{rec.get('major_limit', '不限专业')}")
            print(f"\n共 {len(saved)} 条写入历史：{HISTORY_FILE}")
    return rows


def read_pasted_text(first: str) -> str:
    """单行直接返回；否则继续读，空行或 . 结束。"""
    if len(first) >= 20:
        return first
    print("（继续粘贴剩余内容，空行或输入 . 结束）")
    lines = [first]
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip() in ("", "."):
            break
        lines.append(line)
    return "\n".join(lines)


def pause(msg: str = "\n按回车键退出...") -> None:
    """双击运行时防止窗口一闪而过；管道输入时静默跳过。"""
    try:
        input(msg)
    except (EOFError, KeyboardInterrupt):
        print()


MENU = (
    "  monthly [年 月]  查看月程（默认本月）\n"
    "  all              查看全部记录\n"
    "  config           修改年级/专业\n"
    "  show             查看配置\n"
    "  q                退出"
)


def interactive_mode(cfg: dict) -> None:
    """主循环：每轮开头都回到当月概览，粘贴 → 记录 → 回到月程，形成闭环。"""
    while True:
        # 每次操作完回到这里，自动重新显示当月概览
        monthly_view()

        print()
        print("=" * 50)
        print("粘贴竞赛/评奖通知即解析，或输入命令：")
        print(MENU)
        print("=" * 50)

        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        cmd = text.lower()
        if cmd in ("q", "quit", "exit"):
            break
        if cmd == "config":
            cmd_config(cfg)
            continue
        if cmd == "show":
            cmd_show(cfg)
            continue
        if cmd in ("all", "history", "h"):
            cmd_history()
            continue
        if cmd.startswith("monthly"):
            parts = cmd.split()
            try:
                monthly_view(
                    int(parts[1]) if len(parts) > 1 else None,
                    int(parts[2]) if len(parts) > 2 else None,
                )
            except ValueError:
                print("用法：monthly 2026 10")
            continue
        if not text:
            continue

        rows = analyze_and_print(read_pasted_text(text), cfg)
        if not rows:
            print("未识别到竞赛/评奖信息，请检查内容。")


def main() -> int:
    # 中文 Windows 下被重定向到管道时可能是 GBK，统一成 UTF-8 避免乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    # 管道输入按 UTF-8 读；控制台输入本身走宽字符 API，不受影响
    try:
        if sys.stdin and not sys.stdin.isatty():
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        prog="award-tracker",
        description="提取竞赛/评奖/志愿通知的报名信息，并按个人画像筛选",
    )
    parser.add_argument("--text", help="通知原文")
    parser.add_argument("--file", help="通知文本文件路径")
    parser.add_argument("--config", action="store_true", help="修改年级/专业等配置")
    parser.add_argument("--show", action="store_true", help="查看当前配置")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--match-only", action="store_true", help="只显示符合/待确认（默认不过滤）")
    parser.add_argument("--history", action="store_true", help="查看历史记录")
    parser.add_argument("--monthly", nargs="*", metavar=("Y", "M"), help="查看某月日程，如 --monthly 2026 10")
    parser.add_argument("--no-save", action="store_true", help="本次解析不写入历史")
    args = parser.parse_args()

    cfg, created = ensure_config()
    if created:
        print(f"首次运行，已生成配置文件：{config_path()}")
        print("（可运行 award-tracker --config 修改学院/年级/专业）")
        print()

    if args.show:
        cmd_show(cfg)
        return 0

    if args.config:
        cmd_config(cfg)
        return 0

    if args.history:
        cmd_history()
        return 0

    if args.monthly is not None:
        try:
            y = int(args.monthly[0]) if len(args.monthly) > 0 else None
            m = int(args.monthly[1]) if len(args.monthly) > 1 else None
        except ValueError:
            print("用法：--monthly 2026 10")
            return 1
        monthly_view(y, m)
        return 0

    # 双击 / 无参数：临近提醒 + 剪贴板 + 交互主循环（每轮回到月程）
    if len(sys.argv) == 1:
        startup_check(cfg, show_monthly=False)
        clip = read_clipboard().strip()
        if clip:
            preview = clip[:200] + ("..." if len(clip) > 200 else "")
            print(f"\n检测到剪贴板内容：\n{preview}")
            ans = input("\n解析这段内容？(Y/n) ").strip().lower()
            if ans in ("", "y", "yes"):
                analyze_and_print(clip, cfg)
        interactive_mode(cfg)
        pause()
        return 0

    text = ""
    if args.text:
        text = args.text
    elif args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            print(f"读取文件失败：{e}")
            return 1

    if not text:
        parser.print_help()
        return 1

    analyze_and_print(text, cfg, args.json, args.match_only, save=not args.no_save)

    if sys.platform == "win32" and not args.text and not args.file:
        pause()
    return 0


if __name__ == "__main__":
    sys.exit(main())
