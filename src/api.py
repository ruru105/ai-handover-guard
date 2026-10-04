"""やること抜けチェッカー V0.7 - FastAPIによるAPIサーバー。

提供するAPI:
  GET  /health   : 動作確認
  POST /audit    : AI抽出済みの申し送りを監査する(追加課金なし)
  POST /overdue  : 監査したうえで、期限超過(要確認)の分だけを返す(追加課金なし)
  POST /extract  : 申し送りの原文からAIで「やること・担当・期限」を取り出す(有料API)
  POST /history/audit : 保存済みの履歴とも比べて監査し、新しい記録を保存する(追加課金なし)
  GET  /history  : 保存している件数

起動(作品のフォルダで):
  python -m uvicorn api:app --app-dir src

APIキーはサーバー側の.envにだけ置き、リクエストやレスポンスには出しません。
認証機能は付けていないため、自分のパソコン内(127.0.0.1)での利用に限ってください。
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Union

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from audit_rules import (
    DATETIME_FORMAT,
    DEFAULT_DUPLICATE_WINDOW_HOURS,
    DEFAULT_HIGH_PRIORITY_SLA_HOURS,
    OVERALL_REASON_LABELS,
    audit_records,
    overall_reason_codes,
    parse_action_required,
)
import history_store
from history_store import HistoryStore, audit_with_history, validate_record_ids
from llm_extractor import (
    DEFAULT_MODEL,
    create_openai_client,
    estimate_luna_cost_usd,
    extract_records,
)


# ============================================================
# 1. 安全設定
# ============================================================

MAX_AUDIT_RECORDS = 500
MAX_EXTRACT_RECORDS = 10

app = FastAPI(
    title="やること抜けチェッカー API",
    description="申し送りの抜け・期限超過・重複候補を監査し、原文からの抽出もできる。",
    version="0.7.0",
)


# ============================================================
# 2. リクエストの形
# ============================================================

class ExtractedRecord(BaseModel):
    """AI抽出済みの1件(監査の入力)。"""

    record_id: str = Field(min_length=1)
    submitted_at: str = Field(description="登録日時。YYYY-MM-DD HH:MM")
    source_department: str = ""
    message_text: str = ""
    extracted_action: str = ""
    extracted_assignee: str = ""
    extracted_deadline: str = ""
    extracted_priority: str = Field(default="", description="低 / 中 / 高")
    action_required: Union[bool, str] = Field(description="対応が必要か(true / false)")

    @field_validator("action_required")
    @classmethod
    def check_action_required(cls, value: Union[bool, str]) -> Union[bool, str]:
        """true/falseのどちらにも読めない値(誤記・空欄)は、黙って対応不要にせず受け付けない。"""

        if parse_action_required(value) is None:
            raise ValueError("action_requiredは true / false のどちらかで指定してください")
        return value


class AuditRequest(BaseModel):
    records: List[ExtractedRecord] = Field(min_length=1, max_length=MAX_AUDIT_RECORDS)
    as_of: Optional[str] = Field(
        default=None,
        description="期限超過を判定する基準日時(YYYY-MM-DD HH:MM)。省略時は現在時刻",
    )
    sla_hours: float = Field(default=DEFAULT_HIGH_PRIORITY_SLA_HOURS, gt=0)
    duplicate_window_hours: float = Field(default=DEFAULT_DUPLICATE_WINDOW_HOURS, gt=0)

    @model_validator(mode="after")
    def check_record_ids(self) -> "AuditRequest":
        """同じ回の中で、record_idが重複していないこと(重複すると結果や履歴が混ざる)。"""

        try:
            validate_record_ids([{"record_id": record.record_id} for record in self.records])
        except ValueError as error:
            raise ValueError(str(error))
        return self


class HistoryAuditRequest(AuditRequest):
    save: bool = Field(
        default=True,
        description="監査のあとで、今回の記録を履歴に保存する(falseなら保存せず、比べるだけ)",
    )


class RawRecord(BaseModel):
    """申し送りの原文1件(AI抽出の入力)。"""

    record_id: str = Field(min_length=1)
    submitted_at: str = Field(description="登録日時。YYYY-MM-DD HH:MM")
    source_department: str = ""
    message_text: str = Field(min_length=1)


class ExtractRequest(BaseModel):
    records: List[RawRecord] = Field(min_length=1, max_length=MAX_EXTRACT_RECORDS)
    model: str = DEFAULT_MODEL
    confirm_paid_api: bool = Field(
        default=False,
        description="有料のAPI通信を実行してよい場合だけtrueにする",
    )


# ============================================================
# 3. 共通処理
# ============================================================

def parse_as_of(value: Optional[str]) -> datetime:
    """基準日時の文字列を読み取る。省略時は現在時刻。"""

    if value is None:
        return datetime.now()
    try:
        return datetime.strptime(value.strip(), DATETIME_FORMAT)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="as_ofは「YYYY-MM-DD HH:MM」の形式で指定してください",
        )


def to_audit_input(record: ExtractedRecord) -> Dict[str, str]:
    """リクエストの1件を、監査ルールが読む文字列だけの辞書へ変換する。"""

    data = record.model_dump()
    data["action_required"] = str(data["action_required"]).lower()
    return {key: str(value) for key, value in data.items()}


def run_audit_request(request: AuditRequest) -> tuple[datetime, List[Dict[str, str]]]:
    """監査リクエストを実行し、(基準日時, 監査結果)を返す。"""

    as_of = parse_as_of(request.as_of)
    results = audit_records(
        [to_audit_input(record) for record in request.records],
        as_of,
        request.sla_hours,
        request.duplicate_window_hours,
    )
    return as_of, results


def count_status(results: List[Dict[str, str]], field: str, status: str) -> int:
    """指定の判定項目が、指定の状態になっている件数を数える。"""

    return sum(result.get(field) == status for result in results)


# ============================================================
# 4. エンドポイント
# ============================================================

@app.get("/health")
def health() -> Dict[str, str]:
    """動作確認用。"""

    return {"status": "ok", "version": app.version}


def count_overall_reasons(results: List[Dict[str, str]]) -> Dict[str, int]:
    """総合判定が要確認になった理由別の件数(1件が複数の理由に数えられることがある)。"""

    counts = {code: 0 for code in OVERALL_REASON_LABELS}
    for result in results:
        if result.get("overall_status") != "NEEDS_REVIEW":
            continue
        for code, _ in overall_reason_codes(result):
            counts[code] += 1
    return counts


def summarize(results: List[Dict[str, str]]) -> Dict[str, object]:
    """監査結果の件数のまとめ。

    ready / needs_review / info_only は「項目の充足」(audit_status)の件数。
    overall_ready / overall_needs_review が、期限超過・重複・優先度の不一致なども含めた総合判定の件数。
    overall_reasons は、総合判定が要確認になった理由別の件数(1件が複数の理由に数えられることがある)。
    """

    return {
        "ready": count_status(results, "audit_status", "READY"),
        "needs_review": count_status(results, "audit_status", "NEEDS_REVIEW"),
        "info_only": count_status(results, "audit_status", "INFO_ONLY"),
        "overall_ready": count_status(results, "overall_status", "READY"),
        "overall_needs_review": count_status(results, "overall_status", "NEEDS_REVIEW"),
        "overdue": count_status(results, "sla_audit_status", "NEEDS_REVIEW"),
        "duplicate_candidates": count_status(results, "duplicate_audit_status", "NEEDS_REVIEW"),
        "priority_mismatch": count_status(results, "priority_audit_status", "NEEDS_REVIEW"),
        "overall_reasons": count_overall_reasons(results),
    }


@app.post("/audit")
def audit(request: AuditRequest) -> Dict[str, object]:
    """AI抽出済みの申し送りを監査する。追加課金はない。履歴の保存・参照はしない。"""

    as_of, results = run_audit_request(request)
    return {
        "as_of": as_of.strftime(DATETIME_FORMAT),
        "count": len(results),
        "summary": summarize(results),
        "results": results,
    }


@app.post("/history/audit")
def history_audit(request: HistoryAuditRequest) -> Dict[str, object]:
    """保存済みの履歴とも比べて監査する(重複候補は過去分ともまたがって調べる)。

    saveがtrueなら、監査のあとで今回の記録を履歴に保存する(同じrecord_idは置き換え)。
    追加課金はない。
    """

    as_of = parse_as_of(request.as_of)
    store = HistoryStore(history_store.DEFAULT_DB_PATH)
    results, history_compared = audit_with_history(
        store,
        [to_audit_input(record) for record in request.records],
        as_of,
        request.sla_hours,
        request.duplicate_window_hours,
        save=request.save,
    )
    return {
        "as_of": as_of.strftime(DATETIME_FORMAT),
        "count": len(results),
        "history_compared": history_compared,
        "saved": request.save,
        "stored_total": store.count(),
        "summary": summarize(results),
        "results": results,
    }


@app.get("/history")
def history() -> Dict[str, int]:
    """保存している履歴の件数。"""

    return {"count": HistoryStore(history_store.DEFAULT_DB_PATH).count()}


@app.post("/overdue")
def overdue(request: AuditRequest) -> Dict[str, object]:
    """監査したうえで、期限超過(要確認)の分だけを返す。追加課金はない。"""

    as_of, results = run_audit_request(request)
    overdue_items = [
        {
            "record_id": result["record_id"],
            "submitted_at": result["submitted_at"],
            "extracted_assignee": result["extracted_assignee"],
            "extracted_action": result["extracted_action"],
            "extracted_deadline": result["extracted_deadline"],
            "rule_priority": result["rule_priority"],
            "sla_audit_message": result["sla_audit_message"],
        }
        for result in results
        if result.get("sla_audit_status") == "NEEDS_REVIEW"
    ]
    return {
        "as_of": as_of.strftime(DATETIME_FORMAT),
        "total_records": len(results),
        "count": len(overdue_items),
        "overdue": overdue_items,
    }


@app.post("/extract")
def extract(request: ExtractRequest) -> Dict[str, object]:
    """申し送りの原文からAIで抽出する。有料API通信を行う。

    安全のため、confirm_paid_apiがtrueでなければ通信しない。1回あたりの件数にも上限がある。
    """

    if not request.confirm_paid_api:
        raise HTTPException(
            status_code=400,
            detail="有料のAPI通信です。実行する場合は confirm_paid_api を true にしてください",
        )

    try:
        client = create_openai_client()
    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail="サーバー側でAPIキーが設定されていません(.envを確認してください)",
        )

    rows = [record.model_dump() for record in request.records]
    try:
        extracted, input_tokens, output_tokens = extract_records(
            client, rows, request.model
        )
    except Exception:
        # 例外の中身(キーや通信内容を含む可能性)は返さない
        raise HTTPException(status_code=502, detail="AIによる抽出に失敗しました")

    cost_usd: Optional[float] = (
        estimate_luna_cost_usd(input_tokens, output_tokens)
        if request.model == DEFAULT_MODEL
        else None
    )
    return {
        "model": request.model,
        "count": len(extracted),
        "records": extracted,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": cost_usd,
        },
    }
