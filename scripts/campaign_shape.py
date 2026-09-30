"""候補では不明値を保持し、採用・配信では元の必須条件を維持する。"""
from copy import deepcopy

from jsonschema import Draft202012Validator

from common import SCHEMA_DIR, load_json

_PUBLIC_SCHEMA = load_json(SCHEMA_DIR / "campaign.schema.json")
PUBLIC_VALIDATOR = Draft202012Validator(_PUBLIC_SCHEMA)
_CANDIDATE_SCHEMA = deepcopy(_PUBLIC_SCHEMA)
for key in ("start", "end"):
    _CANDIDATE_SCHEMA["properties"]["period"]["properties"][key]["type"] = ["string", "null"]
_CANDIDATE_SCHEMA["properties"]["entry"]["properties"]["required"]["type"] = ["boolean", "null"]
CANDIDATE_VALIDATOR = Draft202012Validator(_CANDIDATE_SCHEMA)


def publication_errors(c: dict) -> list[str]:
    """配信には候補用の緩和を使わない。壊れた型や必須値の不足も拒否する。"""
    return [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}"
            for e in PUBLIC_VALIDATOR.iter_errors(c)]


def unknown_required_fields(c: dict) -> list[str]:
    """候補の形が正常であることを確認した後に呼ぶ。"""
    reasons = []
    if c["entry"]["required"] is None:
        reasons.append("V02 エントリー要否が不明（entry.required）")
    for key, label in (("start", "開始日時"), ("end", "終了日時")):
        if c["period"][key] is None:
            reasons.append(f"V02 {label}が不明（period.{key}）")
    return reasons
