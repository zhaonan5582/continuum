# Continuum（工作代号）

**跨 agent 持久记忆与连续人格层。** 你的记忆，所有 agent 通用。

> 状态：**设计实现中（P0 阶段）**。产品设计与实施方案见项目内部文档；对外发布于 P4，本仓库当前为开发态。

## 它解决什么

长程任务中上下文压缩导致 agent「换人」：状态丢失、连贯性丢失、人格漂移、红线失守。
Continuum 用四道机制化防线对抗：落库扳机（L3 原文无损层）→ 机制化收敛（sweeping）→ 冷启动装配包 → 红线门禁 hook。

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

纯 Python 3.13 标准库实现（零第三方核心依赖、零 C 扩展、零网络调用），SQLite 由 Python 内置。

## 开发

```bash
# 全量测试（零依赖）
python -m unittest discover -s tests -v     # Windows 用 PYTHONPATH=src
# 隐私承诺检查（零外联验证）
python tests/check_no_network.py
```
