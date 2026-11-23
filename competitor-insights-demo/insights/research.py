"""Evidence-backed product decisions for a single, dated research snapshot."""

import csv
from datetime import date
from pathlib import Path

from .catalog import InputError

CAPABILITIES = {
    "generate": (
        "首图生成",
        "用相同设计任务生成首张可继续编辑的图。",
        "成功生成且可用的首图数 / 提交任务数",
    ),
    "iterate": (
        "多轮迭代",
        "从同一首图发起第二轮修改，记录是否要重新上传。",
        "可沿用首图的第二轮任务数 / 第二轮任务数",
    ),
    "edit": (
        "编辑衔接",
        "从调色后的画面进入局部编辑，核对底图与上一轮结果是否一致。",
        "无需重传且底图正确的任务数 / 进入局部编辑的任务数",
    ),
    "mask": (
        "选区精度",
        "使用同一张细椅腿贴墙的测试图，只修改墙面，逐次核对选区外是否改变。",
        "选区外发生漂移的输出数 / 有效编辑输出数",
    ),
    "reference": (
        "参考约束",
        "附上同一张材质样图，连续编辑两轮，逐轮检查参考素材与材质约束。",
        "两轮后仍保留约束的任务数 / 使用参考素材的任务数",
    ),
    "history": (
        "版本恢复",
        "从第三轮画布还原第二轮，核对图像、选区与后续编辑入口。",
        "成功恢复且可继续编辑的任务数 / 发起恢复的任务数",
    ),
    "export": (
        "交付导出",
        "将同一画布导出，检查分辨率、格式及透明背景是否符合任务要求。",
        "通过约定规格检查的文件数 / 导出文件数",
    ),
}
ACTIONS = {
    "generate": "优先排查从任务提交到首图可用之间的失败步骤，保留输入条件做复现。",
    "iterate": "让第二轮默认选择上一轮输出，并保留切换参考版本的入口。",
    "edit": "进入编辑画布时默认带入用户刚生成的版本，同时保留手动换图入口。",
    "mask": "把边界细节单独设为验收项，先确认影响哪些图像，再决定调整选区还是重绘策略。",
    "reference": "在后续编辑步骤里标明仍在生效的参考素材，并复核其约束。",
    "history": "让用户能回到上一轮修改前的画布，并核对恢复后能否再次编辑。",
    "export": "在导出前核对目标格式与交付约束，并保留一份可复现的错误样例。",
}
INTENT_NAMES = {
    "opportunity": "机会优先级",
    "benchmark": "竞品能力对标",
    "change": "版本复验",
}
SOURCE_NAMES = {
    "walkthrough": "任务走查",
    "release": "版本说明",
    "feedback": "用户反馈",
}
STATE_NAMES = {
    "verified": "走查已验证",
    "friction": "实测有阻碍",
    "conflict": "材料有分歧",
    "reported": "材料待复验",
    "unknown": "待补证据",
}
SIGNALS = Path(__file__).resolve().parents[1] / "samples" / "research-signals.csv"


def sample_signals():
    with SIGNALS.open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def _evidence(products, rows, cutoff, signals):
    result = []
    for row in rows:
        if row["product"] not in products or row["date"] > cutoff:
            continue
        result.append(
            {
                "id": row["reference"],
                "product": row["product"],
                "version": row["version"],
                "date": row["date"],
                "capability": row["step"],
                "source_type": "walkthrough",
                "origin": row["material"],
                "source": row["material"],
                "source_label": SOURCE_NAMES["walkthrough"],
                "position": {
                    "pass": "support",
                    "fail": "friction",
                    "direct": "support",
                    "start": "support",
                    "upload": "friction",
                    "unknown": "unknown",
                }[row["handoff"]],
                "excerpt": row["observation"],
            }
        )
    for row in signals:
        if row["product"] in products and row["date"] <= cutoff:
            result.append({**row, "source_label": SOURCE_NAMES[row["source_type"]]})
    return sorted(result, key=lambda r: (r["date"], r["id"]))


def _refs(records):
    return [r["id"] for r in records]


def _citations(refs):
    return " ".join(f"[{ref}]" for ref in refs)


def _contrary_retest(current, walks):
    """Find a prior observation that contradicts this result on the exact same version."""
    opposite = {"support": "friction", "friction": "support"}.get(current["position"])
    if opposite is None:
        return None
    return next(
        (
            r
            for r in reversed(walks)
            if r["source_type"] == "walkthrough"
            and r["capability"] == current["capability"]
            and r["version"] == current["version"]
            and r["date"] < current["date"]
            and r["position"] == opposite
        ),
        None,
    )


def _matrix_cell(product, capability, evidence, batch):
    scoped = [
        r for r in evidence if r["product"] == product and r["capability"] == capability
    ]
    latest_walk = next(
        (
            r
            for r in scoped
            if r["source_type"] == "walkthrough" and r["date"] == batch["date"]
        ),
        None,
    )
    signals = [
        r
        for r in scoped
        if r["source_type"] != "walkthrough" and r["version"] == batch["version"]
    ]
    contrary = _contrary_retest(latest_walk, scoped) if latest_walk else None
    current = [r for r in (latest_walk, contrary) if r] + signals
    positions = {r["position"] for r in current}
    disagreement = {"support", "friction"} <= positions
    if contrary:
        status = "conflict"
        note = (
            f"{latest_walk['version']} 在 {contrary['date']} 与 {latest_walk['date']} 的同一步骤走查结果不一致，"
            "需核实两次使用的素材、入口和参数。"
        )
    elif latest_walk and latest_walk["position"] in {"support", "friction"}:
        status = "verified" if latest_walk["position"] == "support" else "friction"
        note = latest_walk["excerpt"]
        if disagreement:
            note += " 同版材料存在不同信号，以该任务实测给出当前判断。"
    elif disagreement:
        status, note = (
            "conflict",
            "同版材料同时出现正向与负向信号，需要在同一任务上复测。",
        )
    elif signals:
        status = "reported"
        direction = (
            "有阻碍的线索"
            if "friction" in positions
            else "可用的线索"
            if "support" in positions
            else "待确认的描述"
        )
        note = f"同版材料提供{direction}，需要实际走查确认。"
    else:
        status, note = "unknown", "当前批次缺少可判定的材料。"
    return {
        "product": product,
        "version": batch["version"],
        "status": status,
        "label": STATE_NAMES[status],
        "note": note,
        "refs": _refs(current),
        "disagreement": disagreement,
        "source_count": len({r["origin"] for r in current}),
        "signal_positions": sorted({r["position"] for r in signals}),
        "walk_ref": latest_walk["id"] if latest_walk else None,
    }


def _changes(products, evidence, batches):
    result = []
    for product in products:
        walks = [
            r
            for r in evidence
            if r["product"] == product and r["source_type"] == "walkthrough"
        ]
        dates = sorted({r["date"] for r in walks})
        if len(dates) < 2:
            continue
        for new in (r for r in walks if r["date"] == batches[product]["date"]):
            old = _contrary_retest(new, walks)
            if old:
                transition = "same_version_conflict"
            else:
                old = next(
                    (
                        r
                        for r in reversed(walks)
                        if r["capability"] == new["capability"]
                        and r["date"] < new["date"]
                    ),
                    None,
                )
                if not old:
                    continue
                pair = old["position"], new["position"]
                transition = {
                    ("friction", "support"): "resolved",
                    ("support", "friction"): "regressed",
                }.get(pair)
                if not transition:
                    transition = "unknown" if "unknown" in pair else "stable"
            result.append(
                {
                    "product": product,
                    "capability": new["capability"],
                    "transition": transition,
                    "from_version": old["version"],
                    "to_version": new["version"],
                    "from_date": old["date"],
                    "to_date": new["date"],
                    "refs": _refs([old, new]),
                }
            )
    return result


def _finding(title, priority, reason, action, key, refs):
    _, experiment, metric = CAPABILITIES[key]
    return {
        "title": title,
        "priority": priority,
        "reason": reason,
        "action": action,
        "capability": key,
        "experiment": experiment,
        "metric": metric,
        "refs": list(dict.fromkeys(refs)),
    }


def _opportunities(matrix, changes, names):
    result = []
    for axis in matrix:
        key, label = axis["key"], axis["label"]
        for cell in axis["cells"]:
            name = names[cell["product"]]
            if cell["status"] == "friction":
                peer = next(
                    (
                        c
                        for c in axis["cells"]
                        if c["product"] != cell["product"] and c["status"] == "verified"
                    ),
                    None,
                )
                reason = cell["note"]
                refs = cell["refs"][:]
                if peer:
                    reason += (
                        f" 对照组 {names[peer['product']]} 已在自身走查中完成该步。"
                    )
                    refs += [peer["walk_ref"]]
                result.append(
                    _finding(
                        f"{name} · 优先处理{label}",
                        "优先处置",
                        reason,
                        ACTIONS[key],
                        key,
                        refs,
                    )
                )
            elif cell["status"] == "conflict":
                result.append(
                    _finding(
                        f"{name} · 复核{label}",
                        "优先验证",
                        cell["note"],
                        "固定素材、入口与参数复测，在新记录中写明测试条件和版本。",
                        key,
                        cell["refs"],
                    )
                )
    for change in changes:
        if change["transition"] == "resolved":
            key = change["capability"]
            result.append(
                _finding(
                    f"{names[change['product']]} · {CAPABILITIES[key][0]}转回归",
                    "转入回归",
                    f"同一步骤在 {change['from_version']} 出现阻碍，"
                    f"{change['to_version']} 的本次走查已通过。",
                    "保留两版走查作为回归用例，扩展测试不同素材与入口。",
                    key,
                    change["refs"],
                )
            )
    order = {"优先处置": 0, "优先验证": 1, "转入回归": 2}
    return sorted(result, key=lambda finding: order[finding["priority"]])


def _benchmark(matrix, names):
    findings = []
    for axis in matrix:
        cells = axis["cells"]
        comparable = {
            (
                c["status"],
                tuple(c["signal_positions"]) if c["status"] == "reported" else (),
            )
            for c in cells
        }
        if len(comparable) == 1:
            continue
        detail = "；".join(
            f"{names[c['product']]} 为「{c['label']}」"
            + (f"（{c['note']}）" if c["status"] == "reported" else "")
            for c in cells
        )
        findings.append(
            _finding(
                f"{axis['label']} · 对标差异",
                "对标",
                detail + "。",
                "先复核条件是否一致，再判断差异来自产品能力还是证据覆盖。",
                axis["key"],
                [ref for c in cells for ref in c["refs"]],
            )
        )
    return findings


def _version_findings(changes, names):
    findings = []
    for change in changes:
        kind = change["transition"]
        if kind not in {"resolved", "regressed"}:
            continue
        key = change["capability"]
        solved = kind == "resolved"
        findings.append(
            _finding(
                f"{names[change['product']]} · {CAPABILITIES[key][0]}{'转回归' if solved else '出现回退'}",
                "转入回归" if solved else "优先处置",
                f"对照 {change['from_date']}（{change['from_version']}）"
                f"与 {change['to_date']}（{change['to_version']}）的同一步骤，"
                + ("新版走查已通过。" if solved else "新版走查出现阻碍。"),
                "保留两版引用，按相同输入增加一轮边界场景复验。",
                key,
                change["refs"],
            )
        )
    return findings


def _gaps(matrix):
    gaps = []
    for status in ("conflict", "reported", "unknown"):
        for axis in matrix:
            for cell in axis["cells"]:
                if cell["status"] == status:
                    gaps.append(
                        {
                            "product": cell["product"],
                            "capability": axis["key"],
                            "label": axis["label"],
                            "reason": cell["note"],
                            "refs": cell["refs"],
                        }
                    )
    return gaps


def _markdown(intent, cutoff, findings, matrix):
    lines = [f"## {INTENT_NAMES[intent]}", f"证据截止 {cutoff}。"]
    for finding in findings:
        lines += [
            "",
            f"### {finding['title']}",
            finding["reason"] + " " + _citations(finding["refs"]),
            f"下一步：{finding['action']}",
            f"验证方法：{finding['experiment']}",
            f"观测口径：{finding['metric']}。",
        ]
    if intent == "benchmark":
        lines += ["", "### 七项能力覆盖"]
        for row in matrix:
            cells = "；".join(
                f"{c['product']} {c['label']} {_citations(c['refs'])}"
                for c in row["cells"]
            )
            lines.append(f"- {row['label']}：{cells}")
    return "\n".join(lines)


def build(pair, rows, *, intent="opportunity", as_of=None, signals=None):
    """Analyze the latest available walkthrough batch per product at the cutoff."""
    if intent not in INTENT_NAMES:
        raise InputError("请选择机会优先级、能力对标或版本复验。")
    if as_of is not None:
        try:
            if date.fromisoformat(as_of).isoformat() != as_of:
                raise ValueError
        except (ValueError, TypeError):
            raise InputError("证据截止日应使用 YYYY-MM-DD。") from None
    products = pair["products"]
    names = {p["sku"]: p["name"] for p in products}
    # The demo opens at the last real walkthrough. Importing another batch advances the horizon.
    cutoff = as_of or max(
        (r["date"] for r in rows if r["product"] in names),
        default=date.today().isoformat(),
    )
    evidence = _evidence(
        names, rows, cutoff, sample_signals() if signals is None else signals
    )
    batches = {}
    for sku in names:
        walks = [
            r
            for r in evidence
            if r["product"] == sku and r["source_type"] == "walkthrough"
        ]
        signals = [r for r in evidence if r["product"] == sku]
        anchor = walks[-1] if walks else signals[-1] if signals else {}
        batches[sku] = {
            "version": anchor.get("version"),
            "date": walks[-1]["date"] if walks else None,
        }
    matrix = [
        {
            "key": key,
            "label": spec[0],
            "cells": [_matrix_cell(sku, key, evidence, batches[sku]) for sku in names],
        }
        for key, spec in CAPABILITIES.items()
    ]
    changes = _changes(names, evidence, batches)
    gaps = _gaps(matrix)
    for item in evidence:
        batch = batches[item["product"]]
        if (
            item["source_type"] != "walkthrough"
            and batch["date"]
            and item["date"] > batch["date"]
            and item["version"] != batch["version"]
        ):
            gaps.append(
                {
                    "product": item["product"],
                    "capability": item["capability"],
                    "label": CAPABILITIES[item["capability"]][0],
                    "reason": f"{item['version']} 的材料晚于最近走查批次，请补同版本实测。",
                    "refs": [item["id"]],
                }
            )
    opportunities = _opportunities(matrix, changes, names)
    if intent == "benchmark":
        findings = _benchmark(matrix, names)
    elif intent == "change":
        findings = _version_findings(changes, names)
        if not findings and not any(
            r["transition"] == "same_version_conflict" for r in changes
        ):
            comparable = [r for r in changes if r["transition"] == "stable"]
            reason = (
                "最近两批可比较步骤的衔接结果相同。"
                if comparable
                else "缺少同一产品、同一步骤的两批可判定走查。"
            )
            findings.append(
                _finding(
                    "继续补做版本对照",
                    "待复验",
                    reason,
                    "固定同一份素材和步骤，补齐下一批观测。",
                    "edit",
                    [ref for r in comparable for ref in r["refs"]],
                )
            )
        findings.extend(f for f in opportunities if f["priority"] == "优先验证")
    else:
        findings = opportunities
    if not findings:
        gap = (
            gaps[0]
            if gaps
            else {
                "capability": "edit",
                "refs": [],
                "reason": "已观察到的任务尚无可复现的主要阻碍。",
            }
        )
        findings = [
            _finding(
                "收集下一轮验证证据",
                "补证据",
                gap["reason"],
                "补齐同一版本、同一任务的对照。",
                gap["capability"],
                gap["refs"],
            )
        ]
    conflicts = [
        ref
        for row in matrix
        for c in row["cells"]
        if c["disagreement"]
        for ref in c["refs"]
    ]
    walks = [r for r in evidence if r["source_type"] == "walkthrough"]
    coverage = {
        sku: sum(
            c["status"] in {"verified", "friction"}
            for row in matrix
            for c in row["cells"]
            if c["product"] == sku
        )
        for sku in names
    }
    trace = [
        {
            "step": "筛选材料",
            "detail": f"按产品与截止日筛出 {len(evidence)} 条证据，来自 {len({r['origin'] for r in evidence})} 份去重材料。",
            "refs": _refs(evidence),
        },
        {
            "step": "核对版本与步骤",
            "detail": f"读取 {len(walks)} 条走查，按各产品最近批次对齐七项能力；旧版本仅参与历史对照。",
            "refs": _refs(walks),
        },
        {
            "step": "检查相反信号",
            "detail": f"发现 {sum(c['disagreement'] for row in matrix for c in row['cells'])} 个存在分歧的矩阵单元。",
            "refs": list(dict.fromkeys(conflicts)),
        },
        {
            "step": INTENT_NAMES[intent],
            "detail": f"产出 {len(findings)} 项判断，关联可复验步骤及指标口径。",
            "refs": list(dict.fromkeys(r for f in findings for r in f["refs"])),
        },
    ]
    return {
        "as_of": cutoff,
        "intent": intent,
        "products": products,
        "capabilities": matrix,
        "findings": findings,
        "changes": changes,
        "gaps": gaps,
        "coverage": coverage,
        "evidence": evidence,
        "trace": trace,
        "markdown": _markdown(intent, cutoff, findings, matrix),
    }
