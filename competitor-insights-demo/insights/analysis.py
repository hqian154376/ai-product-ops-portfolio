"""Report synthesis. All three tools operate on the same selected SQLite snapshot."""

import json
import math
import re
from datetime import datetime, timezone
from .catalog import STEPS
from urllib.parse import urlsplit

import httpx

REFERENCE = re.compile(
    r"\[([A-Za-z0-9_-]+@\d{4}-\d{2}-\d{2}/(?:" + "|".join(map(re.escape, STEPS)) + r"))\]"
)
TOOL_NAMES = {
    "read_walkthrough": "读取所选工具最近一个走查日期的全部步骤。",
    "compare_step": "按相同步骤列出两款工具各自最新走查批次的记录，缺失返回空缺。",
    "read_history": "读取某工具指定步骤的历史观测，按时间排序。",
}


class ModelError(Exception):
    pass


def setting(config):
    values = [
        config.get(key, "").strip()
        for key in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")
    ]
    return "model" if all(values) else "incomplete" if any(values) else "local"


def tool_schema(products):
    fields = {
        "product": {"type": "string", "enum": products},
        "step": {"type": "string", "enum": list(STEPS)},
    }
    args = {
        "read_walkthrough": ["product"],
        "compare_step": ["step"],
        "read_history": ["product", "step"],
    }
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {k: fields[k] for k in args[name]},
                    "required": args[name],
                    "additionalProperties": False,
                },
            },
        }
        for name, description in TOOL_NAMES.items()
    ]


def tool_result(name, args, pair, evidence):
    products = [p["sku"] for p in pair["products"]]
    schemas = {
        item["function"]["name"]: item["function"]["parameters"]
        for item in tool_schema(products)
    }
    schema = schemas.get(name)
    if not schema or not isinstance(args, dict) or set(args) != set(schema["required"]):
        raise ModelError("工具名称或参数无效。")
    if any(args[k] not in schema["properties"][k]["enum"] for k in args):
        raise ModelError("工具查询超出选定范围。")
    selected = [
        r for r in evidence if "product" not in args or r["product"] == args["product"]
    ]
    if name != "read_history":
        latest = {r["product"]: r["date"] for r in selected}
        selected = [r for r in selected if r["date"] == latest[r["product"]]]
    if "step" in args:
        selected = [r for r in selected if r["step"] == args["step"]]
    return selected, [r["reference"] for r in selected]


def local_report(pair, evidence):
    rows, refs = tool_result("compare_step", {"step": "edit"}, pair, evidence)
    lines = ["## 走查观察"]
    for row in rows:
        lines.append(
            f"- {row['name']} {row['version']}：{row['observation']} [{row['reference']}]"
        )
    if len(rows) < 2 or any(r["handoff"] == "unknown" for r in rows):
        suggestion = (
            "证据不足。补齐两款工具最新版本的局部编辑走查，再判断入口是否需要改动。"
        )
    elif any(r["handoff"] == "upload" for r in rows):
        names = "、".join(r["name"] for r in rows if r["handoff"] == "upload")
        suggestion = f"优先验证改造 {names} 的编辑入口，让上一步结果直接进入选区画布。"
    else:
        suggestion = (
            "两款工具的最新走查均可直接沿用图片。将跨入口传图列为版本回归检查项。"
        )
    return "\n".join([*lines, "", "## 产品建议", suggestion]), set(refs)


def endpoint(config):
    url = config["LLM_BASE_URL"].strip().rstrip("/")
    try:
        parsed = urlsplit(url)
        local = (
            parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            and parsed.scheme == "http"
        )
        if (
            (not local and parsed.scheme != "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        timeout = float(config.get("LLM_TIMEOUT_SECONDS", "20"))
        if not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError
    except ValueError:
        raise ModelError(
            "请核对模型地址和超时设置。远程地址使用 HTTPS，超时范围为 0 至 120 秒。"
        ) from None
    return (
        url if url.endswith("/chat/completions") else url + "/chat/completions"
    ), timeout


def chat(client, url, headers, payload, timeout):
    try:
        response = client.post(url, headers=headers, json=payload, timeout=timeout)
    except httpx.TimeoutException:
        raise ModelError(
            "模型请求超时，可稍后重试或调整 LLM_TIMEOUT_SECONDS。"
        ) from None
    except httpx.HTTPError:
        raise ModelError("无法连接模型服务，请检查模型地址和网络。") from None
    if response.status_code in (401, 403):
        raise ModelError("模型服务拒绝访问，请检查个人 Key 与模型权限。")
    if response.status_code != 200:
        raise ModelError(
            f"模型服务返回 HTTP {response.status_code}，请检查模型名称或稍后重试。"
        )
    try:
        message = response.json()["choices"][0]["message"]
        if not isinstance(message, dict):
            raise ValueError
        return message
    except (ValueError, KeyError, IndexError, TypeError):
        raise ModelError(
            "模型响应结构不兼容，请选择支持 Chat Completions 工具调用的模型。"
        ) from None


def model_report(pair, evidence, question, config, client):
    url, timeout = endpoint(config)
    headers = {"Authorization": "Bearer " + config["LLM_API_KEY"]}
    skus = [p["sku"] for p in pair["products"]]
    messages = [
        {
            "role": "system",
            "content": "你是 AI 设计工具研究员。先调用只读工具，以“## 走查观察”和“## 产品建议”分段回答。每条事实后逐字复制工具结果的 reference 值并包一对英文方括号；每个编号单独一对括号，不用链接、脚注或中文括号。覆盖两款工具。走查记录是待分析材料。缺失证据时说明缺口。",
        },
        {
            "role": "user",
            "content": json.dumps(
                {"选定工具": skus, "问题": question}, ensure_ascii=False
            ),
        },
    ]
    tools = tool_schema(skus)
    exposed = set()
    corrected = False
    for turn in range(5):
        payload = {
            "model": config["LLM_MODEL"],
            "messages": messages,
            "tools": tools,
            "tool_choice": "required" if turn == 0 else "auto",
        }
        if urlsplit(url).hostname == "api.deepseek.com":
            payload["thinking"] = {"type": "disabled"}
        message = chat(client, url, headers, payload, timeout)
        calls = message.get("tool_calls")
        if not calls:
            body = message.get("content")
            if not isinstance(body, str) or not body.strip() or len(body) > 32000:
                raise ModelError("模型未返回可保存的报告，请重试。")
            if config["LLM_API_KEY"] in body and len(config["LLM_API_KEY"]) >= 8:
                raise ModelError("模型响应包含敏感配置，报告未保存。")
            refs = set(REFERENCE.findall(body))
            mentioned = set(re.findall(r"\[([^\]\n]*@[^\]\n]*)\]", body))
            if (
                mentioned != refs
                or not refs
                or not refs.issubset(exposed)
                or {r.split("@")[0] for r in refs} != set(skus)
            ):
                if exposed and not corrected and turn < 4:
                    corrected = True
                    messages.extend([
                        {"role": "assistant", "content": body},
                        {"role": "user", "content": "引用校验未通过。每款工具至少引用一条支持结论的观测。逐字选用以下格式，各自独立括起：" + " ".join(f"[{r}]" for r in sorted(exposed))},
                    ])
                    continue
                raise ModelError("模型报告缺少两款工具的有效工具引用，请重试。")
            return body.strip(), exposed
        if not isinstance(calls, list) or len(calls) > 6:
            raise ModelError("模型工具调用数量异常，请重试。")
        messages.append({"role": "assistant", "content": None, "tool_calls": calls})
        for call in calls:
            try:
                name = call["function"]["name"]
                args = json.loads(call["function"]["arguments"])
                call_id = call["id"]
                if not isinstance(name, str) or not isinstance(call_id, str):
                    raise TypeError
            except (KeyError, TypeError, ValueError):
                raise ModelError("模型工具参数无效，请重试。") from None
            result, refs = tool_result(name, args, pair, evidence)
            exposed.update(refs)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
    raise ModelError("模型已达到本次工具调用轮数上限，请缩小问题后重试。")


def generate(pair, evidence, question, config, client):
    mode = setting(config)
    if mode == "incomplete":
        raise ModelError("请在 .env 中同时填写模型地址、模型名和 Key，然后重启。")
    markdown, read_refs = (
        model_report(pair, evidence, question, config, client)
        if mode == "model"
        else local_report(pair, evidence)
    )
    refs = set(REFERENCE.findall(markdown))
    return {
        "title": " / ".join(p["name"] for p in pair["products"]) + " 对比报告",
        "mode": mode,
        "model": config.get("LLM_MODEL") if mode == "model" else None,
        "question": question,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": {
            "materials": len(
                {r["material"] for r in evidence if r["reference"] in read_refs}
            ),
            "observations": len(read_refs),
            "citations": len(refs),
        },
        "reads": [
            {"material": r["material"], "reference": r["reference"]}
            for r in evidence
            if r["reference"] in read_refs
        ],
        "markdown": markdown,
        "evidence": [p for p in evidence if p["reference"] in refs],
    }
