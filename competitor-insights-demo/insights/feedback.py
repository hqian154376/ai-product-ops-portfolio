"""Count attributable feedback by product and theme across equal date windows."""

import unicodedata
from collections import defaultdict
from datetime import date

from .catalog import InputError

# A capability can contain different customer problems. Explicit phrases take
# precedence; the conservative bigram fallback only claims a single close match.
# IDs are stable when another excerpt is added to an existing theme.
THEMES = {
    "generate": [
        ("latency", "首图等待", ("等待", "排队", "首图很慢", "生成速度", "生成耗时")),
        (
            "prompt",
            "首图与描述一致性",
            ("提示词", "描述不一致", "没按描述", "符合描述", "构图偏差"),
        ),
    ],
    "iterate": [
        (
            "continuity",
            "多轮上下文保留",
            ("上一轮", "第二轮", "上下文", "沿用首图", "忘了前面"),
        ),
        ("variant", "方案分支管理", ("分支", "并排比较", "备选方案", "不同方案")),
    ],
    "edit": [
        (
            "handoff",
            "编辑入口传图",
            ("重传", "重新上传", "再选一次", "第二次选图", "带入画布", "沿用底图"),
        ),
        (
            "entry",
            "编辑入口可发现性",
            ("找不到入口", "入口太深", "入口在哪", "隐藏入口"),
        ),
    ],
    "mask": [
        ("boundary", "选区外细节保留", ("边缘", "边界", "选区外", "椅腿", "家具纹理")),
        ("brush", "选区绘制操控", ("笔刷", "画笔", "套索", "橡皮擦", "刷头")),
    ],
    "reference": [
        (
            "retention",
            "跨轮参考图保留",
            ("参考图", "参考素材", "参考图片", "参考图丢失"),
        ),
        ("style", "参考风格一致性", ("风格", "材质还原", "材质不一致", "颜色跑偏")),
    ],
    "history": [
        ("restore", "恢复此前版本", ("恢复", "还原", "上一个版本", "撤回")),
        ("discover", "查找历史记录", ("历史记录", "历史列表", "版本排序", "版本命名")),
    ],
    "export": [
        (
            "alpha",
            "透明背景导出",
            ("透明底", "透明背景", "背景透明", "透明通道", "alpha", "白色底", "白底"),
        ),
        (
            "size",
            "导出分辨率",
            ("分辨率", "像素尺寸", "尺寸变小", "输出尺寸", "长边像素"),
        ),
    ],
}

CHANNELS = {
    "interview": "访谈",
    "community": "社群反馈",
    "support": "支持工单",
    "unknown": "渠道未登记",
    "mixed": "跨渠道同一来源",
}

METHOD = (
    "仅分析用户反馈摘录。先按能力与主题短语归类；无直接命中时用连续双字比对，"
    "相似度不足或难以区分的摘录留待归类。一条摘录可直接命中多个主题。"
    "同一来源在一期内涉及多个已登记渠道时归入跨渠道，只计算一次；旧材料保留为渠道未登记。"
    "正负倾向来自材料登记。每期每个产品按来源编号去重，一份来源在一个主题内最多计一次；"
    "同一来源同时有正反例时，两种倾向各记一次。分母含该产品在本期的全部反馈来源。"
    "主题热度是样本内占比。每期至少有两份来源、涉及该主题的来源数有变化且占比相差至少20个百分点时，标记升温或降温。"
    "负向异动需上期至少两份来源、本期至少三份独立负向来源、负向来源净增至少两份且占比增加至少20个百分点。"
    "以上口径用于安排复验；当前样本不能估算线上故障率或用户总量。"
)


def _normalize(text):
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _bigrams(text):
    return {text[i : i + 2] for i in range(len(text) - 1)}


def _match(row):
    text = _normalize(row["excerpt"])
    rules = THEMES.get(row["capability"], ())
    hits = [
        (key, label)
        for key, label, phrases in rules
        if any(term in text for term in phrases)
    ]
    if hits:
        return hits

    # Match short contiguous passages, not the length of the entire review.
    # At least three shared bigrams and a clear margin avoid arbitrary ties.
    ranked = []
    available = _bigrams(text)
    for key, label, phrases in rules:
        best = 0.0
        for phrase in (*phrases, label):
            target = _bigrams(phrase)
            if len(available & target) < 3:
                continue
            width = len(phrase) + 1
            for i in range(max(1, len(text) - width + 1)):
                grams = _bigrams(text[i : i + width])
                common = len(grams & target)
                if common >= 3:
                    best = max(best, 2 * common / (len(target) + len(grams)))
        ranked.append((best, key, label))
    ranked.sort(reverse=True)
    if (
        ranked
        and ranked[0][0] >= 0.72
        and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.12)
    ):
        _, key, label = ranked[0]
        return [(key, label)]
    return []


def _parse_window(as_of, days):
    try:
        end = date.fromisoformat(as_of)
        if end.isoformat() != as_of:
            raise ValueError
    except (ValueError, TypeError):
        raise InputError("反馈截止日应使用 YYYY-MM-DD。") from None
    if type(days) is not int or not 1 <= days <= 90:
        raise InputError("反馈窗口须为 1 至 90 的整数天。")
    # Using ordinal differences also handles a cutoff close to the start of the calendar.
    last = end.toordinal()
    start = last - days + 1

    def iso(n):
        return date.fromordinal(max(1, n)).isoformat()

    return {
        "current": {"start": iso(start), "end": as_of},
        "previous": {"start": iso(start - days), "end": iso(start - 1)},
    }, last


def _origins(rows, position=None):
    return {
        row["origin"] for row in rows if position is None or row["position"] == position
    }


def _representatives(current, previous):
    # First show a recent adverse and favourable observation where available,
    # then one earlier comparison. Repeated excerpts do not crowd out other sources.
    picked = []
    seen = set()
    for rows, position in [
        (current, "friction"),
        (current, "support"),
        (previous, None),
        (current, None),
    ]:
        candidates = [
            r for r in reversed(rows) if position is None or r["position"] == position
        ]
        for row in candidates:
            key = row["origin"], row["position"]
            if key not in seen:
                picked.append(
                    {
                        k: row[k]
                        for k in (
                            "id",
                            "source",
                            "origin",
                            "date",
                            "excerpt",
                            "position",
                        )
                    }
                )
                seen.add(key)
                break
    return picked


def _origin_channels(rows):
    buckets = defaultdict(set)
    for r in rows:
        known = buckets[(r["product"], r["origin"])]
        channel = r.get("channel")
        if channel in {"interview", "community", "support"}:
            known.add(channel)
    return {
        origin: ("mixed" if len(channels) > 1 else next(iter(channels), "unknown"))
        for origin, channels in buckets.items()
    }


def _channel_mix(current, previous, channel_index):
    result = {}
    for period, rows in (("current", current), ("previous", previous)):
        sources = defaultdict(set)
        negative = defaultdict(set)
        for r in rows:
            key = r["product"], channel_index[period][(r["product"], r["origin"])]
            sources[key].add(r["origin"])
            if r["position"] == "friction":
                negative[key].add(r["origin"])
        for key, origin_ids in sources.items():
            sku, channel = key
            item = result.setdefault(
                key,
                {
                    "product": sku,
                    "channel": channel,
                    "label": CHANNELS[channel],
                    "current_sources": 0,
                    "previous_sources": 0,
                    "current_negative": 0,
                    "previous_negative": 0,
                },
            )
            item[period + "_sources"] = len(origin_ids)
            item[period + "_negative"] = len(negative[key])
    return sorted(
        result.values(),
        key=lambda r: (r["product"], -r["current_sources"], r["channel"]),
    )


def _topic(
    product, capability, key, label, rows, totals, idx, last, days, channel_index
):
    current = [r for r in rows if idx[r["id"]] > last - days]
    previous = [r for r in rows if last - 2 * days < idx[r["id"]] <= last - days]
    if not current and not previous:
        return None
    now, then = len(_origins(current)), len(_origins(previous))
    negative, old_negative = (
        len(_origins(current, "friction")),
        len(_origins(previous, "friction")),
    )
    total, old_total = totals
    # Compare fractions with integer arithmetic to keep 20 pp exactly inclusive.
    threshold = total * old_total
    total_change = (now * old_total - then * total) * 5
    negative_change = (negative * old_total - old_negative * total) * 5
    if not old_total:
        trend = "缺少上期基线"
    elif not total:
        trend = "本期无反馈"
    elif min(total, old_total) < 2:
        trend = "样本不足"
    elif now > then and total_change >= threshold:
        trend = "升温"
    elif now < then and -total_change >= threshold:
        trend = "降温"
    else:
        trend = "变化有限"
    needs_review = (
        old_total >= 2
        and negative >= 3
        and negative - old_negative >= 2
        and negative_change >= threshold
    )
    current_text = f"本期 {now}/{total} 份来源提及" if total else "本期没有反馈来源"
    previous_text = f"上期 {then}/{old_total} 份" if old_total else "缺少上期样本"
    reason = f"{current_text}；{previous_text}。"
    if needs_review:
        reason += f" 其中负向来源从 {old_negative}/{old_total} 增至 {negative}/{total}，达到复验门槛。"
    result = {
        "id": f"{product}:{capability}:{key}",
        "product": product,
        "capability": capability,
        "label": label,
        "current_sources": now,
        "previous_sources": then,
        "current_total": total,
        "previous_total": old_total,
        "current_negative": negative,
        "previous_negative": old_negative,
        "current_positive": len(_origins(current, "support")),
        "previous_positive": len(_origins(previous, "support")),
        "trend": trend,
        "reason": reason,
        "needs_review": needs_review,
        "first_seen": rows[0]["date"],
        "last_seen": rows[-1]["date"],
        "current_refs": [r["id"] for r in current],
        "previous_refs": [r["id"] for r in previous],
        "refs": [r["id"] for r in (*previous, *current)],
        "representatives": _representatives(current, previous),
        "channel_mix": _channel_mix(current, previous, channel_index),
    }
    return result


def build(evidence, products, as_of, window_days=7):
    """Summarize the selected products' feedback with two adjacent, inclusive windows."""
    periods, last = _parse_window(as_of, window_days)
    skus = {p["sku"] for p in products}
    feedback = {
        r["id"]: r
        for r in evidence
        if r["source_type"] == "feedback"
        and r["product"] in skus
        and r["date"] <= as_of
    }
    source_rows = sorted(feedback.values(), key=lambda row: (row["date"], row["id"]))
    idx = {r["id"]: date.fromisoformat(r["date"]).toordinal() for r in source_rows}
    present = defaultdict(set)
    previous = defaultdict(set)
    groups = defaultdict(list)
    unmatched = []
    for row in source_rows:
        ordinal = idx[row["id"]]
        if ordinal > last - window_days:
            present[row["product"]].add(row["origin"])
        elif ordinal > last - 2 * window_days:
            previous[row["product"]].add(row["origin"])
        matches = _match(row)
        for key, label in matches:
            groups[(row["product"], row["capability"], key, label)].append(row)
        if not matches and ordinal > last - 2 * window_days:
            unmatched.append(row["id"])

    current_rows = [r for r in source_rows if idx[r["id"]] > last - window_days]
    previous_rows = [
        r
        for r in source_rows
        if last - 2 * window_days < idx[r["id"]] <= last - window_days
    ]
    channel_index = {
        "current": _origin_channels(current_rows),
        "previous": _origin_channels(previous_rows),
    }
    topics = []
    for (sku, capability, key, label), rows in groups.items():
        totals = len(present[sku]), len(previous[sku])
        summary = _topic(
            sku,
            capability,
            key,
            label,
            rows,
            totals,
            idx,
            last,
            window_days,
            channel_index,
        )
        if summary is not None:
            topics.append(summary)
    topics.sort(
        key=lambda t: (
            not t["needs_review"],
            -t["current_negative"],
            -t["current_sources"],
            t["id"],
        )
    )
    alerts = [
        {
            "topic_id": t["id"],
            "product": t["product"],
            "capability": t["capability"],
            "label": t["label"],
            "reason": t["reason"],
            "refs": t["refs"],
            "priority": "待复验",
            "channel_mix": t["channel_mix"],
        }
        for t in topics
        if t["needs_review"]
    ]
    coverage = [
        {
            "product": sku,
            "current_total": len(present[sku]),
            "previous_total": len(previous[sku]),
        }
        for sku in sorted(skus)
    ]
    return {
        "as_of": as_of,
        "window_days": window_days,
        "periods": periods,
        "coverage": coverage,
        "channel_mix": _channel_mix(current_rows, previous_rows, channel_index),
        "topics": topics,
        "alerts": alerts,
        "unclassified": {"count": len(unmatched), "refs": unmatched},
        "method": METHOD,
    }
