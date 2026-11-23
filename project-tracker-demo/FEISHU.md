# 把自己的飞书多维表格接入看板

一个项目在表中占一行，以「项目编号」匹配。首次联调建议用一张新建的演示表，从 `examples/projects.csv` 导入样例。表里修改的项目信息在本地点击导入时生效，正式环境的发布结果由流水线回写。

| 列 | 飞书字段类型 | 谁维护 |
| --- | --- | --- |
| 项目编号 | 文本，推荐作为首列，每行唯一 | 表格和台账共用的稳定编号，如 P-101 |
| 项目名称、负责人、阶段 | 文本，各一列 | 项目维护者 |
| 进度 | 数字，0 到 100 的整数 | 项目维护者 |
| 里程碑、目标日期、风险、下一步 | 文本，各一列，目标日期格式 YYYY-MM-DD | 项目维护者 |
| 当前版本、发布结果、发布时间 | 文本，各一列 | 正式环境的发布回执 |

1. 在飞书开放平台创建企业自建应用，开通多维表格记录的读取和编辑权限，发布应用版本。在表的应用访问或协作者设置中授予该应用可编辑权限。应用权限和目标表授权都需要生效。
2. 从应用凭证页取得 App ID 和 App Secret。直接打开多维表格本体，从 `/base/<app_token>` 取得表格应用标识，从 `?table=<table_id>` 取得数据表标识。知识库页面的 wiki 节点标识与多维表格应用标识用途不同，可从表格的开发信息获取实际标识。
3. 将 `.env.example` 复制为 `.env`，填写 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_APP_TOKEN`、`FEISHU_TABLE_ID`，重启本地应用。若目录中已经有 `.env`，只更新这四项。个人密钥只放本机。
4. 在飞书修改 P-101 的下一步，回到看板同步项目信息，打开 P-101 确认变更。运行 README 中的正式环境示例发布命令，回到同一行核对当前版本、结果和时间。失败回执显示最近成功版本及本次失败状态。
5. 若提示权限不足，检查应用版本和表级授权。若提示项目缺失或重复，在目标表补上唯一的同编号项目行。修正网络或配置后在看板重试待同步的发布，本地已保存的发布历史可继续查看。

应用通过飞书的企业自建应用令牌、记录列表和记录更新接口访问这一张表。[获取访问令牌](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal) · [列出记录](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/list) · [更新记录](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/update)

包内适配器测试使用本机模拟服务覆盖分页、权限错误、超时和恢复。使用自己的应用和真实表格完成第 4 步，即可确认该应用和表格的实际授权。

## 个人账号接入

安装[飞书官方 CLI](https://github.com/larksuite/cli)，通过 `lark-cli config init --new` 创建应用，再用 `lark-cli auth login --scope "base:record:retrieve base:record:update base:table:read base:field:read"` 完成用户授权。建表和初始化记录时另需对应创建权限。单表列名与上文相同。

在本机 .env 指定 FEISHU_CLI_PATH（可执行文件绝对路径）、FEISHU_APP_TOKEN 和 FEISHU_TABLE_ID，重启台账。个人身份由 CLI 管理。在本作品演示中，已从个人飞书导入 6 项，并核对 P-101 的正式版本、最近发布结果与发布时间回写。
