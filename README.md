# Continuum

**跨 agent 持久记忆与连续人格层。** 你的记忆，所有 agent 通用。

> 状态：**v0.1.0 公开开发中**。每次提交自动在 Linux / macOS / Windows 三平台跑全量测试与安装冒烟。

## 它解决什么

长程任务中上下文压缩导致 agent「换人」：状态丢失、连贯性丢失、人格漂移、红线失守。
Continuum 用四道机制化防线对抗：落库扳机（L3 原文无损层）→ 机制化收敛（sweeping）→ 冷启动装配包 → 红线门禁 hook。

### 为什么「写一份交接文档」不够

换 agent、换会话、上下文被压缩时，常见做法是由人（或上一个 agent）手工写一份交接文档。
这条路有三个无法回避的缺陷：

1. **必然有损**——写交接的人想不全。实测：一份认真撰写的交接文档，接手方读完后
   依然会暴露出多处关键缺失（运行环境、当前进度、协作约定……），而这些缺失往往在踩坑后才被发现。
2. **成本不可控**——写的人要停下来总结现状，接手方要重新建立上下文，两头都耗时；
   任务越长、上下文越大，这份成本越高。
3. **无法验证**——交接文档写的内容与真实状态是否一致，没有任何机制保证。

Continuum 用**机制**替代手工交接：原文永久留存（L3 无损层），状态由 sweeping 自动收敛为
L0/L1（目录只放指针、每层硬预算），冷启动时装配包直接注入——**不依赖任何人或 agent 的
自觉与记忆**。交接不再是一份需要人写的文档，而是系统的一个确定性输出。

## 快速上手

### 安装（核心零第三方依赖）

```bash
pip install "continuum-core[mcp] @ git+https://github.com/zhaonan5582/continuum.git"
# 或克隆本仓库后：
pip install ".[mcp]"
```

`[mcp]` 仅在「方式 A」需要；方式 B（代理）零额外依赖。

### 软件壳（图形界面，零命令行）

```bash
continuum shell --db ~/memory.continuum.db
```

自动打开浏览器（仅 127.0.0.1 本机回环，不暴露网络）：人格定版与 few-shot、红线录入/启停/防误伤回归、
记忆库总览与跨会话检索、沉淀触发、一键体检——全部点选化，不需要记任何命令。
界面支持 **16 种语言**（中/繁/英/日/韩/西/法/德/葡/俄/意/土/越/印尼/阿/印地，阿语自动 RTL）。

**后台常驻（推荐）**——不需要一直开着命令行窗口：

```bash
continuum shell --install-autostart    # 注册登录自启（后台无窗口运行，日志落 ~/.continuum/shell.log）
continuum shell --uninstall-autostart  # 取消自启
```

注册后每次登录自动在后台运行，浏览器直接访问 <http://127.0.0.1:8501> 即可。

### 一键安装（推荐）

```bash
pip install continuum-core        # 或克隆本仓库后 pip install .
continuum setup                   # 探测本机 agent + 自动接入全部（零交互）
```

`continuum setup` 会自动完成：**探测**本机所有已安装的 AI coding agent →
**自动接入**全部（写 MCP 配置，只增不改 + 写前备份 + 幂等）→ 注册后台常驻 →
输出一页现状与待办。想先预览不改动：`continuum setup --dry-run`；
只看探测结果：`continuum agents`。

### 保持喂食（会话内容持续入库）

Continuum 的「喂食器」主动扫描宿主会话文件并增量入库——**独立节奏，不依赖任何工具调用**：

| 方式 | 命令/条件 | 说明 |
|---|---|---|
| 常驻自动（推荐） | `continuum serve` 或 `continuum shell` 运行期间 | 进程自带后台线程，每 2 分钟主动扫描，零额外操作 |
| 系统计划任务 | `continuum feed --once` | 无常驻进程：计划任务每 N 分钟跑一次（扫完即退出） |
| 手动兜底 | `continuum feed --once` | 随时手动补 |

历史存量（部署前的老会话）不自动回灌——由你显式决定：
`continuum import-workbuddy <会话.jsonl>`（幂等，重复导入自动跳过）。

### 接入宿主

Continuum 同时支持**不挂代理**与**挂代理**两条路径——宿主能力不同、用户偏好不同，两条都要通：

**方式 A：MCP 直连（不挂代理，默认推荐）**——宿主直接挂 MCP server（七工具），
落库与召回由**机制扳机**驱动，不依赖 agent 自觉：

```bash
continuum serve --db ~/memory.continuum.db
```

以 WorkBuddy 为例（`~/.workbuddy/mcp.json`；codex 写入 `~/.codex/config.toml` 的
`[mcp_servers.continuum]`，Claude Code 用 hooks 模板）：

```json
{
  "mcpServers": {
    "continuum": {
      "command": "continuum",
      "args": ["serve", "--db", "C:\\Users\\you\\.continuum\\memory.continuum.db"]
    }
  }
}
```

**方式 B：LLM API 透明代理（零宿主配合）**——Continuum 作为本地 HTTP 代理插入
agent 与 LLM API 之间：请求/响应原样转发，同时透明抽取对话入库、注入记忆、按三行制
明示 token 增量。宿主只需把 base URL 改成 `http://127.0.0.1:8402/v1`：

```bash
continuum proxy --target https://api.deepseek.com
```

已支持格式：OpenAI 兼容（DeepSeek/vLLM/Ollama/Groq 等天然覆盖）/ OpenAI Responses /
Anthropic / Gemini（自动探测路由）。两种方式可叠加，互为冗余防线。

### 体检

```bash
continuum doctor
```

一键体检：配置文件 → 记忆库（是否有活动连接）→ 按配置确切命令拉起 server 真实握手，
并给出每个宿主的配置生效方式（冷加载型 / hooks 热加载型 / 代理型）。

### 红线门禁（机制，不靠 agent 自觉）

```bash
# pattern 支持"正则优先、非法回退子串"——写动作短语/正则，别写名词（名词会误拦只读操作）
continuum redline add --pattern "(删除|修改|写入|DELETE|UPDATE)[\\s\\S]{0,20}参照站" \
    --statement "参照站绝对只读，禁止任何写操作"
continuum redline test --redline-id 1 --case positive --sample "删除参照站样本表"
continuum redline test --redline-id 1 --case negative --sample "查询参照站设备列表"
continuum guard-test        # 正反测试集回归：防误伤
```

### 人格（版本化：可 diff / 回滚 / 审计）

```bash
continuum persona version --text "你是老王：干练直接，汇报必须带数字。" --reason "初版"
continuum persona add --user "状态如何？" --agent "3 个模块全绿，0 阻塞。"
continuum persona current
```

冷启动装配包（`memory_assemble`）自动携带当前人格状态块与 few-shot 样本——
换 agent、换会话，人格与默契跟着走。支持**多套命名人格**（如「工作人格」与「写作人格」）一键切换，
版本历史可 diff 可回滚。

## MCP 七工具

| 工具 | 职责 |
|------|------|
| `memory_append` | 原文落库（UDF v1，幂等去重）——由适配器机制推送，不等会话结束 |
| `memory_extract` | 沉淀（低置信度进待确认队列，不冒充高置信度） |
| `memory_recall` | 检索（结构化主力+全文兜底；每条结果必带证据等级与原文指针） |
| `memory_audit` | 溯源——「这条记忆哪来的」的答案入口 |
| `memory_compact` | 按条判决压缩（full/truncate/drop + 理由），产物是清单不是摘要 |
| `memory_assemble` | 冷启动装配包（persona + L0 + L1 + 最近现场，≤8K token） |
| `memory_guard` | 红线门禁判定（allow / warn / block） |

## 设计宪法与隐私

- [设计宪法（公开版，十条）](DESIGN-CONSTITUTION.md) —— 不可妥协，违反即返工
- [隐私白皮书](PRIVACY.md) —— 核心零外联（CI 源码级扫描验证）、无遥测、彻底删除 = 删一个文件

## 已知边界（诚实清单）

- **升级需重启宿主**：stdio MCP server 的生命周期绑定宿主子进程。轻量模式下升级代码后需
  重启宿主一次；「薄壳 + 本地运行时」形态在规划中（将支持热升级与跨宿主汇聚）。
- **人格录入为人工 CLI**：few-shot 样本人工挑选（自动聚类不在承诺内）。
- **历史会话回灌**：增量喂食从部署时刻起算；更早的历史用 `continuum import-workbuddy`
  显式导入——哪些历史入库由你决定。
- **并发写**：SQLite WAL 模式支持多进程/多线程并发读 + 串行写（已实测 200 条双连接并发
  写零丢失）；极端高并发写场景（多进程同时大批量落库）可能出现短暂的 busy 等待，属
  SQLite 正常行为。单用户/单团队本地场景不构成问题。

## 许可

核心：AGPL-3.0-only（见 LICENSE / NOTICE）。「Continuum」名称与标志为商标，分支须改名。
Pro 模块与托管服务不在本仓库。

## 平台支持

| 平台 | 状态 |
|------|------|
| Windows 10/11 | ✅ 开发与全量测试平台 |
| Linux (x86_64) | ✅ CI 矩阵实测（GitHub Actions，push 自动跑） |
| macOS (arm64) | ✅ CI 矩阵实测（GitHub Actions，push 自动跑） |

纯 Python 3.13+ 标准库实现（零第三方核心依赖、零 C 扩展）；核心记忆库默认零外联，
仅代理与判官（BYOK）模块按用户显式配置联网。SQLite 由 Python 内置。

## 开发

```bash
# 全量测试（零依赖）
python -m unittest discover -s tests -v     # Windows 用 PYTHONPATH=src
# 隐私承诺检查（零外联验证）
python tests/check_no_network.py
```
