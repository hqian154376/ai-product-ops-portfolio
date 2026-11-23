"""读取同一张多维表格的项目字段，只回写正式环境的发布摘要。"""

import json
import os
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


class FeishuError(Exception):
    """可直接展示的错误，不包含请求凭据或第三方响应正文。"""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


PROJECT_COLUMNS = {
    "id": "项目编号", "name": "项目名称", "owner": "负责人", "stage": "阶段",
    "progress": "进度", "milestone": "里程碑", "due": "目标日期",
    "risk": "风险", "next_step": "下一步",
}


def _text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and all(isinstance(item, dict) and isinstance(item.get("text"), str) for item in value):
        return "".join(item["text"] for item in value).strip()
    raise FeishuError("字段格式不符，请按模板使用文本列，进度使用数字列。")


def _progress(value):
    if isinstance(value, bool):
        raise FeishuError("进度须为 0 至 100 的整数。")
    if isinstance(value, (int, float)) and 0 <= value <= 100 and int(value) == value:
        return int(value)
    text = _text(value)
    if text.isascii() and text.isdigit() and len(text) <= 3 and int(text) <= 100:
        return int(text)
    raise FeishuError("进度须为 0 至 100 的整数。")


class Bitable:
    def __init__(self, app_id, app_secret, app_token, table_id, *,
                 origin="https://open.feishu.cn", timeout=8):
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self.table_id = table_id
        self.origin = origin.rstrip("/")
        self.timeout = timeout
        self.http = build_opener(_NoRedirect())

    @classmethod
    def from_env(cls):
        names = ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_APP_TOKEN", "FEISHU_TABLE_ID")
        values = [os.environ.get(name, "").strip() for name in names]
        cli = os.environ.get("FEISHU_CLI_PATH", "").strip()
        if cli:
            if not all(values[2:]):
                raise FeishuError("个人账号模式需要目标表标识。")
            return PersonalBitable(cli, *values[2:])
        return cls(*values) if any(values) else None

    def _request(self, method, path, data=None, token=None):
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if token:
            headers["Authorization"] = "Bearer " + token
        body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
        request = Request(self.origin + path, data=body, headers=headers, method=method)
        try:
            with self.http.open(request, timeout=self.timeout) as response:
                value = json.load(response)
        except HTTPError as exc:
            exc.close()
            if exc.code in (401, 403):
                raise FeishuError("飞书拒绝访问，请核对应用权限和目标表的协作者授权。") from None
            raise FeishuError(f"飞书请求失败（HTTP {exc.code}），请核对目标表或稍后重试。") from None
        except (URLError, OSError):
            raise FeishuError("连接飞书失败或请求超时，请检查网络后重试。") from None
        except (ValueError, UnicodeError):
            raise FeishuError("飞书返回了无法解析的内容。") from None
        if not isinstance(value, dict) or value.get("code") != 0:
            code = value.get("code") if isinstance(value, dict) else None
            suffix = f"（错误码 {code}）" if type(code) is int else ""
            raise FeishuError("飞书操作失败，请核对应用授权、表标识及字段名称" + suffix + "。")
        return value

    def _token(self):
        if not all((self.app_id, self.app_secret, self.app_token, self.table_id)):
            raise FeishuError("请在 .env 中填全四项飞书配置，重启后重试。")
        response = self._request("POST", "/open-apis/auth/v3/tenant_access_token/internal",
                                 {"app_id": self.app_id, "app_secret": self.app_secret})
        token = response.get("tenant_access_token")
        if not isinstance(token, str) or not token:
            raise FeishuError("未能获得飞书应用访问令牌。")
        return token

    @property
    def _records_path(self):
        app, table = (quote(value, safe="") for value in (self.app_token, self.table_id))
        return f"/open-apis/bitable/v1/apps/{app}/tables/{table}/records"

    def _rows(self, token):
        rows = {}
        params = {"page_size": 100}
        seen_pages = set()
        while True:
            response = self._request("GET", self._records_path + "?" + urlencode(params), token=token)
            page = response.get("data")
            if not isinstance(page, dict) or not isinstance(page.get("items"), list):
                raise FeishuError("飞书记录列表格式异常。")
            for record in page["items"]:
                if not isinstance(record, dict) or not isinstance(record.get("fields"), dict):
                    raise FeishuError("飞书记录缺少字段。")
                fields = record["fields"]
                if not fields or all(value in (None, "", []) for value in fields.values()):
                    continue
                project_id = _text(fields.get("项目编号"))
                if not project_id or not isinstance(record.get("record_id"), str) or not record["record_id"]:
                    raise FeishuError("有记录缺少项目编号或记录标识，请按模板补全后重试。")
                if project_id in rows:
                    raise FeishuError("项目编号重复，请在飞书为每个项目保留唯一一行。")
                rows[project_id] = record
            if not page.get("has_more"):
                return rows
            cursor = page.get("page_token")
            if not isinstance(cursor, str) or not cursor or cursor in seen_pages:
                raise FeishuError("飞书分页标识异常，请重新同步。")
            seen_pages.add(cursor)
            params["page_token"] = cursor

    def list_projects(self):
        rows = self._rows(self._token())
        projects = []
        for record in rows.values():
            fields = record["fields"]
            projects.append({name: (_progress(fields.get(col)) if name == "progress" else _text(fields.get(col)))
                             for name, col in PROJECT_COLUMNS.items()})
        return projects

    def write_release(self, project_id, version, result, released_at):
        token = self._token()
        target = self._rows(token).get(project_id)
        if target is None:
            raise FeishuError("飞书中找不到对应项目编号，请先在表中建立该项目行。")
        status = {"success": "成功", "failed": "失败"}.get(result)
        if status is None:
            raise FeishuError("发布结果须为 success 或 failed。")
        self._update(token, target["record_id"], {"当前版本": version or "", "发布结果": status, "发布时间": released_at})

    def _update(self, token, record_id, fields):
        self._request("PUT", self._records_path + "/" + quote(record_id, safe=""),
                      {"fields": fields}, token=token)


class PersonalBitable(Bitable):
    """使用官方 CLI 保存的用户授权，仅访问配置的单表。"""
    def __init__(self, cli, app_token, table_id):
        self.cli = cli
        self.app_token = app_token
        self.table_id = table_id

    def _token(self):
        # CLI 管理令牌并在每次调用时选择用户身份。
        return None

    def _call(self, command, *args):
        argv = [self.cli, "base", command, "--as", "user", "--base-token",
                self.app_token, "--table-id", self.table_id, "--format", "json", *args]
        try:
            reply = subprocess.run(argv, capture_output=True, timeout=30, check=True)
            data = json.loads(reply.stdout)
            if not data.get("ok"):
                raise ValueError
            return data["data"]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            raise FeishuError("飞书个人授权调用失败，请检查 CLI 登录状态和目标表权限。") from None

    def _rows(self, token):
        rows = {}
        offset = 0
        while True:
            page = self._call("+record-list", "--offset", str(offset), "--limit", "100")
            for rid, values in zip(page["record_id_list"], page["data"], strict=True):
                fields = dict(zip(page["fields"], values, strict=True))
                if not any(v is not None and v != "" for v in values):
                    continue
                key = _text(fields.get("项目编号"))
                if not key or key in rows:
                    raise FeishuError("请检查飞书记录是否有缺失或重复的项目编号。")
                rows[key] = {"record_id": rid, "fields": fields}
            if not page.get("has_more"):
                return rows
            if not page["data"]:
                raise FeishuError("飞书分页没有返回记录。")
            offset += len(page["data"])

    def _update(self, token, record_id, fields):
        self._call("+record-upsert", "--record-id", record_id, "--json",
                   json.dumps(fields, ensure_ascii=False))
