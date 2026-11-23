"""在本机运行测试后向独立的发布回执端点报告结果。"""

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="P-101")
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--environment", choices=["production", "staging"], default="production"
    )
    parser.add_argument("--result", choices=["success", "failed"], default="success")
    parser.add_argument("--pipeline-id", default=None)
    parser.add_argument(
        "--released-at", help="带时区的 ISO 8601 时间；重投同一回执时保持一致"
    )
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    tests = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=ROOT
    )
    outcome = "failed" if tests.returncode else args.result
    data = {
        "project_id": args.project,
        "environment": args.environment,
        "version": args.version,
        "result": outcome,
        "pipeline_id": args.pipeline_id or "local-" + uuid.uuid4().hex[:12],
        "released_at": args.released_at or datetime.now(timezone.utc).isoformat(),
    }
    token = os.getenv("TRACKER_RELEASE_TOKEN", "").strip()
    if not token:
        token_file = (
            Path(os.getenv("TRACKER_DATA_DIR") or ROOT / ".local") / "release-token"
        )
        if not token_file.is_file():
            parser.exit(2, "未找到本地回执令牌。请先启动台账。\n")
        token = token_file.read_text().strip()
    req = request.Request(
        "http://127.0.0.1:8711/api/releases",
        data=json.dumps(data).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        },
    )
    print("本次回执 " + json.dumps(data, ensure_ascii=False), flush=True)
    try:
        with request.urlopen(req, timeout=90) as result:
            print(
                json.dumps({**data, **json.load(result)}, ensure_ascii=False, indent=2)
            )
    except error.HTTPError as exc:
        parser.exit(
            1, f"回执请求失败：HTTP {exc.code}。请核对令牌、项目和流水线标识。\n"
        )
    except (error.URLError, TimeoutError):
        parser.exit(1, "无法确认本地服务回执。请查看项目历史并确认服务已启动。\n")
    return int(outcome == "failed")


if __name__ == "__main__":
    sys.exit(main())
