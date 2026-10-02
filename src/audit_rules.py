"""AI Handover Guard V0.3 - 申し送り抽出結果の監査。"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from excel_report import write_audit_xlsx


# ============================================================
# 1. パス設定
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_PATH = PROJECT_ROOT / "data" / "mock_ai_output.csv"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "output" / "audit_result.csv"

REQUIRED_ACTION_FIELDS = {
    "extracted_action": "作業内容",
    "extracted_assignee": "担当者",
    "extracted_deadline": "期限",
}

# 原文に明確なリスクがある場合に使う一般化キーワード
HIGH_PRIORITY_KEYWORDS = (
    "至急",
    "緊急",
    "事故",
    "安全",
    "異音",
    "停止",
    "予備電源",
    "未到着",
    "届いていません",
    "クレーム",
    "苦情",
)

# 通常業務として扱う代表的なキーワード
LOW_PRIORITY_KEYWORDS = (
    "共有のみ",
    "対応不要",
    "照明",
    "連絡網",
    "定例",
)

# 申し送りの日時項目の書式（登録日時・期限とも共通）
DATETIME_FORMAT = "%Y-%m-%d %H:%M"

# 緊急度「高」は、文中の期限に関わらず、登録から何時間以内に着手すべきかの目安。
# サンプル値であり、実運用では業種・組織に合わせて調整する（--sla-hoursで上書き可能）。
DEFAULT_HIGH_PRIORITY_SLA_HOURS = 4.0

# 同じ担当者・同じ作業内容が、これより短い間隔で複数回登録されたら重複候補とする目安。
# サンプル値であり、実運用では調整する（--duplicate-window-hoursで上書き可能）。
DEFAULT_DUPLICATE_WINDOW_HOURS = 24.0


# ============================================================
# 2. 共通変換
# ============================================================

def to_bool(value: str) -> bool:
    """CSVの真偽値表現をPythonのboolへ変換する。"""

    return value.strip().lower() in {"true", "1", "yes", "y"}


def parse_datetime(value: str) -> Optional[datetime]:
    """「YYYY-MM-DD HH:MM」形式の文字列をdatetimeへ変換する。読めない場合はNoneを返す。"""

    try:
        return datetime.strptime(value.strip(), DATETIME_FORMAT)
    except (ValueError, AttributeError):
        return None


def determine_rule_priority(record: Dict[str, str]) -> str:
    """原文と対応要否からPython側の優先度を独立判定する。"""

    if not to_bool(record.get("action_required", "")):
        return "低"

    source_text = " ".join(
        (
            record.get("message_text", ""),
            record.get("extracted_action", ""),
        )
    )
    if any(keyword in source_text for keyword in HIGH_PRIORITY_KEYWORDS):
        return "高"
    if any(keyword in source_text for keyword in LOW_PRIORITY_KEYWORDS):
        return "低"
    return "中"


def add_priority_audit(result: Dict[str, str]) -> None:
    """AI優先度とPython判定を比較し、独立した監査結果を追加する。"""

    ai_priority = result.get("extracted_priority", "").strip()
    rule_priority = determine_rule_priority(result)
    result["rule_priority"] = rule_priority

    if ai_priority == rule_priority:
        result["priority_audit_status"] = "MATCH"
        result["priority_audit_message"] = "AI判定とPython判定が一致しています"
    else:
        result["priority_audit_status"] = "NEEDS_REVIEW"
        result["priority_audit_message"] = (
            f"優先度要確認：AI={ai_priority or '未設定'} / Python={rule_priority}"
        )


def add_sla_audit(
    result: Dict[str, str],
    as_of: datetime,
    sla_hours: float,
) -> None:
    """緊急度に応じた着手期限の超過を判定する。

    緊急度「高」は、文中の期限に関わらず、登録からsla_hours以内に着手すべきという
    自社側の目安を基準にする（顧客側が示す期限は、放置してよい猶予ではないため）。
    緊急度「中」「低」は、これまでどおり文中の期限を基準にする。
    """

    if not to_bool(result.get("action_required", "")):
        result["sla_audit_status"] = "NOT_APPLICABLE"
        result["sla_audit_message"] = "対応不要のため対象外です"
        return

    submitted_at = parse_datetime(result.get("submitted_at", ""))
    if submitted_at is None:
        result["sla_audit_status"] = "UNKNOWN"
        result["sla_audit_message"] = "登録日時を読み取れないため判定できません"
        return

    if result.get("rule_priority") == "高":
        elapsed_hours = (as_of - submitted_at).total_seconds() / 3600
        if elapsed_hours > sla_hours:
            result["sla_audit_status"] = "NEEDS_REVIEW"
            result["sla_audit_message"] = (
                f"緊急度が高いのに登録から{elapsed_hours:.1f}時間"
                f"({sla_hours:g}時間以内が目安)着手されていません"
            )
        else:
            result["sla_audit_status"] = "ON_TIME"
            result["sla_audit_message"] = "緊急度「高」の目安時間内です"
        return

    deadline = parse_datetime(result.get("extracted_deadline", ""))
    if deadline is None:
        result["sla_audit_status"] = "NOT_APPLICABLE"
        result["sla_audit_message"] = "期限が未確定のため判定できません"
    elif as_of > deadline:
        result["sla_audit_status"] = "NEEDS_REVIEW"
        result["sla_audit_message"] = "文中の期限を過ぎています"
    else:
        result["sla_audit_status"] = "ON_TIME"
        result["sla_audit_message"] = "文中の期限内です"


def normalize_for_duplicate_match(value: str) -> str:
    """重複候補の比較用に、空白・句読点をそろえる。"""

    return re.sub(r"[\s　。、，,]+", "", value.strip())


def set_default_duplicate_audit(result: Dict[str, str]) -> None:
    """重複候補判定の既定値を設定する（複数件どうしの比較はmark_duplicate_candidatesで行う）。"""

    if not to_bool(result.get("action_required", "")):
        result["duplicate_audit_status"] = "NOT_APPLICABLE"
        result["duplicate_audit_message"] = "対応不要のため対象外です"
        return

    assignee = result.get("extracted_assignee", "").strip()
    action = result.get("extracted_action", "").strip()
    if not assignee or not action:
        result["duplicate_audit_status"] = "NOT_APPLICABLE"
        result["duplicate_audit_message"] = "担当者または作業内容が未確定のため判定できません"
        return

    if parse_datetime(result.get("submitted_at", "")) is None:
        result["duplicate_audit_status"] = "UNKNOWN"
        result["duplicate_audit_message"] = "登録日時を読み取れないため判定できません"
        return

    result["duplicate_audit_status"] = "UNIQUE"
    result["duplicate_audit_message"] = "同じ担当者・作業内容の重複候補はありません"


def mark_duplicate_candidates(
    results: List[Dict[str, str]],
    window_hours: float,
) -> None:
    """同じ担当者・同じ作業内容が、短期間に複数回登録されていないか確認する。

    誤って二重登録してしまったケースや、違う人が同じ案件を別々に報告したケースを
    想定した目安の判定であり、必ずしも間違いと決めつけるものではない。
    """

    groups: Dict[Tuple[str, str], List[Tuple[datetime, Dict[str, str]]]] = defaultdict(list)
    for result in results:
        if result.get("duplicate_audit_status") != "UNIQUE":
            continue
        key = (
            result["extracted_assignee"].strip(),
            normalize_for_duplicate_match(result["extracted_action"]),
        )
        submitted_at = parse_datetime(result["submitted_at"])
        groups[key].append((submitted_at, result))

    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda pair: pair[0])
        for i in range(len(group) - 1):
            submitted_a, record_a = group[i]
            submitted_b, record_b = group[i + 1]
            gap_hours = (submitted_b - submitted_a).total_seconds() / 3600
            if gap_hours > window_hours:
                continue
            for record, other in ((record_a, record_b), (record_b, record_a)):
                record["duplicate_audit_status"] = "NEEDS_REVIEW"
                other_id = other.get("record_id", "(ID不明)")
                record["duplicate_audit_message"] = (
                    f"重複候補：{other_id}と担当者・作業内容が同じで、"
                    f"{gap_hours:.1f}時間以内に登録されています"
                )


# ============================================================
# 3. 1件分の監査
# ============================================================

def audit_record(
    record: Dict[str, str],
    as_of: Optional[datetime] = None,
    sla_hours: float = DEFAULT_HIGH_PRIORITY_SLA_HOURS,
) -> Dict[str, str]:
    """1件の申し送りを監査し、状態と警告理由を追加する。"""

    result = dict(record)
    add_priority_audit(result)
    add_sla_audit(result, as_of or datetime.now(), sla_hours)
    set_default_duplicate_audit(result)

    if not to_bool(record.get("action_required", "")):
        result["audit_status"] = "INFO_ONLY"
        result["missing_fields"] = ""
        result["audit_message"] = "対応不要の共有情報です"
        return result

    missing_labels = [
        label
        for field_name, label in REQUIRED_ACTION_FIELDS.items()
        if not record.get(field_name, "").strip()
    ]

    if missing_labels:
        result["audit_status"] = "NEEDS_REVIEW"
        result["missing_fields"] = " / ".join(missing_labels)
        result["audit_message"] = f"要確認：{'・'.join(missing_labels)}が未確定です"
    else:
        result["audit_status"] = "READY"
        result["missing_fields"] = ""
        result["audit_message"] = "実行に必要な基本項目がそろっています"

    return result


# ============================================================
# 4. 複数件の監査
# ============================================================

def audit_records(
    records: Iterable[Dict[str, str]],
    as_of: Optional[datetime] = None,
    sla_hours: float = DEFAULT_HIGH_PRIORITY_SLA_HOURS,
    duplicate_window_hours: float = DEFAULT_DUPLICATE_WINDOW_HOURS,
) -> List[Dict[str, str]]:
    """複数の申し送りをまとめて監査する。"""

    as_of = as_of or datetime.now()
    audited_records = [audit_record(record, as_of, sla_hours) for record in records]
    mark_duplicate_candidates(audited_records, duplicate_window_hours)
    return audited_records


# ============================================================
# 5. CSV入出力
# ============================================================

def read_csv(path: Path) -> List[Dict[str, str]]:
    """UTF-8 BOM対応でCSVを読み込む。"""

    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def write_csv(path: Path, records: List[Dict[str, str]]) -> None:
    """監査結果をExcelでも開きやすいUTF-8 BOM付きCSVへ出力する。"""

    if not records:
        raise ValueError("出力対象のデータがありません")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)


# ============================================================
# 6. メイン処理
# ============================================================

def run_audit(
    input_path: Path,
    output_path: Path,
    as_of: Optional[datetime] = None,
    sla_hours: float = DEFAULT_HIGH_PRIORITY_SLA_HOURS,
    duplicate_window_hours: float = DEFAULT_DUPLICATE_WINDOW_HOURS,
) -> List[Dict[str, str]]:
    """指定CSVを監査して結果を書き出す。"""

    source_records = read_csv(input_path)
    audited_records = audit_records(source_records, as_of, sla_hours, duplicate_window_hours)
    write_csv(output_path, audited_records)
    return audited_records


def main() -> None:
    """指定データを監査し、結果CSVを作成する。"""

    parser = argparse.ArgumentParser(description="AI抽出結果の不足項目を監査する")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--as-of",
        type=lambda value: datetime.strptime(value, DATETIME_FORMAT),
        default=None,
        help="期限超過を判定する基準日時（省略時は実行時刻。例: 2026-09-20 09:00）",
    )
    parser.add_argument(
        "--sla-hours",
        type=float,
        default=DEFAULT_HIGH_PRIORITY_SLA_HOURS,
        help="緊急度「高」の案件を、登録から何時間以内の着手が目安とするか",
    )
    parser.add_argument(
        "--duplicate-window-hours",
        type=float,
        default=DEFAULT_DUPLICATE_WINDOW_HOURS,
        help="同じ担当者・同じ作業内容が、何時間以内なら重複候補とみなすか",
    )
    args = parser.parse_args()

    audited_records = run_audit(
        args.input,
        args.output,
        args.as_of,
        args.sla_hours,
        args.duplicate_window_hours,
    )

    needs_review_count = sum(
        record["audit_status"] == "NEEDS_REVIEW" for record in audited_records
    )
    sla_review_count = sum(
        record["sla_audit_status"] == "NEEDS_REVIEW" for record in audited_records
    )
    duplicate_review_count = sum(
        record["duplicate_audit_status"] == "NEEDS_REVIEW" for record in audited_records
    )
    print(f"監査完了: {len(audited_records)}件")
    print(f"要確認: {needs_review_count}件")
    print(f"期限超過の要確認: {sla_review_count}件")
    print(f"重複候補の要確認: {duplicate_review_count}件")
    print(f"出力先(CSV): {args.output.resolve()}")

    # 人が読むためのExcel版(日本語見出し・列幅調整つき)を同じ場所に作る
    xlsx_path = args.output.with_suffix(".xlsx")
    write_audit_xlsx(xlsx_path, audited_records)
    print(f"出力先(Excel): {xlsx_path.resolve()}")


if __name__ == "__main__":
    main()
