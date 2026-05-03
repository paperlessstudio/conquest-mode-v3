"""Pack #83 Phase 2 — UserFeedback の Neo4j 永続化 + 履歴学習.

probabilistic_bim.py の UserFeedback は in-memory で扱うが、
将来の VLM prompt 改善学習のため Neo4j に蓄積する.

設計:
  - (:UserFeedback) ノード: canonical_id / observed_type / strength / user / timestamp
  - (:UserFeedback)-[:CORRECTS]->(:RelationshipAnomaly) (anomaly があれば連動)
  - get_feedback_history() で過去 feedback を取得 → top-3 prior の sanity check に使う

セキュリティ (Pack #79.5 audit 反映):
  - WRITE session 必須
  - tenant_id 明示
  - user 識別は session 経由で取得 (route 側で auth 確認済前提)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from plugins.conquest.probabilistic_bim import UserFeedback
from plugins.conquest.schemas import ElementType

logger = logging.getLogger("conquest.feedback_persistence")


def _get_driver():  # type: ignore[no-untyped-def]
    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER", "neo4j")
    pwd = os.environ.get("NEO4J_PASSWORD")
    if not uri or not pwd:
        return None
    try:
        from neo4j import GraphDatabase
    except ImportError:
        return None
    return GraphDatabase.driver(uri, auth=(user, pwd), connection_timeout=8)


def persist_feedback(
    feedback: UserFeedback,
    *,
    model_id: str = "unknown",
    tenant_id: str = "default",
) -> bool:
    """単一 feedback を Neo4j に永続化."""
    driver = _get_driver()
    if driver is None:
        return False
    try:
        with driver.session(default_access_mode="WRITE") as session:
            fb_id = (
                f"{tenant_id}:{model_id}:{feedback.canonical_id}:"
                f"{feedback.observed_type.value}:{feedback.timestamp or 'no_ts'}"
            )
            session.run(
                """
                MERGE (uf:UserFeedback {id: $id})
                SET uf.canonical_id = $cid,
                    uf.observed_type = $observed,
                    uf.strength = $strength,
                    uf.user = $user,
                    uf.model_id = $model_id,
                    uf.tenant_id = $tenant_id,
                    uf.recorded_at = datetime(),
                    uf.user_timestamp = $ts
                WITH uf
                OPTIONAL MATCH (ra:RelationshipAnomaly {model_id: $model_id, tenant_id: $tenant_id})
                  WHERE ra.element_id = $cid OR $cid IN coalesce(ra.related_elements, [])
                FOREACH (_ IN CASE WHEN ra IS NULL THEN [] ELSE [1] END |
                  MERGE (uf)-[:CORRECTS]->(ra)
                )
                """,
                id=fb_id,
                cid=feedback.canonical_id,
                observed=feedback.observed_type.value,
                strength=feedback.strength,
                user=feedback.user,
                model_id=model_id,
                tenant_id=tenant_id,
                ts=feedback.timestamp,
            )
        logger.info("UserFeedback persisted: %s", fb_id)
        return True
    finally:
        driver.close()


def persist_feedback_batch(
    feedbacks: tuple[UserFeedback, ...],
    *,
    model_id: str = "unknown",
    tenant_id: str = "default",
) -> int:
    """複数 feedback を batch persist. Returns success count."""
    n = 0
    for fb in feedbacks:
        if persist_feedback(fb, model_id=model_id, tenant_id=tenant_id):
            n += 1
    return n


def get_feedback_history(
    *,
    canonical_id: Optional[str] = None,
    tenant_id: str = "default",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """過去 feedback を取得 (canonical_id 指定で絞込可)."""
    driver = _get_driver()
    if driver is None:
        return []
    out: list[dict[str, Any]] = []
    try:
        with driver.session(default_access_mode="READ") as session:
            if canonical_id:
                r = session.run(
                    """
                    MATCH (uf:UserFeedback {canonical_id: $cid, tenant_id: $tenant_id})
                    RETURN uf.id AS id, uf.observed_type AS observed,
                           uf.strength AS strength, uf.user AS user,
                           toString(uf.recorded_at) AS recorded_at
                    ORDER BY uf.recorded_at DESC LIMIT $limit
                    """,
                    cid=canonical_id, tenant_id=tenant_id, limit=limit,
                )
            else:
                r = session.run(
                    """
                    MATCH (uf:UserFeedback {tenant_id: $tenant_id})
                    RETURN uf.id AS id, uf.canonical_id AS cid,
                           uf.observed_type AS observed, uf.strength AS strength,
                           uf.user AS user, toString(uf.recorded_at) AS recorded_at
                    ORDER BY uf.recorded_at DESC LIMIT $limit
                    """,
                    tenant_id=tenant_id, limit=limit,
                )
            for rec in r:
                out.append({k: rec[k] for k in rec.keys()})
    finally:
        driver.close()
    return out


def feedback_to_persistent(feedback: dict[str, Any]) -> Optional[UserFeedback]:
    """Neo4j 取得結果を UserFeedback dataclass に変換."""
    try:
        return UserFeedback(
            canonical_id=feedback.get("cid", feedback.get("canonical_id", "")),
            observed_type=ElementType(feedback["observed"]),
            strength=float(feedback.get("strength", 0.5)),
            user=str(feedback.get("user", "unknown")),
            timestamp=feedback.get("recorded_at", "") or "",
        )
    except (KeyError, ValueError):
        return None


def get_top_correction_patterns(
    *, tenant_id: str = "default", limit: int = 10,
) -> list[tuple[str, str, int]]:
    """過去 feedback から「VLM が誤判定する典型パターン」を抽出.

    Returns: [(observed_type, vlm_predicted_first, count)]
    将来 VLM prompt の "be careful with X vs Y" として使う.
    """
    driver = _get_driver()
    if driver is None:
        return []
    out: list[tuple[str, str, int]] = []
    try:
        with driver.session(default_access_mode="READ") as session:
            r = session.run(
                """
                MATCH (uf:UserFeedback {tenant_id: $tenant_id})
                  -[:CORRECTS]->(ra:RelationshipAnomaly)
                RETURN uf.observed_type AS user_obs, ra.anomaly_type AS vlm_label,
                       count(*) AS n
                ORDER BY n DESC LIMIT $limit
                """,
                tenant_id=tenant_id, limit=limit,
            )
            for rec in r:
                out.append((rec["user_obs"], rec["vlm_label"], int(rec["n"])))
    finally:
        driver.close()
    return out
