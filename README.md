# Continuum（工作代号）

**跨 agent 持久记忆与连续人格层。** 你的记忆，所有 agent 通用。

> 状态：**公开开发中（P0 已完成，P1 进行中）**。设计文档与商业规划为内部文件，不在本仓库；正式发布 v0.1 于 P4。CI 每次提交自动在 Linux / macOS / Windows 三平台跑全量测试。

## 它解决什么

长程任务中上下文压缩导致 agent「换人」：状态丢失、连贯性丢失、人格漂移、红线失守。
Continuum 用四道机制化防线对抗：落库扳机（L3 原文无损层）→ 机制化收敛（sweeping）→ 冷启动装配包 → 红线门禁 hook。

## 两种接入方式

Continuum 同时支持**不挂代理**与**挂代理**两条路径——宿主能力不同、用户偏好不同，两条都要通：

### 方式 A：MCP 直连（不挂代理，默认推荐）
宿主 agent 直接挂 Continuum MCP server（七工具：append / extract / recall / audit / compact / assemble / guard）。落库与召回由**机制扳机**（轮次 / 时长阈值）驱动，不依赖 agent 自觉。

```bash
python -m continuum.cli serve --db ~/memory.continuum.db
```

### 方式 B：LLM API 透明代理（零宿主配合）
Continuum 作为本地 HTTP 代理插入 agent 与 LLM API 之间：请求/响应原样转发，同时透明抽取对话入库、注入记忆、按三行制明示 token 增量。宿主只需把 base URL 改成 `http://127.0.0.1:8402/v1`，其余零改动。

```bash
python -m continuum.cli proxy --target https://api.deepseek.com
```

已支持格式：OpenAI 兼容 / Anthropic / Gemini（自动探测路由）。

两种方式可叠加：代理负责「拿全对话流」，MCP / hook 负责「推回去」，互为冗余防线。
代理的全部外联仅发生在用户显式启动并指定上游之后；核心记忆库默认零外联。

## 设计宪法（十条，摘要）

1. 摘要是解释，全文是证据——L3 全文库是唯一无损层
2. 溯源四强制字段：来源指针 / 时间戳 / 证据等级 / 有效期，缺一不许写入
3. 任何被收敛移走的信息 30 秒内可寻回
4. 目录只放指针，每层硬预算
5. 压缩 = 按条判决，禁止摘要式压缩
6. 记忆管「知道」，hook 管「不能违反」
7. 核心对宿主零认知（MCP 接入）
8. 本地优先
9. 用户数据主权：可彻底删除
10. 一切记忆操作的触发是机制，不是 agent 的自觉

## 许可

核心：AGPL-3.0-only（见 LICENSE / NOTICE）。Pro 模块与托管服务不在本仓库。

## 平台支持

| 平台 | 状态 |
|------|------|
| Windows 10/11 | ✅ 开发与全量测试平台 |
| Linux (x86_64) | ✅ CI 矩阵实测（GitHub Actions，push 自动跑） |
| macOS (arm64) | ✅ CI 矩阵实测（GitHub Actions，push 自动跑） |

纯 Python 3.13 标准库实现（零第三方核心依赖、零 C 扩展）；核心记忆库默认零外联，仅代理与判官（BYOK）模块按用户显式配置联网。SQLite 由 Python 内置。

## 开发

```bash
# 全量测试（零依赖）
python -m unittest discover -s tests -v     # Windows 用 PYTHONPATH=src
# 隐私承诺检查（零外联验证）
python tests/check_no_network.py
```
