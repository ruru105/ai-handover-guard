"""やること抜けチェッカー V0.3 - 申し送り抽出結果の監査。"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from excel_report import excel_safe_csv_path, write_audit_xlsx, write_excel_safe_csv


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

# 期限に時刻がなく日付だけのとき(AIが日付のみで出力した場合など)の書式
DATE_ONLY_FORMAT = "%Y-%m-%d"

# 緊急度「高」は、文中の期限に関わらず、登録から何時間以内に着手すべきかの目安。
# サンプル値であり、実運用では業種・組織に合わせて調整する（--sla-hoursで上書き可能）。
DEFAULT_HIGH_PRIORITY_SLA_HOURS = 4.0

# 同じ担当者・同じ作業内容が、これより短い間隔で複数回登録されたら重複候補とする目安。
# サンプル値であり、実運用では調整する（--duplicate-window-hoursで上書き可能）。
DEFAULT_DUPLICATE_WINDOW_HOURS = 24.0


# ============================================================
# 2. 共通変換
# ============================================================

TRUE_VALUES = {"true", "1", "yes", "y"}
FALSE_VALUES = {"false", "0", "no", "n"}


def to_bool(value: str) -> bool:
    """CSVの真偽値表現をPythonのboolへ変換する。"""

    return value.strip().lower() in TRUE_VALUES


def parse_action_required(value: object) -> Optional[bool]:
    """対応要否を読み取る。true/falseのどちらにも読めない値(誤記・空欄)はNoneを返す。

    誤記を黙って「対応不要」として扱わないために、to_boolとは別に用意している。
    """

    text = str(value).strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return None


def parse_datetime(value: str) -> Optional[datetime]:
    """「YYYY-MM-DD HH:MM」形式の文字列をdatetimeへ変換する。読めない場合はNoneを返す。"""

    try:
        return datetime.strptime(value.strip(), DATETIME_FORMAT)
    except (ValueError, AttributeError):
        return None


def parse_deadline(value: str) -> Tuple[Optional[datetime], bool]:
    """期限の文字列を読み取り、(期限の日時, 日付のみだったか)を返す。読めない場合は(None, False)。

    「YYYY-MM-DD HH:MM」はその時刻を期限とする。時刻のない「YYYY-MM-DD」は、その日の終わり
    (翌日0時の直前)まで有効とする。時刻を勝手に決めて誤警告を出さないための扱い。
    """

    exact = parse_datetime(value)
    if exact is not None:
        return exact, False

    try:
        day = datetime.strptime(value.strip(), DATE_ONLY_FORMAT)
    except (ValueError, AttributeError):
        return None, False
    return day + timedelta(days=1) - timedelta(microseconds=1), True


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

    deadline_text = result.get("extracted_deadline", "").strip()
    deadline, date_only = parse_deadline(deadline_text)

    if result.get("rule_priority") == "高":
        # 緊急度「高」の判定には文中の期限を使わないが、書かれているのに読めない期限は
        # 見逃さない(読めない期限を黙って「期限内・問題なし」にしない)。
        unreadable = deadline is None and bool(deadline_text)
        note = f"(期限の書式を読み取れません。値: {deadline_text})" if unreadable else ""
        elapsed_hours = (as_of - submitted_at).total_seconds() / 3600
        if elapsed_hours > sla_hours:
            result["sla_audit_status"] = "NEEDS_REVIEW"
            result["sla_audit_message"] = (
                f"緊急度が高いのに登録から{elapsed_hours:.1f}時間"
                f"({sla_hours:g}時間以内が目安)着手されていません" + note
            )
        elif unreadable:
            result["sla_audit_status"] = "UNKNOWN"
            result["sla_audit_message"] = (
                "緊急度「高」の目安時間内ですが、期限の書式(YYYY-MM-DD HH:MM または YYYY-MM-DD)を"
                f"読み取れないため判定できません(値: {deadline_text})"
            )
        else:
            result["sla_audit_status"] = "ON_TIME"
            result["sla_audit_message"] = "緊急度「高」の目安時間内です"
        return

    if deadline is None and deadline_text:
        result["sla_audit_status"] = "UNKNOWN"
        result["sla_audit_message"] = (
            f"期限の書式(YYYY-MM-DD HH:MM または YYYY-MM-DD)を読み取れないため判定できません(値: {deadline_text})"
        )
    elif deadline is None:
        result["sla_audit_status"] = "NOT_APPLICABLE"
        result["sla_audit_message"] = "期限が未確定のため判定できません"
    elif as_of > deadline:
        result["sla_audit_status"] = "NEEDS_REVIEW"
        result["sla_audit_message"] = (
            "文中の期限(日付のみ)の日が終わっています" if date_only else "文中の期限を過ぎています"
        )
    else:
        result["sla_audit_status"] = "ON_TIME"
        result["sla_audit_message"] = (
            "文中の期限(日付のみ)の日の終わりまで有効です" if date_only else "文中の期限内です"
        )


# ============================================================
# 曖昧な作業内容の検出
# ============================================================

# 「例の件」のように、何を指すのか原文を読まないと分からない言い方
VAGUE_REFERENCE_WORDS = (
    "例の件", "あの件", "その件", "この件", "先日の件", "前回の件", "いつもの件", "さっきの件",
    "例のやつ", "あのやつ", "いつものやつ", "例の話", "あの話", "その話", "例のもの", "例のあれ",
    "例の", "あの", "その", "この", "いつもの", "さっきの", "先日の", "前回の",
    "あれ", "それ", "これ", "やつ",
)

# 動詞・依頼の言い回しだけで、何を対象にするのかが書かれていない言葉
GENERIC_ACTION_WORDS = (
    "お願いします", "お願い", "よろしくお願いします", "よろしく", "ください", "くださ", "頼む", "頼みます",
    "しておいて", "しておく", "しておきます", "やっておいて", "やっておく", "やっておきます",
    "対応", "確認", "連絡", "処理", "調整", "検討", "共有", "手配", "準備", "実施", "対処",
    "報告", "相談", "フォロー", "進める", "進めて", "やる", "やって", "します", "する", "して",
    "おく", "ます", "です", "ね", "よ",
)

_PARTICLES = "をにはがでともへのや"
_PUNCTUATION_PATTERN = r"[\s　。、，,.!！?？・「」『』()（）\[\]【】~〜ー-]+"


def _build_removal_pattern(words: Tuple[str, ...]) -> "re.Pattern[str]":
    ordered = sorted(words, key=len, reverse=True)
    return re.compile("|".join(re.escape(word) for word in ordered))


_VAGUE_REFERENCE_PATTERN = _build_removal_pattern(VAGUE_REFERENCE_WORDS)
_GENERIC_ACTION_PATTERN = _build_removal_pattern(GENERIC_ACTION_WORDS)


def detect_vague_action(action: str) -> Optional[str]:
    """作業内容が曖昧なら、理由の種類("reference" / "generic")を返す。具体的ならNone。

    「例の件」「あれをお願いします」のように指す対象が分からない言い方(reference)と、
    「確認する」「対応」のように動詞だけで何をするのか分からない言い方(generic)を見つける。
    指示語や動詞を取り除いたあとに、対象を表す言葉が何も残らなければ曖昧とみなす。
    「部品Aの在庫を確認する」のように、対象が書かれていれば曖昧とはみなさない。
    """

    text = re.sub(_PUNCTUATION_PATTERN, "", action or "")
    if not text:
        return None

    has_reference = _VAGUE_REFERENCE_PATTERN.search(text) is not None
    remainder = _VAGUE_REFERENCE_PATTERN.sub("", text)
    remainder = _GENERIC_ACTION_PATTERN.sub("", remainder)
    remainder = "".join(ch for ch in remainder if ch not in _PARTICLES)
    if remainder:
        return None
    return "reference" if has_reference else "generic"


def add_vague_audit(result: Dict[str, str]) -> None:
    """作業内容が、誰が読んでも実行できるほど具体的に書かれているかを確認する。

    項目の充足(audit_status)は「空欄でないか」だけを見るため、「例の件」のような
    空欄ではないが中身のない作業内容を通してしまう。それを別の判定として拾う。
    """

    if not to_bool(result.get("action_required", "")):
        result["vague_audit_status"] = "NOT_APPLICABLE"
        result["vague_audit_message"] = "対応不要のため対象外です"
        return

    action = result.get("extracted_action", "").strip()
    if not action:
        result["vague_audit_status"] = "NOT_APPLICABLE"
        result["vague_audit_message"] = "作業内容が未確定のため判定できません(項目の充足で確認します)"
        return

    kind = detect_vague_action(action)
    if kind == "reference":
        result["vague_audit_status"] = "NEEDS_REVIEW"
        result["vague_audit_message"] = (
            f"作業内容「{action}」は、指す対象が分かりません。何を・どの案件かを具体的にしてください"
        )
    elif kind == "generic":
        result["vague_audit_status"] = "NEEDS_REVIEW"
        result["vague_audit_message"] = (
            f"作業内容「{action}」は、動詞だけで対象が分かりません。何を対象にするかを具体的にしてください"
        )
    else:
        result["vague_audit_status"] = "CLEAR"
        result["vague_audit_message"] = "作業内容に、対象が具体的に書かれています"


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


def mark_action_required_unknown(result: Dict[str, str]) -> None:
    """対応要否がtrue/falseのどちらにも読めないとき、黙って対応不要にせず「要確認」にする。"""

    value = str(result.get("action_required", "")).strip()
    shown = f"「{value}」" if value else "空欄"
    reason = f"対応要否(action_required)が{shown}で、true / false のどちらにも読めないため判定できません"
    result["rule_priority"] = ""
    for prefix in ("priority", "sla", "duplicate"):
        result[f"{prefix}_audit_status"] = "UNKNOWN"
        result[f"{prefix}_audit_message"] = reason
    result["vague_audit_status"] = "NOT_APPLICABLE"
    result["vague_audit_message"] = "対応要否が不明のため判定できません"
    result["audit_status"] = "NEEDS_REVIEW"
    result["missing_fields"] = "対応要否"
    result["audit_message"] = f"要確認：{reason}"


OVERALL_REASON_LABELS = {
    "missing_fields": "項目不足",
    "action_required_unknown": "対応要否が不明",
    "overdue": "期限超過",
    "duplicate": "重複候補",
    "priority_mismatch": "優先度の不一致",
    "vague_action": "作業内容が曖昧",
    "unreadable": "日時の形式などが読めず判定不能",
}


def overall_reason_codes(result: Dict[str, str]) -> List[Tuple[str, str]]:
    """総合判定が「要確認」になる理由を、(コード, 表示文)の一覧で返す。

    overall_messageの文面と、APIの理由別件数の両方がこの関数を使う(理由の数え方を1か所にそろえるため)。
    """

    codes: List[Tuple[str, str]] = []
    if result.get("audit_status") == "NEEDS_REVIEW":
        missing = result.get("missing_fields", "")
        if missing == "対応要否":
            codes.append(("action_required_unknown", OVERALL_REASON_LABELS["action_required_unknown"]))
        else:
            codes.append(("missing_fields", f"項目不足({missing})"))
    if result.get("sla_audit_status") == "NEEDS_REVIEW":
        codes.append(("overdue", OVERALL_REASON_LABELS["overdue"]))
    if result.get("duplicate_audit_status") == "NEEDS_REVIEW":
        codes.append(("duplicate", OVERALL_REASON_LABELS["duplicate"]))
    if result.get("priority_audit_status") == "NEEDS_REVIEW":
        codes.append(("priority_mismatch", OVERALL_REASON_LABELS["priority_mismatch"]))
    if result.get("vague_audit_status") == "NEEDS_REVIEW":
        codes.append(("vague_action", OVERALL_REASON_LABELS["vague_action"]))
    if result.get("missing_fields", "") != "対応要否" and any(
        result.get(f"{prefix}_audit_status") == "UNKNOWN" for prefix in ("priority", "sla", "duplicate")
    ):
        codes.append(("unreadable", OVERALL_REASON_LABELS["unreadable"]))
    return codes


def add_overall_audit(result: Dict[str, str]) -> None:
    """総合判定(overall_status)を作る。

    audit_statusは「実行に必要な項目(作業内容・担当者・期限)がそろっているか」だけを表す。
    overall_statusは、それに加えて期限超過・重複候補・優先度の不一致・判定不能も見て、
    1つでもあれば「要確認」にする。対応不要の共有情報は INFO_ONLY のまま。
    """

    if result.get("audit_status") == "INFO_ONLY":
        result["overall_status"] = "INFO_ONLY"
        result["overall_message"] = "対応不要の共有情報です"
        return

    reasons = [label for _, label in overall_reason_codes(result)]

    if reasons:
        result["overall_status"] = "NEEDS_REVIEW"
        result["overall_message"] = "要確認：" + "・".join(reasons)
    else:
        result["overall_status"] = "READY"
        result["overall_message"] = "項目がそろい、期限超過・重複候補・優先度の不一致もありません"


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

    if parse_action_required(record.get("action_required", "")) is None:
        mark_action_required_unknown(result)
        add_overall_audit(result)
        return result

    add_priority_audit(result)
    add_sla_audit(result, as_of or datetime.now(), sla_hours)
    set_default_duplicate_audit(result)
    add_vague_audit(result)

    if not to_bool(record.get("action_required", "")):
        result["audit_status"] = "INFO_ONLY"
        result["missing_fields"] = ""
        result["audit_message"] = "対応不要の共有情報です"
        add_overall_audit(result)
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

    add_overall_audit(result)
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
    # 重複候補は複数件を比べたあとで決まるため、総合判定はここで作り直す
    for record in audited_records:
        add_overall_audit(record)
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
    overall_review_count = sum(
        record["overall_status"] == "NEEDS_REVIEW" for record in audited_records
    )
    print(f"項目不足の要確認: {needs_review_count}件")
    print(f"総合判定が要確認: {overall_review_count}件")
    print(f"期限超過の要確認: {sla_review_count}件")
    print(f"重複候補の要確認: {duplicate_review_count}件")
    print(f"出力先(CSV): {args.output.resolve()}")

    # 人が読むためのExcel版(日本語見出し・列幅調整つき)を同じ場所に作る
    xlsx_path = args.output.with_suffix(".xlsx")
    write_audit_xlsx(xlsx_path, audited_records)
    print(f"出力先(Excel): {xlsx_path.resolve()}")

    # Excelで直接開いても数式が実行されないCSV(危険な文字で始まる文字の前に「'」を付けた版)
    safe_csv_path = excel_safe_csv_path(args.output)
    write_excel_safe_csv(safe_csv_path, audited_records)
    print(f"出力先(Excelで開く用のCSV): {safe_csv_path.resolve()}")


if __name__ == "__main__":
    main()
