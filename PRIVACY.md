# Continuum 隐私白皮书

> 版本：v0.1（随代码同步更新）· 本页所有承诺均可在源码与 CI 中验证，不是口号。

## 一句话承诺

**你的记忆库是你本机的一个 SQLite 文件。核心软件不联网、不遥测、不上传。**

## 数据在哪

- 全部数据存于**你指定的一个本地 SQLite 文件**（默认 `~/.continuum/memory.continuum.db`）。
- 备份：`continuum backup <输出路径>`（SQLite 原生备份，产物同样是本地文件）。
- **彻底删除 = 删掉这个文件。** 无云端副本、无隐藏索引、无同步残留。

## 核心零外联（可验证）

- 核心包源码**禁止出现任何网络库引用**。这不是口头承诺：CI 每次提交都跑
  `tests/check_no_network.py` 静态扫描，命中 `urllib / requests / httpx / socket /
  http.client / urllib3 / websocket` 任一关键词即构建失败。
- SQLite 由 Python 标准库提供；核心零第三方依赖（见 `pyproject.toml` 的 `dependencies = []`）。

## 两个显式联网边界（均由你主动发起）

| 模块 | 何时联网 | 联到哪里 | 如何关闭 |
|------|---------|---------|---------|
| LLM API 透明代理（方式 B） | 仅当你显式启动 `continuum proxy --target <上游地址>` | 你自己指定的上游 LLM API | 不启动即不联网 |
| 判决模型（BYOK，可选） | 仅当你显式配置 endpoint + API key | 你自己配置的端点 | 不配置即不联网 |

两者源码文件头均带 `BYOK-USER-INITIATED-NETWORK` 标记，且不在 CI 零外联扫描的豁免之外——
扫描规则与白名单见 `tests/check_no_network.py`（与 `tests/test_commercial.py::TestZeroNetwork` 同一契约）。

## 无遥测

- 不收集使用统计、崩溃报告、匿名指标——没有任何此类代码路径。
- 软件不「回话」：除上述两个显式边界外，无任何出站网络行为。

## 记忆可溯源

- 每条记忆强制携带四字段：**来源指针 / 时间戳 / 证据等级 / 有效期**（缺一不许写入）。
- `continuum` 的 audit 工具可回答「这条记忆是哪来的」。

## 红线在你手里

- 红线（如"严禁删除生产配置"）逐字存储、禁止改写；`memory_guard` 在写/删/外发前按红线拦截。
- 正反测试集（`redline` CLI + `guard-test`）保证红线不误伤。

## 许可与边界

- 核心软件：AGPL-3.0-only（见 LICENSE / NOTICE）。
- 「Continuum」名称与标志为商标，不在 AGPL 授权范围内；分支须改名。
- Pro 模块与托管服务（如有）不在本仓库，另行声明。

---

Copyright (C) 2026 Continuum contributors
