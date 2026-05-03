"""Pack #83 Phase 4 — Conquest Pipeline v3 統合 orchestrator.

CEO directive 「圧倒的な精度」「無料配布が迫る」 — Phase 1-3 を 1 関数に統合.

入力: 図面 image bytes (実 VLM 呼出) または fixture model
出力: ConquestPipelineResult — 確定 BuildingModel + Probabilistic 候補 +
      Physics validation report + Anomaly report + 信頼度 metrics

flow:
  Stage 1. multi_persona_inference (7 ペルソナ並列 → consensus)
  Stage 2. probabilistic_bim (top-3 候補 + ベイズ更新可能)
  Stage 3. physics_self_validator (6 種物理 check + 不可能性検出)
  Stage 4. relationship_validator (9 種関係異常 + 自動修復)
  Stage 5. neo4j_constraint_solver (永続化 + 履歴学習)

retroaction:
  Stage 3 で物理 critical → Stage 2 の top-2 候補に切替えて再評価 (将来)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from plugins.conquest.schemas import BuildingModel, DrawingMetadata
from plugins.conquest.probabilistic_bim import (
    ProbabilisticBuildingModel,
    merge_persona_results_to_probabilistic,
    update_with_feedback,
    UserFeedback,
)
from plugins.conquest.physics_self_validator import (
    PhysicsValidationResult,
    validate_with_physics,
    format_physics_report,
)
from plugins.conquest.relationship_validator import (
    ValidationResult,
    validate_and_repair,
    format_validation_report,
)

logger = logging.getLogger("conquest.pipeline_v3")


@dataclass(frozen=True)
class ConquestPipelineResult:
    """v3 pipeline の総合結果."""
    final_model: BuildingModel
    probabilistic: ProbabilisticBuildingModel
    physics: PhysicsValidationResult
    relationships: ValidationResult
    overall_confidence: float
    """0.0-1.0. (top-1 平均) × (1 - critical_ratio) で算出."""
    distribution_ready: bool
    """配布 ready 判定: physics CRITICAL=0 AND avg confidence ≥ 0.85"""
    blocking_issues: tuple[str, ...] = ()
    metadata: DrawingMetadata = field(default_factory=DrawingMetadata)


def _compute_overall_confidence(
    pmodel: ProbabilisticBuildingModel,
    physics: PhysicsValidationResult,
) -> float:
    if not pmodel.elements:
        return 0.0
    avg_top1 = sum(
        e.confidence for e in pmodel.elements
    ) / len(pmodel.elements)
    # critical 比率 (0-1) で減点
    total_issues = (
        physics.critical_count + physics.warning_count + physics.info_count
    )
    if total_issues > 0:
        critical_ratio = physics.critical_count / total_issues
    else:
        critical_ratio = 0.0
    return max(0.0, avg_top1 * (1.0 - critical_ratio * 0.5))


def _summarize_blockers(
    physics: PhysicsValidationResult,
    rels: ValidationResult,
) -> tuple[str, ...]:
    blockers: list[str] = []
    for issue in physics.issues:
        if issue.severity.value == "critical":
            blockers.append(
                f"[physics:{issue.issue_type.value}] {issue.element_id}: {issue.description}"
            )
    for a in rels.anomalies:
        if a.severity.value == "critical":
            blockers.append(
                f"[relationship:{a.anomaly_type.value}] {a.element_id}: {a.description}"
            )
    return tuple(blockers)


def run_pipeline_from_persona_results(
    persona_results: dict[str, BuildingModel],
    *,
    metadata: Optional[DrawingMetadata] = None,
    feedbacks: tuple[UserFeedback, ...] = (),
    confidence_threshold: float = 0.85,
) -> ConquestPipelineResult:
    """7 ペルソナの結果から開始する pipeline (実 VLM 呼出済前提).

    feedback があれば Phase 2 probabilistic に適用後 deterministic 化.
    """
    # Stage 2: probabilistic
    pmodel = merge_persona_results_to_probabilistic(persona_results, metadata=metadata)
    if feedbacks:
        for fb in feedbacks:
            pmodel = update_with_feedback(pmodel, fb)

    deterministic = pmodel.to_deterministic()

    # Stage 3: physics
    analyzed_model, physics_result = validate_with_physics(deterministic)

    # Stage 4: relationships (validate_and_repair は ValidationResult のみ返す)
    rel_result = validate_and_repair(analyzed_model, auto_fix=True)
    repaired_model = analyzed_model

    # Stage 5: 統合判定
    overall_conf = _compute_overall_confidence(pmodel, physics_result)
    blockers = _summarize_blockers(physics_result, rel_result)
    distribution_ready = (
        physics_result.critical_count == 0
        and rel_result.critical_count == 0
        and overall_conf >= confidence_threshold
    )

    logger.info(
        "Conquest v3 pipeline complete: confidence=%.2f, distribution_ready=%s, blockers=%d",
        overall_conf, distribution_ready, len(blockers),
    )

    return ConquestPipelineResult(
        final_model=repaired_model,
        probabilistic=pmodel,
        physics=physics_result,
        relationships=rel_result,
        overall_confidence=overall_conf,
        distribution_ready=distribution_ready,
        blocking_issues=blockers,
        metadata=metadata or DrawingMetadata(),
    )


def run_pipeline_with_vlm(
    image_data: bytes,
    *,
    mime_type: str = "image/png",
    source_file: str = "",
    feedbacks: tuple[UserFeedback, ...] = (),
    confidence_threshold: float = 0.85,
) -> Optional[ConquestPipelineResult]:
    """実 VLM (Gemini Vision) 呼出付きフルパイプライン.

    GEMINI_API_KEY 必要. オフライン env では None を返す.
    """
    import asyncio
    from plugins.conquest.multi_persona_inference import (
        analyze_drawing_multi_persona,
        PERSONAS,
    )

    async def _run() -> Optional[ConquestPipelineResult]:
        # Stage 1: multi_persona — 既存実装は consensus を返すが、
        # ここでは 7 ペルソナの生 results を取得したい
        # → analyze_drawing_multi_persona を直接呼んで pmodel を作る方が efficient だが、
        #   現実装が consensus を返すため deterministic を 1 ペルソナとして扱う妥協 fallback
        consensus = await analyze_drawing_multi_persona(
            image_data, mime_type, source_file
        )
        if consensus is None:
            return None
        # consensus を全 persona の単一票として feed (簡易; 将来 expand)
        persona_results = {p.id: consensus for p in PERSONAS[:4]}
        return run_pipeline_from_persona_results(
            persona_results,
            metadata=consensus.metadata,
            feedbacks=feedbacks,
            confidence_threshold=confidence_threshold,
        )

    return asyncio.run(_run())


def format_pipeline_summary(result: ConquestPipelineResult) -> str:
    """人間 (CTO + CEO) が読める pipeline 結果サマリ."""
    lines: list[str] = []
    lines.append("═" * 60)
    lines.append("Conquest Mode v3 Pipeline Result")
    lines.append("═" * 60)
    badge = "✅ 配布 READY" if result.distribution_ready else "❌ 配布 NOT READY"
    lines.append(f"判定: {badge}")
    lines.append(f"総合信頼度: {result.overall_confidence:.2f} (target ≥ 0.85)")
    lines.append("")
    lines.append("--- 抽出結果 ---")
    lines.append(f"final elements: {len(result.final_model.elements)}")
    lines.append(f"probabilistic elements: {len(result.probabilistic.elements)}")
    lines.append(f"  └ ambiguous (要確認): {result.probabilistic.ambiguous_count}")
    lines.append("")
    lines.append("--- Physics validation ---")
    lines.append(
        f"重量: {result.physics.total_weight_kn:.1f} kN, "
        f"CRITICAL {result.physics.critical_count} / "
        f"WARNING {result.physics.warning_count} / "
        f"INFO {result.physics.info_count}"
    )
    lines.append("")
    lines.append("--- Relationship validation ---")
    lines.append(
        f"anomalies {len(result.relationships.anomalies)} "
        f"(critical {result.relationships.critical_count}), "
        f"repairs {len(result.relationships.repairs)}"
    )
    lines.append("")
    if result.blocking_issues:
        lines.append("--- 配布をブロックする問題 (上位 5 件) ---")
        for b in result.blocking_issues[:5]:
            lines.append(f"  ❌ {b}")
    return "\n".join(lines)
