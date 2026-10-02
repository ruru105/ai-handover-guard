"""やること抜けチェッカー用の架空申し送りデータ100件を生成する。"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List


# ============================================================
# 1. 出力設定
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MOCK_OUTPUT_PATH = PROJECT_ROOT / "data" / "mock_ai_output.csv"
RAW_INPUT_PATH = PROJECT_ROOT / "data" / "raw_handover.csv"
EXPECTED_LABELS_PATH = PROJECT_ROOT / "data" / "expected_labels.csv"
TRIAL_INPUT_PATH = PROJECT_ROOT / "data" / "trial_10.csv"

FIELDNAMES = [
    "record_id",
    "submitted_at",
    "source_department",
    "message_text",
    "extracted_action",
    "extracted_assignee",
    "extracted_deadline",
    "extracted_priority",
    "action_required",
]

RAW_FIELDNAMES = [
    "record_id",
    "submitted_at",
    "source_department",
    "message_text",
]

EXPECTED_FIELDNAMES = [
    "record_id",
    "expected_action",
    "expected_assignee",
    "expected_deadline",
    "expected_priority",
    "expected_action_required",
    "expected_audit_status",
]

DEPARTMENTS = [
    "製造",
    "事務",
    "介護",
    "物流",
    "総務",
    "設備",
    "訪問支援",
    "調達",
    "給食",
    "送迎",
]

ASSIGNEES = [
    "田中",
    "佐藤",
    "鈴木",
    "高橋",
    "山本",
    "中村",
    "小林",
    "加藤",
    "吉田",
    "山田",
]


# ============================================================
# 2. 最初に確認した6件
# ============================================================

INITIAL_ROWS: List[Dict[str, str]] = [
    {
        "record_id": "H001",
        "submitted_at": "2026-09-16 08:10",
        "source_department": "製造",
        "message_text": "田中さん、部品Aの在庫を本日15時までに確認してください",
        "extracted_action": "部品Aの在庫を確認する",
        "extracted_assignee": "田中",
        "extracted_deadline": "2026-09-16 15:00",
        "extracted_priority": "中",
        "action_required": "true",
    },
    {
        "record_id": "H002",
        "submitted_at": "2026-09-16 08:30",
        "source_department": "事務",
        "message_text": "請求書の金額に違いがあります。明日の午前中までに取引先へ確認してください",
        "extracted_action": "取引先へ請求金額を確認する",
        "extracted_assignee": "",
        "extracted_deadline": "2026-09-17 12:00",
        "extracted_priority": "中",
        "action_required": "true",
    },
    {
        "record_id": "H003",
        "submitted_at": "2026-09-16 09:00",
        "source_department": "介護",
        "message_text": "山本さんの受診時間が14時に変更になりました。共有のみです",
        "extracted_action": "",
        "extracted_assignee": "",
        "extracted_deadline": "",
        "extracted_priority": "低",
        "action_required": "false",
    },
    {
        "record_id": "H004",
        "submitted_at": "2026-09-16 09:20",
        "source_department": "物流",
        "message_text": "冷蔵便がまだ届いていません。佐藤さんが配送会社へ至急連絡",
        "extracted_action": "配送会社へ連絡する",
        "extracted_assignee": "佐藤",
        "extracted_deadline": "",
        "extracted_priority": "高",
        "action_required": "true",
    },
    {
        "record_id": "H005",
        "submitted_at": "2026-09-16 10:00",
        "source_department": "総務",
        "message_text": "会議室の照明が切れています。交換をお願いします",
        "extracted_action": "照明を交換する",
        "extracted_assignee": "",
        "extracted_deadline": "",
        "extracted_priority": "低",
        "action_required": "true",
    },
    {
        "record_id": "H006",
        "submitted_at": "2026-09-16 10:15",
        "source_department": "製造",
        "message_text": "鈴木さんが設備3号機の異音を11時までに確認する",
        "extracted_action": "設備3号機の異音を確認する",
        "extracted_assignee": "鈴木",
        "extracted_deadline": "2026-09-16 11:00",
        "extracted_priority": "高",
        "action_required": "true",
    },
]


# ============================================================
# 3. 追加する94件の作成
# ============================================================

def make_row(record_number: int) -> Dict[str, str]:
    """番号に応じて異なる種類の申し送りを1件作る。"""

    variant = record_number % 10
    department = DEPARTMENTS[(record_number - 1) % len(DEPARTMENTS)]
    assignee = ASSIGNEES[(record_number * 3) % len(ASSIGNEES)]
    item_number = (record_number % 8) + 1
    submitted = datetime(2026, 9, 16, 8, 0) + timedelta(
        days=(record_number - 1) // 20,
        minutes=(record_number * 17) % 600,
    )
    deadline = submitted.replace(hour=17, minute=0) + timedelta(days=record_number % 3)

    base = {
        "record_id": f"H{record_number:03d}",
        "submitted_at": submitted.strftime("%Y-%m-%d %H:%M"),
        "source_department": department,
        "message_text": "",
        "extracted_action": "",
        "extracted_assignee": "",
        "extracted_deadline": "",
        "extracted_priority": "中",
        "action_required": "true",
    }

    if variant == 0:
        base.update(
            message_text=f"{assignee}さん、備品{item_number}の残数を{deadline:%m月%d日%H時}までに確認してください",
            extracted_action=f"備品{item_number}の残数を確認する",
            extracted_assignee=assignee,
            extracted_deadline=deadline.strftime("%Y-%m-%d %H:%M"),
        )
    elif variant == 1:
        base.update(
            message_text=f"発注番号{1000 + record_number}の数量に相違があります。{deadline:%m月%d日}までに仕入先へ確認してください",
            extracted_action="仕入先へ発注数量を確認する",
            extracted_deadline=deadline.strftime("%Y-%m-%d %H:%M"),
        )
    elif variant == 2:
        base.update(
            message_text=f"明日の定例連絡は{10 + record_number % 5}時開始へ変更になりました。共有のみです",
            extracted_priority="低",
            action_required="false",
        )
    elif variant == 3:
        base.update(
            message_text=f"予定していた配送便{item_number}が到着していません。{assignee}さんが運送会社へ至急連絡してください",
            extracted_action=f"配送便{item_number}について運送会社へ連絡する",
            extracted_assignee=assignee,
            extracted_priority="高",
        )
    elif variant == 4:
        base.update(
            message_text=f"共用スペース{item_number}の照明が点滅しています。交換をお願いします",
            extracted_action=f"共用スペース{item_number}の照明を交換する",
            extracted_priority="低",
        )
    elif variant == 5:
        base.update(
            message_text=f"{assignee}さんが設備{item_number}号機の異音を{deadline:%m月%d日%H時}までに確認する",
            extracted_action=f"設備{item_number}号機の異音を確認する",
            extracted_assignee=assignee,
            extracted_deadline=deadline.strftime("%Y-%m-%d %H:%M"),
            extracted_priority="高",
        )
    elif variant == 6:
        base.update(
            message_text=f"{assignee}さん、例の件を{deadline:%m月%d日%H時}までにお願いします",
            extracted_assignee=assignee,
            extracted_deadline=deadline.strftime("%Y-%m-%d %H:%M"),
        )
    elif variant == 7:
        base.update(
            message_text=f"予備電源{item_number}の燃料残量が少なくなっています。補充の手配をお願いします",
            extracted_action=f"予備電源{item_number}の燃料補充を手配する",
            extracted_priority="高",
        )
    elif variant == 8:
        base.update(
            message_text=f"{assignee}さんが生活用品便{item_number}の受け入れ場所を{deadline:%m月%d日%H時}までに準備してください",
            extracted_action=f"生活用品便{item_number}の受け入れ場所を準備する",
            extracted_assignee=assignee,
            extracted_deadline=deadline.strftime("%Y-%m-%d %H:%M"),
        )
    else:
        base.update(
            message_text=f"{assignee}さん、連絡網{item_number}の更新内容を{deadline:%m月%d日%H時}までに確認してください",
            extracted_action=f"連絡網{item_number}の更新内容を確認する",
            extracted_assignee=assignee,
            extracted_deadline=deadline.strftime("%Y-%m-%d %H:%M"),
            extracted_priority="低",
        )

    return base


def build_rows() -> List[Dict[str, str]]:
    """初期6件を残し、合計100件のデータを作る。"""

    generated_rows = [make_row(number) for number in range(7, 101)]
    return INITIAL_ROWS + generated_rows


# ============================================================
# 4. 評価用データへの分離
# ============================================================

def expected_audit_status(row: Dict[str, str]) -> str:
    """人間が決めた正解として、期待する監査状態を返す。"""

    if row["action_required"] == "false":
        return "INFO_ONLY"

    required_values = (
        row["extracted_action"],
        row["extracted_assignee"],
        row["extracted_deadline"],
    )
    return "READY" if all(required_values) else "NEEDS_REVIEW"


def make_raw_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """AIへ渡す原文だけのデータを作る。"""

    return [
        {field_name: row[field_name] for field_name in RAW_FIELDNAMES}
        for row in rows
    ]


def make_expected_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """AIの回答を採点するための正解表を作る。"""

    return [
        {
            "record_id": row["record_id"],
            "expected_action": row["extracted_action"],
            "expected_assignee": row["extracted_assignee"],
            "expected_deadline": row["extracted_deadline"],
            "expected_priority": row["extracted_priority"],
            "expected_action_required": row["action_required"],
            "expected_audit_status": expected_audit_status(row),
        }
        for row in rows
    ]


# ============================================================
# 5. CSV出力
# ============================================================

def write_csv(
    path: Path,
    rows: List[Dict[str, str]],
    fieldnames: List[str],
) -> None:
    """Excelでも開きやすいUTF-8 BOM付きCSVへ保存する。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    """100件を生成し、原文・正解表・模擬AI回答へ分けて保存する。"""

    rows = build_rows()
    raw_rows = make_raw_rows(rows)
    expected_rows = make_expected_rows(rows)

    write_csv(RAW_INPUT_PATH, raw_rows, RAW_FIELDNAMES)
    write_csv(TRIAL_INPUT_PATH, raw_rows[:10], RAW_FIELDNAMES)
    write_csv(EXPECTED_LABELS_PATH, expected_rows, EXPECTED_FIELDNAMES)
    write_csv(MOCK_OUTPUT_PATH, rows, FIELDNAMES)

    print(f"生成完了: {len(rows)}件")
    print(f"AI入力: {RAW_INPUT_PATH}")
    print(f"10件試験: {TRIAL_INPUT_PATH}")
    print(f"正解表: {EXPECTED_LABELS_PATH}")
    print(f"模擬回答: {MOCK_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
