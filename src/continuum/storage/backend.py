# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""存储后端抽象（docs/01 §3：Storage Backend 接口——默认 SQLite，云托管形态预留 Postgres）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from continuum.udf import UDFMessage


class StorageBackend(ABC):
    """核心只依赖本接口。实现必须保证：
    - append 幂等（同一消息重复推送不产生重复行）；
    - 一切写动作落 audit_log；
    - messages 原文永不改写（宪法 1/9；用户删除权走 quarantine 流程，非物理改写）。
    """

    @abstractmethod
    def migrate(self) -> list[str]:
        """应用全部未应用迁移，返回本次应用的迁移文件名。"""

    @abstractmethod
    def ensure_session(
        self,
        host_agent: str,
        external_id: str,
        title: str | None = None,
        project_id: str | None = None,
    ) -> int:
        """取或建会话（幂等），返回 session_id。"""

    @abstractmethod
    def append_messages(self, session_id: int, messages: list[UDFMessage]) -> tuple[list[int], int]:
        """原文落库（幂等）。返回 (新消息 id 列表, 跳过数)。"""

    @abstractmethod
    def iter_session_messages(self, session_id: int, limit: int | None = None) -> Iterator[UDFMessage]:
        """按 seq 升序取会话消息（UDF 形态）。"""

    @abstractmethod
    def list_session_messages_with_ids(self, session_id: int, limit: int = 200) -> list[tuple[int, UDFMessage]]:
        """同上但携带 message_id（extract 的数据源需要来源指针）。"""

    @abstractmethod
    def fetch_messages_since(self, since_ts: str | None = None, limit: int = 200) -> list[tuple[int, UDFMessage]]:
        """取 (message_id, UDFMessage) 列表（跨会话，ts 升序）。since_ts=None 时取最新 limit 条。
        sweeping/extract 的数据源。"""

    @abstractmethod
    def search_content(self, query: str, limit: int = 20) -> list[dict]:
        """FTS 全文检索（P1 快速路径主力之一；P0 仅直通实现）。"""

    @abstractmethod
    def audit(self, actor: str, action: str, target: str | None, detail: dict | None = None) -> int:
        """写审计账本，返回 audit id。"""

    @abstractmethod
    def close(self) -> None: ...
