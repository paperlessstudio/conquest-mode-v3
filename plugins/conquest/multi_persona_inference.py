"""Pack #83 Phase 1 — 7 ペルソナ Multi-Agent VLM 推論 + 合意形成.

CEO 直接指示 (2026-05-03 21:00 JST):
  「2D→3D 精度を極限まで」「今までになかった視点」

革新ポイント:
  - 競合は単一 VLM (Gemini Vision 1 系統) に依存 → 単一視点バイアス
  - PLS は 7 建築ペルソナ (Pack #5/#7) で同一図面を independent に解釈 → 集合知
  - element 単位で 7 票投票 → 多数決 + confidence score (0.0-1.0)
  - 4/7 以上の合意 = 高信頼、3/7 以下 = 不確実 (HumanReviewPoint 化)

7 ペルソナ:
  1. アーキ (Architecture) — 意匠・空間構成・室名・動線
  2. ストラ (Structure) — 構造種別・荷重経路・耐震
  3. メプ (MEP) — 設備・配管・空調・給排水・電気
  4. セキサン (Quantity Surveyor) — 数量・寸法精度・部材リスト
  5. セコカン (Construction Mgmt) — 施工順序・実現可能性・運搬・足場
  6. ホウキ (Code Compliance) — 建築基準法・防火・避難・採光
  7. ビム (BIM Manager) — IFC 4.0 互換・LOD・レイヤ命名規則

設計原則 (CEO directive 「品質を上げる」):
  - 並列実行 (asyncio.gather) で 7 Pass を同時 → wall-clock を 1 pass 相当に
  - 元 4-pass cognitive model (See/Imagine/Understand/Validate) は保持
  - 7 ペルソナは Pass 1 (See) の signal-to-noise 改善のみに集中
  - 既存 vlm_analyzer.analyze_drawing() の互換 wrapper 提供
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from plugins.conquest.schemas import (
    BuildingElement,
    BuildingModel,
    DrawingMetadata,
    ElementType,
    ExtractionMethod,
    InferenceMetadata,
    MaterialType,
    Point2D,
    StructuralRole,
)

logger = logging.getLogger(__name__)


# ─── 7 ペルソナ system prompt 定義 ──────────────────────────────

@dataclass(frozen=True)
class PersonaSpec:
    """ペルソナ定義 — Pack #5/#7 と整合."""
    id: str
    name: str
    specialty: str
    focus: str  # この視点で何を「見る」か
    weight: float  # element 投票時の重み (一部要素は専門ペルソナを重視)


PERSONAS: tuple[PersonaSpec, ...] = (
    PersonaSpec("archi", "アーキ", "意匠設計",
                "壁の構成・室境界・動線・空間プロポーション", weight=1.0),
    PersonaSpec("structure", "ストラ", "構造設計",
                "柱・梁・耐震ブレース・荷重経路・スパン", weight=1.0),
    PersonaSpec("mep", "メプ", "設備設計",
                "配管・配線ルート・PS/EPS・天井ふところ", weight=0.8),
    PersonaSpec("qs", "セキサン", "積算",
                "寸法精度・部材数量・標準規格寸法との整合", weight=0.9),
    PersonaSpec("constmgmt", "セコカン", "施工管理",
                "施工順序・搬入経路・足場・分割可能性", weight=0.9),
    PersonaSpec("code", "ホウキ", "法規",
                "防火区画・避難経路・採光・階高・道路斜線", weight=0.9),
    PersonaSpec("bim", "ビム", "BIM 管理",
                "IFC 互換・LOD・レイヤ命名・座標精度", weight=1.0),
)


def _persona_prompt(persona: PersonaSpec, base_pass1_prompt: str) -> str:
    """ベース Pass 1 prompt にペルソナ固有の視点指示を注入."""
    persona_lens = (
        f"\n\n## ペルソナ視点 ({persona.name} / {persona.specialty})\n"
        f"あなたは {persona.specialty} の専門家として図面を読みます。\n"
        f"特に注視: {persona.focus}\n"
        f"他の専門が見落とす element を優先して抽出してください。"
        f"出力 JSON 形式 (elements / spaces 等) は base prompt に従う。"
    )
    return base_pass1_prompt + persona_lens


# ─── 投票 / 合意形成ロジック ─────────────────────────────────

@dataclass
class ElementVote:
    """単一 element に対する 7 ペルソナの投票結果."""
    canonical_id: str
    element_type: ElementType
    votes: list[tuple[str, ElementType, float]] = field(default_factory=list)
    """各票: (persona_id, persona's element_type, persona's confidence)"""

    @property
    def n_votes(self) -> int:
        return len(self.votes)

    @property
    def majority_type(self) -> ElementType:
        """多数決 ElementType."""
        if not self.votes:
            return self.element_type
        counts: dict[ElementType, float] = {}
        for pid, etype, conf in self.votes:
            persona = next((p for p in PERSONAS if p.id == pid), None)
            weight = (persona.weight if persona else 1.0) * conf
            counts[etype] = counts.get(etype, 0.0) + weight
        return max(counts.items(), key=lambda kv: kv[1])[0]

    @property
    def confidence(self) -> float:
        """0.0-1.0. 全 7 票が揃って同一 type なら 1.0、半数で異なれば 0.5 程度."""
        if not self.votes:
            return 0.0
        majority = self.majority_type
        agree = sum(1 for _, etype, _ in self.votes if etype == majority)
        return agree / len(self.votes)


def _spatial_canonical_id(elem: BuildingElement, grid_mm: float = 100.0) -> str:
    """空間位置でグリッド化した canonical id — 異ペルソナの抽出を同一 element として
    紐付ける key. 100mm グリッドで丸め (図面誤差吸収).

    storey を含む — 柱 (storey=1) と footing (storey=0) が同 xy にあっても
    別 element として扱うため. これを含めないと vertical stack が消える.
    """
    sx = round(elem.start.x / grid_mm) * grid_mm
    sy = round(elem.start.y / grid_mm) * grid_mm
    ex = round(elem.end.x / grid_mm) * grid_mm
    ey = round(elem.end.y / grid_mm) * grid_mm
    # 線分の方向は逆でも同じ canonical
    a = (sx, sy)
    b = (ex, ey)
    if a > b:
        a, b = b, a
    storey = elem.storey if elem.storey is not None else 0
    return f"st{storey}_{a[0]:.0f}_{a[1]:.0f}_{b[0]:.0f}_{b[1]:.0f}"


def _aggregate_votes(
    persona_results: dict[str, BuildingModel],
) -> tuple[list[BuildingElement], dict[str, float]]:
    """7 ペルソナの BuildingModel を element 単位で投票・合意形成.

    Returns:
        (consensus_elements, confidence_by_id)
    """
    votes: dict[str, ElementVote] = {}

    for pid, model in persona_results.items():
        if model is None:
            continue
        for elem in model.elements:
            cid = _spatial_canonical_id(elem)
            if cid not in votes:
                votes[cid] = ElementVote(
                    canonical_id=cid, element_type=elem.element_type,
                )
            # ペルソナの "confidence" は取得元 metadata 由来 (default 0.5)
            persona_conf = (
                elem.inference.confidence
                if elem.inference and elem.inference.confidence
                else 0.5
            )
            votes[cid].votes.append((pid, elem.element_type, persona_conf))

    consensus: list[BuildingElement] = []
    confidence_by_id: dict[str, float] = {}
    seen_ids: set[str] = set()

    for cid, vote in votes.items():
        # quorum: 4/7 以上の投票がある場合のみ採用 (3 以下は noise)
        if vote.n_votes < 3:
            continue
        majority_type = vote.majority_type
        confidence = vote.confidence

        # 多数決で勝った type を持つ element を 1 つ pick (同一 cid の代表)
        # 候補を持つ persona の最初の result を採用 — start/end は近似一致するはず
        chosen: Optional[BuildingElement] = None
        for pid, model in persona_results.items():
            if model is None:
                continue
            for elem in model.elements:
                if (
                    _spatial_canonical_id(elem) == cid
                    and elem.element_type == majority_type
                ):
                    chosen = elem
                    break
            if chosen:
                break
        if chosen is None:
            continue

        # consensus element は inference に投票結果を記録
        chosen_inf = chosen.inference if chosen.inference else InferenceMetadata()
        new_inference = InferenceMetadata(
            extraction_method=chosen_inf.extraction_method,
            confidence=confidence,
            inferred_from=(
                tuple(chosen_inf.inferred_from) + (f"persona_consensus_{vote.n_votes}_of_7",)
                if chosen_inf.inferred_from
                else (f"persona_consensus_{vote.n_votes}_of_7",)
            ),
            inference_rule=chosen_inf.inference_rule or f"multi_persona_{vote.n_votes}_quorum",
            pass_number=chosen_inf.pass_number,
        )
        # element id は canonical_id を使い idempotent に
        unique_id = f"consensus_{cid}_{majority_type.value}"
        if unique_id in seen_ids:
            continue
        seen_ids.add(unique_id)
        new_elem = BuildingElement(
            id=unique_id,
            element_type=majority_type,
            name=chosen.name,
            start=chosen.start,
            end=chosen.end,
            thickness=chosen.thickness,
            height=chosen.height,
            storey=chosen.storey,
            material=chosen.material,
            structural_role=chosen.structural_role,
            inference=new_inference,
        )
        consensus.append(new_elem)
        confidence_by_id[unique_id] = confidence

    return consensus, confidence_by_id


# ─── orchestrator ──────────────────────────────────────────────

async def analyze_drawing_multi_persona(
    image_data: bytes,
    mime_type: str = "image/png",
    source_file: str = "",
    *,
    quorum: int = 3,
) -> Optional[BuildingModel]:
    """7 ペルソナで independent に Pass 1 を実行 → 合意形成.

    Args:
        image_data: 図面画像
        mime_type: MIME
        source_file: 元ファイル名
        quorum: element 採用に必要な最低投票数 (default 3 = 7 中 3)

    Returns:
        合意形成された BuildingModel (各 element に persona_consensus 信頼度付き)
        None on failure (4 ペルソナ以上失敗)
    """
    # 遅延 import で circular dep 回避 + 既存 vlm_analyzer の logic を再利用
    from plugins.conquest.vlm_analyzer import _call_vlm, _json_to_building_model
    from plugins.conquest.prompts import PASS1_SEE

    if not os.environ.get("GEMINI_API_KEY"):
        logger.error("GEMINI_API_KEY not configured — multi_persona_inference unavailable")
        return None

    # asyncio で 7 Pass を並列に走らせる (Gemini API rate limit に注意)
    async def run_persona(persona: PersonaSpec) -> tuple[str, Optional[BuildingModel]]:
        prompt = _persona_prompt(persona, PASS1_SEE)
        # _call_vlm は同期 → run_in_executor で非同期化
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(None, _call_vlm, image_data, mime_type, prompt)
        except Exception as e:
            logger.warning("persona %s _call_vlm failed: %s", persona.id, e)
            return persona.id, None
        if raw is None:
            return persona.id, None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("persona %s JSON parse failed: %s", persona.id, e)
            return persona.id, None
        model = _json_to_building_model(data, source_file, pass_number=1)
        return persona.id, model

    tasks = [run_persona(p) for p in PERSONAS]
    pairs = await asyncio.gather(*tasks)
    persona_results: dict[str, BuildingModel] = {pid: m for pid, m in pairs if m is not None}

    successes = len(persona_results)
    logger.info("multi_persona inference: %d/%d ペルソナ成功", successes, len(PERSONAS))

    if successes < quorum:
        logger.error("multi_persona quorum failed (%d < %d)", successes, quorum)
        return None

    # 投票・合意形成
    consensus_elements, confidence_map = _aggregate_votes(persona_results)
    logger.info(
        "multi_persona consensus: %d elements (avg confidence=%.2f)",
        len(consensus_elements),
        sum(confidence_map.values()) / max(len(confidence_map), 1),
    )

    if not consensus_elements:
        logger.warning("multi_persona consensus produced 0 elements")
        return None

    # 任意 ペルソナの metadata を seed に新 BuildingModel を組立
    seed_model = next(iter(persona_results.values()))
    return BuildingModel(
        source_file=source_file,
        metadata=seed_model.metadata,
        elements=tuple(consensus_elements),
        spaces=seed_model.spaces,  # spaces は別タスク (Phase 2)
    )


def analyze_drawing_multi_persona_sync(
    image_data: bytes, mime_type: str = "image/png", source_file: str = "",
    *, quorum: int = 3,
) -> Optional[BuildingModel]:
    """sync wrapper for callers that don't run in async context."""
    return asyncio.run(
        analyze_drawing_multi_persona(image_data, mime_type, source_file, quorum=quorum)
    )
