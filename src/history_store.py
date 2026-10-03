"""申し送りの履歴をSQLite(ファイル1つのデータベース)に保存し、過去分とまたがる重複候補を調べる。

保存するのは「AI抽出済みの申し送り記録」だけで、判定結果(要確認など)は保存しない。
判定は基準日時によって変わるため、毎回、保存した記録から計算し直す。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from audit_rules import (
    DATETIME_FORMAT,
    DEFAULT_DUPLICATE_WINDOW_HOURS,
    DEFAULT_HIGH_PRIORITY_SLA_HOURS,
    audit_records,
    parse_datetime,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "output" / "history.db"

# 保存する項目(監査ルールが読む項目と同じ)
RECORD_FIELDS = (
    "record_id",
    "submitted_at",
    "source_department",
    "message_text",
    "extracted_action",
    "extracted_assignee",
    "extracted_deadline",
    "extracted_priority",
    "action_required",
)


class HistoryStore:
    """申し送り履歴の保存先。record_idが同じ記録は、新しい内容で置き換える。"""

    def __init__(self, path: Path = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS records (
                    record_id TEXT PRIMARY KEY,
                    submitted_at TEXT NOT NULL,
                    source_department TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    extracted_action TEXT NOT NULL,
                    extracted_assignee TEXT NOT NULL,
                    extracted_deadline TEXT NOT NULL,
                    extracted_priority TEXT NOT NULL,
                    action_required TEXT NOT NULL,
                    saved_at TEXT NOT NULL
                )
                """
            )

    def upsert(self, records: Iterable[Dict[str, str]]) -> int:
        """記録を保存する(同じrecord_idは置き換え)。保存した件数を返す。"""

        saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            tuple(str(record.get(field, "")) for field in RECORD_FIELDS) + (saved_at,)
            for record in records
        ]
        placeholders = ", ".join("?" for _ in range(len(RECORD_FIELDS) + 1))
        columns = ", ".join(RECORD_FIELDS) + ", saved_at"
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executemany(
                f"INSERT OR REPLACE INTO records ({columns}) VALUES ({placeholders})",
                rows,
            )
        return len(rows)

    def count(self) -> int:
        """保存している件数。"""

        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]

    def fetch_between(self, start: datetime, end: datetime) -> List[Dict[str, str]]:
        """登録日時がstart〜endの記録を、登録日時の順に返す。"""

        columns = ", ".join(RECORD_FIELDS)
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(
                f"SELECT {columns} FROM records "
                "WHERE submitted_at >= ? AND submitted_at <= ? "
                "ORDER BY submitted_at, record_id",
                (start.strftime(DATETIME_FORMAT), end.strftime(DATETIME_FORMAT)),
            ).fetchall()
        return [dict(zip(RECORD_FIELDS, row)) for row in rows]


def audit_with_history(
    store: HistoryStore,
    new_records: List[Dict[str, str]],
    as_of: Optional[datetime] = None,
    sla_hours: float = DEFAULT_HIGH_PRIORITY_SLA_HOURS,
    duplicate_window_hours: float = DEFAULT_DUPLICATE_WINDOW_HOURS,
    save: bool = True,
) -> Tuple[List[Dict[str, str]], int]:
    """新しい記録を、保存済みの履歴と合わせて監査する。

    重複候補は、履歴の記録とも比べる(今回の分とだけでなく、前回までの分とも)。
    返すのは新しい記録の監査結果と、比較に使った履歴の件数。
    saveがTrueなら、監査のあとで新しい記録を履歴に保存する。
    """

    new_ids = {record["record_id"] for record in new_records}
    submitted_times = [parse_datetime(record.get("submitted_at", "")) for record in new_records]
    submitted_times = [time for time in submitted_times if time is not None]

    history: List[Dict[str, str]] = []
    if submitted_times:
        margin = timedelta(hours=duplicate_window_hours)
        history = [
            record
            for record in store.fetch_between(min(submitted_times) - margin, max(submitted_times) + margin)
            if record["record_id"] not in new_ids
        ]

    combined = audit_records(history + new_records, as_of, sla_hours, duplicate_window_hours)
    results = combined[len(history):]

    if save:
        store.upsert(new_records)
    return results, len(history)
