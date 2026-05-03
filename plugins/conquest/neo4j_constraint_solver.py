"""Pack #83 Phase 1 — Neo4j 物理制約 Knowledge Graph Solver.

CEO 直接指示: 「Neo4j 知識グラフによる物理制約解決」(今までになかった視点 ②)

設計:
  - 既存 relationship_validator.py が 9 種類の異常検知 + 自動修復を実装済
  - 本モジュールはその上に Neo4j 永続化 layer を被せる:
    1. PhysicsConstraint ルールを Neo4j Concept として seed
    2. 抽出 BuildingModel を validate → 違反箇所を (:RelationshipAnomaly) 永続化
    3. 修復 (auto_repair) 結果を (:RepairAction) 永続化
    4. 将来: anomaly 履歴から VLM prompt を改善 (Pack #83 Phase 2 候補)

Pack #79 Platform Knowledge Graph 思想:
  「グラフで構造を制約」原理を Conquest BIM に適用。
  Conquest 推論結果は固定値ではなく、過去の検知履歴から self-improve する.

依存:
  - neo4j-driver (既に Coworks で使用)
  - relationship_validator.py (既存 logic を call)

セキュリティ (Pack #79.5 audit からの学び):
  - withWriteSession 相当: write operation 専用 session を明示
  - Cypher パラメータ化必須 (injection 防御)
  - tenant_id を明示伝播 (multi-tenant 対応)
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Optional

from plugins.conquest.relationship_validator import (
    AnomalyType,
    BuildingModel,
    RelationshipAnomaly,
    RepairAction,
    Severity,
    ValidationResult,
    detect_anomalies,
    auto_repair,
    validate_and_repair,
)

logger = logging.getLogger("conquest.neo4j_constraint_solver")

# 既存 conquest 系で多重 import を避けるため遅延 import
def _get_driver():  # type: ignore[no-untyped-def]
    """Neo4j driver の遅延取得 (env 未設定なら None)."""
    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER", "neo4j")
    pwd = os.environ.get("NEO4J_PASSWORD")
    if not uri or not pwd:
        logger.warning("Neo4j env not configured — constraint solver runs offline (no persistence)")
        return None
    try:
        from neo4j import GraphDatabase
    except ImportError:
        logger.error("neo4j-driver not installed — pip install neo4j")
        return None
    return GraphDatabase.driver(uri, auth=(user, pwd), connection_timeout=8)


# ─── 物理制約ルールの Neo4j seed ──────────────────────────────

PHYSICS_CONSTRAINTS: tuple[dict[str, Any], ...] = (
    {
        "id": "physics:column_supported_by_foundation",
        "name": "1F 柱は基礎/フーチングに支持される",
        "description": "1F 柱が基礎に接続していない場合 = 浮遊柱 (FLOATING_COLUMN)",
        "anomaly_type": AnomalyType.FLOATING_COLUMN.value,
        "severity": Severity.CRITICAL.value,
        "physics_principle": "重力は連続的に伝達される — 浮遊する剛体は存在しない",
    },
    {
        "id": "physics:beam_supported_at_both_ends",
        "name": "梁は両端で柱/壁に支持される",
        "description": "梁の両端いずれかに柱がない場合 = 宙に浮く梁",
        "anomaly_type": AnomalyType.UNSUPPORTED_BEAM.value,
        "severity": Severity.CRITICAL.value,
        "physics_principle": "片持ち梁を除き、梁は両端モーメント支持が必要",
    },
    {
        "id": "physics:opening_within_wall",
        "name": "開口部 (ドア/窓) は壁の内側に host される",
        "description": "host_element 未設定の窓/ドア = 壁なし開口部",
        "anomaly_type": AnomalyType.HOMELESS_OPENING.value,
        "severity": Severity.WARNING.value,
        "physics_principle": "開口は壁・床・屋根いずれかの面要素を貫く必要がある",
    },
    {
        "id": "physics:column_continuous_storeys",
        "name": "上下階の柱は連続する",
        "description": "ある階で消失する柱 = 構造的に不連続 (BROKEN_COLUMN)",
        "anomaly_type": AnomalyType.BROKEN_COLUMN.value,
        "severity": Severity.CRITICAL.value,
        "physics_principle": "transfer beam なしに荷重経路を切り替えできない",
    },
    {
        "id": "physics:load_path_intact",
        "name": "荷重経路は屋根 → 梁 → 柱 → 基礎まで途切れない",
        "description": "途中要素欠落 = LOAD_PATH_BREAK",
        "anomaly_type": AnomalyType.LOAD_PATH_BREAK.value,
        "severity": Severity.CRITICAL.value,
        "physics_principle": "重力荷重は地盤に到達するまで途切れてはならない",
    },
    {
        "id": "physics:space_must_be_bounded",
        "name": "空間は壁・床・天井で囲まれる",
        "description": "境界 element が不足 = UNBOUNDED_SPACE",
        "anomaly_type": AnomalyType.UNBOUNDED_SPACE.value,
        "severity": Severity.INFO.value,
        "physics_principle": "建築空間は境界面の閉曲面で定義される",
    },
)


def seed_physics_constraints(*, tenant_id: str = "default") -> int:
    """Neo4j に物理制約ルールを (:PhysicsConstraint) ノードとして MERGE.

    Returns: seed された ノード数 (offline なら 0).
    """
    driver = _get_driver()
    if driver is None:
        return 0
    n = 0
    try:
        # WRITE session (Pack #79.5 audit fix を反映)
        with driver.session(default_access_mode="WRITE") as session:
            for c in PHYSICS_CONSTRAINTS:
                session.run(
                    """
                    MERGE (pc:PhysicsConstraint {id: $id})
                    SET pc.name = $name,
                        pc.description = $description,
                        pc.anomaly_type = $anomaly_type,
                        pc.severity = $severity,
                        pc.physics_principle = $principle,
                        pc.tenant_id = $tenant_id,
                        pc.updated_at = datetime()
                    """,
                    id=c["id"],
                    name=c["name"],
                    description=c["description"],
                    anomaly_type=c["anomaly_type"],
                    severity=c["severity"],
                    principle=c["physics_principle"],
                    tenant_id=tenant_id,
                )
                n += 1
        logger.info("seeded %d :PhysicsConstraint nodes", n)
    finally:
        driver.close()
    return n


# ─── 異常 → Neo4j 永続化 ─────────────────────────────────────

def _anomaly_unique_key(anomaly: RelationshipAnomaly, model_id: str) -> str:
    """anomaly の冪等 ID. 同一 model + 同一 element + 同一 type → 1 ノード."""
    elem_id = anomaly.element_id if anomaly.element_id else "no_elem"
    return f"{model_id}:{elem_id}:{anomaly.anomaly_type.value}"


def persist_validation_result(
    model: BuildingModel,
    result: ValidationResult,
    *,
    model_id: Optional[str] = None,
    tenant_id: str = "default",
) -> dict[str, int]:
    """ValidationResult を Neo4j に永続化.

    永続化対象:
      - (:RelationshipAnomaly) 各 anomaly
      - (:RepairAction) 修復アクション (auto_repair で生成された場合)
      - (:RelationshipAnomaly)-[:VIOLATES]->(:PhysicsConstraint) 関係

    Returns:
        {"anomalies": N, "repairs": M}
    """
    driver = _get_driver()
    if driver is None:
        return {"anomalies": 0, "repairs": 0, "persisted": 0}

    mid = model_id or model.source_file or f"unknown_{datetime.now(timezone.utc).isoformat()}"
    counts = {"anomalies": 0, "repairs": 0}

    try:
        with driver.session(default_access_mode="WRITE") as session:
            # anomalies
            for a in result.anomalies:
                key = _anomaly_unique_key(a, mid)
                session.run(
                    """
                    MERGE (ra:RelationshipAnomaly {id: $id})
                    SET ra.anomaly_type = $type,
                        ra.severity = $severity,
                        ra.element_id = $element_id,
                        ra.description = $description,
                        ra.related_elements = $related,
                        ra.auto_fixable = $auto_fixable,
                        ra.fix_description = $fix_description,
                        ra.model_id = $model_id,
                        ra.tenant_id = $tenant_id,
                        ra.detected_at = datetime()
                    WITH ra
                    OPTIONAL MATCH (pc:PhysicsConstraint {anomaly_type: $type, tenant_id: $tenant_id})
                    FOREACH (_ IN CASE WHEN pc IS NULL THEN [] ELSE [1] END |
                      MERGE (ra)-[:VIOLATES]->(pc)
                    )
                    """,
                    id=key,
                    type=a.anomaly_type.value,
                    severity=a.severity.value,
                    element_id=a.element_id,
                    description=a.description,
                    related=list(a.related_elements),
                    auto_fixable=a.auto_fixable,
                    fix_description=a.fix_description,
                    model_id=mid,
                    tenant_id=tenant_id,
                )
                counts["anomalies"] += 1

            # repairs
            for r in result.repairs:
                rid = f"{mid}:repair:{r.element_id}:{r.anomaly_type.value}"
                session.run(
                    """
                    MERGE (rp:RepairAction {id: $id})
                    SET rp.anomaly_type = $atype,
                        rp.element_id = $element_id,
                        rp.action = $action,
                        rp.confidence = $confidence,
                        rp.before = $before,
                        rp.after = $after,
                        rp.applied_at = datetime(),
                        rp.model_id = $model_id,
                        rp.tenant_id = $tenant_id
                    """,
                    id=rid,
                    atype=r.anomaly_type.value,
                    element_id=r.element_id,
                    action=r.action,
                    confidence=r.confidence,
                    before=r.before,
                    after=r.after,
                    model_id=mid,
                    tenant_id=tenant_id,
                )
                counts["repairs"] += 1
    finally:
        driver.close()

    logger.info(
        "Neo4j persist: model=%s anomalies=%d repairs=%d",
        mid, counts["anomalies"], counts["repairs"],
    )
    return counts


def validate_with_neo4j_persistence(
    model: BuildingModel,
    *,
    model_id: Optional[str] = None,
    tenant_id: str = "default",
    auto_fix: bool = True,
) -> tuple[BuildingModel, ValidationResult, dict[str, int]]:
    """validate_and_repair の結果を Neo4j に永続化するラッパー.

    Note: relationship_validator.validate_and_repair は ValidationResult のみ
    返す (model は in-place 修正でなく log のみ). model はそのまま返す.
    """
    result = validate_and_repair(model, auto_fix=auto_fix)
    counts = persist_validation_result(
        model, result, model_id=model_id, tenant_id=tenant_id,
    )
    return model, result, counts


# ─── learning loop (Phase 2 でフルに) ───────────────────────────

def get_anomaly_history(*, tenant_id: str = "default", limit: int = 100) -> list[dict[str, Any]]:
    """過去 anomaly 履歴を Neo4j から取得 (将来 VLM prompt 改善学習用)."""
    driver = _get_driver()
    if driver is None:
        return []
    out: list[dict[str, Any]] = []
    try:
        with driver.session(default_access_mode="READ") as session:
            r = session.run(
                """
                MATCH (ra:RelationshipAnomaly {tenant_id: $tenant_id})
                OPTIONAL MATCH (ra)-[:VIOLATES]->(pc:PhysicsConstraint)
                RETURN ra.anomaly_type AS type, ra.severity AS severity,
                       ra.description AS description, ra.auto_fixable AS auto_fixable,
                       ra.fix_description AS fix_description,
                       ra.model_id AS model, toString(ra.detected_at) AS detected_at,
                       pc.physics_principle AS principle
                ORDER BY ra.detected_at DESC
                LIMIT $limit
                """,
                tenant_id=tenant_id, limit=limit,
            )
            for rec in r:
                out.append({
                    "type": rec["type"],
                    "severity": rec["severity"],
                    "description": rec["description"],
                    "auto_fixable": rec["auto_fixable"],
                    "fix_description": rec["fix_description"],
                    "model": rec["model"],
                    "detected_at": rec["detected_at"],
                    "principle": rec["principle"],
                })
    finally:
        driver.close()
    return out


def get_top_anomaly_types(*, tenant_id: str = "default") -> list[tuple[str, int]]:
    """頻出 anomaly type を取得 — VLM prompt の重点ヒントに使う."""
    driver = _get_driver()
    if driver is None:
        return []
    out: list[tuple[str, int]] = []
    try:
        with driver.session(default_access_mode="READ") as session:
            r = session.run(
                """
                MATCH (ra:RelationshipAnomaly {tenant_id: $tenant_id})
                RETURN ra.anomaly_type AS type, count(*) AS n
                ORDER BY n DESC LIMIT 10
                """,
                tenant_id=tenant_id,
            )
            for rec in r:
                out.append((rec["type"], int(rec["n"])))
    finally:
        driver.close()
    return out
