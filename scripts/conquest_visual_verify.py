"""Pack #83 — Conquest 抽出結果の視覚確認スクリプト.

CEO directive (2026-05-03): 「サンプル PDF から抽出作成された 3D(実体) を
何度も自分自身で視覚的に確認しながら進めてね」

このスクリプトは BuildingModel を SVG (2D plan view) として render し、
人間 (CTO) が目で見て検証できるようにする.

使用例:
    python3 scripts/conquest_visual_verify.py [--input sample.pdf] [--output verify.svg]

VLM 呼出が不可能な環境 (GEMINI_API_KEY なし) でも fixture data で
pipeline を end-to-end に確認できる.

CEO 学び (memory feedback_verify_with_eyes.md):
  「動いているはず」では受け入れられない. 自分の目で見ろ.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

# repo root を sys.path に追加 (script として実行する用)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.conquest.schemas import (  # noqa: E402
    BuildingElement,
    BuildingModel,
    DrawingMetadata,
    ElementType,
    InferenceMetadata,
    MaterialType,
    Point2D,
    StructuralRole,
    ExtractionMethod,
)
from plugins.conquest.relationship_validator import detect_anomalies  # noqa: E402
from plugins.conquest.probabilistic_bim import (  # noqa: E402
    ProbabilisticBuildingModel,
    ProbabilisticElement,
    Hypothesis,
)


# ─── Element 種別 → SVG color ────────────────────────────────────

ELEMENT_COLORS = {
    ElementType.WALL: ("#4a4a4a", "#888", 6),       # (fill, stroke, stroke_w)
    ElementType.COLUMN: ("#1f1f1f", "#000", 0),
    ElementType.BEAM: ("#a83232", "#600", 4),
    ElementType.SLAB: ("#cccccc", "#999", 1),
    ElementType.DOOR: ("#7c4a23", "#5a2", 3),
    ElementType.WINDOW: ("#3a86c0", "#16f", 3),
    ElementType.STAIR: ("#7e3fa6", "#502", 3),
    ElementType.SPACE: ("none", "#888", 1),
    ElementType.FOUNDATION: ("#5a5a5a", "#333", 2),
    ElementType.ROOF: ("#8b4513", "#532", 2),
}


# ─── BuildingModel → SVG ────────────────────────────────────────

def model_to_svg(
    model: BuildingModel,
    *,
    width_mm: float = 30000,
    height_mm: float = 20000,
    margin_mm: float = 1000,
    title: str = "",
    confidence_overlay: bool = True,
) -> str:
    """BuildingModel を 2D plan view (SVG) として render.

    座標系: model の Point2D (mm 単位 想定) を SVG ピクセル座標に変換
    (1 mm = 0.1 px、デフォルトで 30000mm × 20000mm の図面を 3000 × 2000 px)
    """
    scale = 0.1  # mm → px
    svg_w = (width_mm + 2 * margin_mm) * scale
    svg_h = (height_mm + 2 * margin_mm) * scale
    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w:.0f}" height="{svg_h:.0f}" '
        f'viewBox="0 0 {svg_w:.0f} {svg_h:.0f}" style="background:#fafafa">'
    )
    # title
    if title:
        parts.append(
            f'<text x="10" y="20" font-family="sans-serif" font-size="14" fill="#333">{_xml_escape(title)}</text>'
        )

    # 元 model の bbox を計算して margin に収める
    if model.elements:
        xs = [e.start.x for e in model.elements] + [e.end.x for e in model.elements]
        ys = [e.start.y for e in model.elements] + [e.end.y for e in model.elements]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
    else:
        min_x = min_y = 0.0
        max_x = width_mm
        max_y = height_mm

    def t(p: Point2D) -> tuple[float, float]:
        """model 座標 (mm) → SVG 座標 (px). Y 軸反転 (建築慣習)."""
        x = (p.x - min_x + margin_mm) * scale
        y = svg_h - (p.y - min_y + margin_mm) * scale
        return x, y

    # element 描画 (z-order: SLAB → SPACE → WALL → BEAM → COLUMN → opening)
    z_order = [
        ElementType.SLAB, ElementType.FOUNDATION, ElementType.SPACE,
        ElementType.WALL, ElementType.BEAM, ElementType.COLUMN,
        ElementType.DOOR, ElementType.WINDOW, ElementType.STAIR, ElementType.ROOF,
    ]
    by_type: dict = {t: [] for t in z_order}
    for e in model.elements:
        if e.element_type in by_type:
            by_type[e.element_type].append(e)

    for etype in z_order:
        for e in by_type[etype]:
            fill, stroke, sw = ELEMENT_COLORS.get(etype, ("#999", "#666", 1))
            x1, y1 = t(e.start)
            x2, y2 = t(e.end)
            confidence = (
                e.inference.confidence if e.inference and e.inference.confidence is not None else 1.0
            )
            opacity = 0.4 + 0.6 * confidence  # 信頼度低 → 薄く
            if etype == ElementType.COLUMN:
                # 柱は点として円描画
                parts.append(
                    f'<circle cx="{x1:.1f}" cy="{y1:.1f}" r="6" '
                    f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" opacity="{opacity:.2f}"/>'
                )
            else:
                # 線分として描画 (壁/梁/開口部)
                parts.append(
                    f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                    f'stroke="{fill}" stroke-width="{max(sw, 2)}" opacity="{opacity:.2f}"/>'
                )
            # confidence 表示 (重畳)
            if confidence_overlay and confidence < 0.7:
                mx, my = (x1 + x2) / 2, (y1 + y2) / 2
                parts.append(
                    f'<text x="{mx + 8:.0f}" y="{my - 4:.0f}" font-family="monospace" '
                    f'font-size="9" fill="#c00">{confidence:.2f}</text>'
                )

    # 凡例 (右上)
    legend_x = svg_w - 200
    legend_y = 30
    parts.append(
        f'<rect x="{legend_x - 10}" y="{legend_y - 10}" width="180" height="160" '
        f'fill="white" stroke="#bbb" stroke-width="1" opacity="0.95"/>'
    )
    parts.append(
        f'<text x="{legend_x}" y="{legend_y + 5}" font-family="sans-serif" font-size="11" fill="#333" font-weight="bold">凡例 (Pack #83)</text>'
    )
    legend_items = [
        ("WALL 壁", ElementType.WALL),
        ("COLUMN 柱", ElementType.COLUMN),
        ("BEAM 梁", ElementType.BEAM),
        ("DOOR ドア", ElementType.DOOR),
        ("WINDOW 窓", ElementType.WINDOW),
    ]
    for i, (label, etype) in enumerate(legend_items):
        color = ELEMENT_COLORS[etype][0]
        ly = legend_y + 25 + i * 18
        parts.append(
            f'<line x1="{legend_x}" y1="{ly}" x2="{legend_x + 20}" y2="{ly}" '
            f'stroke="{color}" stroke-width="3"/>'
        )
        parts.append(
            f'<text x="{legend_x + 28}" y="{ly + 4}" font-family="sans-serif" font-size="10" fill="#333">{_xml_escape(label)}</text>'
        )

    # stats (左下)
    stat_y = svg_h - 60
    parts.append(
        f'<text x="10" y="{stat_y}" font-family="monospace" font-size="10" fill="#444">Elements: {len(model.elements)}</text>'
    )
    parts.append(
        f'<text x="10" y="{stat_y + 14}" font-family="monospace" font-size="10" fill="#444">Spaces: {len(model.spaces)}</text>'
    )
    if model.elements:
        avg_conf = sum(
            e.inference.confidence for e in model.elements
            if e.inference and e.inference.confidence is not None
        ) / max(1, sum(1 for e in model.elements if e.inference))
        parts.append(
            f'<text x="10" y="{stat_y + 28}" font-family="monospace" font-size="10" fill="#444">Avg confidence: {avg_conf:.2f}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def probabilistic_to_svg(
    pmodel: ProbabilisticBuildingModel,
    *,
    width_mm: float = 30000,
    height_mm: float = 20000,
    margin_mm: float = 1000,
    title: str = "",
) -> str:
    """ProbabilisticBuildingModel の top-3 候補を 3 layer で重ね描画.

    top-1 = 100% opacity / top-2 = 60% opacity dashed / top-3 = 30% opacity dotted.
    is_ambiguous な element は赤い「?」マーカーを付与 — ユーザ確認推奨を視覚化.
    """
    scale = 0.1
    svg_w = (width_mm + 2 * margin_mm) * scale
    svg_h = (height_mm + 2 * margin_mm) * scale
    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w:.0f}" height="{svg_h:.0f}" '
        f'viewBox="0 0 {svg_w:.0f} {svg_h:.0f}" style="background:#fafafa">'
    )
    if title:
        parts.append(
            f'<text x="10" y="20" font-family="sans-serif" font-size="14" fill="#333">{_xml_escape(title)}</text>'
        )

    if pmodel.elements:
        xs = [pe.representative.start.x for pe in pmodel.elements] + \
             [pe.representative.end.x for pe in pmodel.elements]
        ys = [pe.representative.start.y for pe in pmodel.elements] + \
             [pe.representative.end.y for pe in pmodel.elements]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
    else:
        min_x = min_y = 0.0
        max_x = width_mm
        max_y = height_mm

    def t(p: Point2D) -> tuple[float, float]:
        x = (p.x - min_x + margin_mm) * scale
        y = svg_h - (p.y - min_y + margin_mm) * scale
        return x, y

    for pe in pmodel.elements:
        elem = pe.representative
        x1, y1 = t(elem.start)
        x2, y2 = t(elem.end)
        # top-3 を opacity 100/60/30 % で重ね描き
        for rank, h in enumerate(pe.hypotheses):
            color = ELEMENT_COLORS.get(h.element_type, ("#999", "#666", 1))[0]
            opacity = [1.0, 0.6, 0.3][min(rank, 2)]
            stroke_dash = ["", "stroke-dasharray=\"8,4\"", "stroke-dasharray=\"2,4\""][min(rank, 2)]
            offset = rank * 4  # 同位置を少しずらして重畳可視化
            if h.element_type == ElementType.COLUMN:
                parts.append(
                    f'<circle cx="{x1 + offset:.1f}" cy="{y1 + offset:.1f}" r="{6 + rank}" '
                    f'fill="{color}" opacity="{opacity:.2f}" {stroke_dash}/>'
                )
            else:
                parts.append(
                    f'<line x1="{x1 + offset:.1f}" y1="{y1 + offset:.1f}" '
                    f'x2="{x2 + offset:.1f}" y2="{y2 + offset:.1f}" '
                    f'stroke="{color}" stroke-width="{6 - rank * 2}" opacity="{opacity:.2f}" {stroke_dash}/>'
                )
        # ambiguous マーカー
        if pe.is_ambiguous:
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            parts.append(
                f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="14" fill="none" stroke="#c00" stroke-width="2"/>'
                f'<text x="{mx - 4:.1f}" y="{my + 5:.1f}" font-family="sans-serif" font-size="14" '
                f'fill="#c00" font-weight="bold">?</text>'
            )
        # top-1 type label
        if pe.top1:
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            parts.append(
                f'<text x="{mx + 12:.1f}" y="{my + 4:.1f}" font-family="monospace" font-size="9" fill="#333">'
                f'{pe.top1.element_type.value} {pe.top1.probability:.2f}</text>'
            )

    # 凡例
    legend_x = svg_w - 220
    parts.append(
        f'<rect x="{legend_x - 10}" y="20" width="210" height="120" fill="white" '
        f'stroke="#bbb" stroke-width="1" opacity="0.95"/>'
    )
    parts.append(
        f'<text x="{legend_x}" y="40" font-family="sans-serif" font-size="11" '
        f'fill="#333" font-weight="bold">Probabilistic BIM (Phase 2)</text>'
    )
    parts.append(
        f'<line x1="{legend_x}" y1="55" x2="{legend_x + 30}" y2="55" stroke="#4a4a4a" stroke-width="6"/>'
        f'<text x="{legend_x + 36}" y="59" font-family="sans-serif" font-size="10">top-1 (100%)</text>'
    )
    parts.append(
        f'<line x1="{legend_x}" y1="73" x2="{legend_x + 30}" y2="73" stroke="#4a4a4a" stroke-width="4" '
        f'opacity="0.6" stroke-dasharray="8,4"/>'
        f'<text x="{legend_x + 36}" y="77" font-family="sans-serif" font-size="10">top-2 (60%)</text>'
    )
    parts.append(
        f'<line x1="{legend_x}" y1="91" x2="{legend_x + 30}" y2="91" stroke="#4a4a4a" stroke-width="2" '
        f'opacity="0.3" stroke-dasharray="2,4"/>'
        f'<text x="{legend_x + 36}" y="95" font-family="sans-serif" font-size="10">top-3 (30%)</text>'
    )
    parts.append(
        f'<circle cx="{legend_x + 15}" cy="113" r="10" fill="none" stroke="#c00" stroke-width="2"/>'
        f'<text x="{legend_x + 11}" y="117" font-family="sans-serif" font-size="11" fill="#c00" font-weight="bold">?</text>'
        f'<text x="{legend_x + 36}" y="117" font-family="sans-serif" font-size="10" fill="#c00">'
        f'ambiguous (要確認)</text>'
    )

    # stats
    stat_y = svg_h - 60
    parts.append(
        f'<text x="10" y="{stat_y}" font-family="monospace" font-size="10" fill="#444">'
        f'Probabilistic elements: {len(pmodel.elements)}</text>'
    )
    parts.append(
        f'<text x="10" y="{stat_y + 14}" font-family="monospace" font-size="10" fill="#444">'
        f'Ambiguous (要確認): {pmodel.ambiguous_count}</text>'
    )

    parts.append("</svg>")
    return "\n".join(parts)


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ─── fixture モデル (VLM 不要で pipeline 確認) ──────────────────

def make_fixture_model(scenario: str = "simple_office") -> BuildingModel:
    """シナリオ別のサンプル BuildingModel を返す.

    scenario 一覧:
      - simple_office: 矩形オフィス (壁 4 + 柱 4 + 開口 1)
      - with_floating_column: 浮遊柱 anomaly を含む不正モデル
      - low_confidence_mix: 高/低 confidence 混在 (multi_persona の合意度 simulate)
    """
    if scenario == "simple_office":
        elements = (
            # 4 wall (10m × 6m 矩形)
            _wall("w_n", (0, 6000), (10000, 6000), 0.95),
            _wall("w_e", (10000, 0), (10000, 6000), 0.95),
            _wall("w_s", (0, 0), (10000, 0), 0.95),
            _wall("w_w", (0, 0), (0, 6000), 0.95),
            # 4 column (隅)
            _column("c_ne", (10000, 6000), 0.92),
            _column("c_se", (10000, 0), 0.92),
            _column("c_sw", (0, 0), 0.92),
            _column("c_nw", (0, 6000), 0.92),
            # door (south wall 中央)
            _door("d1", (4500, 0), (5500, 0), 0.85),
        )
    elif scenario == "with_floating_column":
        # 1F 柱が基礎なし → relationship_validator が detect する
        elements = (
            _wall("w1", (0, 6000), (10000, 6000), 0.9),
            _column("c_isolated", (5000, 3000), 0.55, storey=1),  # 浮遊
        )
    elif scenario == "low_confidence_mix":
        elements = (
            _wall("w1", (0, 6000), (10000, 6000), 0.95),
            _wall("w2", (10000, 0), (10000, 6000), 0.4),  # 低信頼
            _wall("w3", (0, 0), (10000, 0), 0.95),
            _wall("w4", (0, 0), (0, 6000), 0.55),
        )
    else:
        raise ValueError(f"unknown scenario: {scenario}")

    return BuildingModel(
        metadata=DrawingMetadata(
            source_file=f"fixture_{scenario}.pdf",
            drawing_type="floor_plan",
        ),
        elements=elements,
        spaces=(),
    )


def _wall(eid: str, start: tuple[float, float], end: tuple[float, float],
          conf: float, storey: int = 1) -> BuildingElement:
    return BuildingElement(
        id=eid,
        element_type=ElementType.WALL,
        name=f"wall_{eid}",
        start=Point2D(*start),
        end=Point2D(*end),
        thickness=200.0,
        height=2700.0,
        storey=storey,
        material=MaterialType.RC,
        structural_role=StructuralRole.LOAD_BEARING,
        inference=InferenceMetadata(extraction_method=ExtractionMethod.VISUAL, confidence=conf),
    )


def _column(eid: str, pos: tuple[float, float], conf: float,
            storey: int = 1) -> BuildingElement:
    return BuildingElement(
        id=eid,
        element_type=ElementType.COLUMN,
        name=f"col_{eid}",
        start=Point2D(*pos),
        end=Point2D(*pos),
        thickness=600.0,  # 600mm 角
        height=2700.0,
        storey=storey,
        material=MaterialType.RC,
        structural_role=StructuralRole.LOAD_BEARING,
        inference=InferenceMetadata(extraction_method=ExtractionMethod.VISUAL, confidence=conf),
    )


def _door(eid: str, start: tuple[float, float], end: tuple[float, float],
          conf: float) -> BuildingElement:
    return BuildingElement(
        id=eid,
        element_type=ElementType.DOOR,
        name=f"door_{eid}",
        start=Point2D(*start),
        end=Point2D(*end),
        thickness=40.0,
        height=2100.0,
        storey=1,
        material=MaterialType.W,
        structural_role=StructuralRole.PARTITION,
        inference=InferenceMetadata(extraction_method=ExtractionMethod.VISUAL, confidence=conf),
    )


# ─── main ─────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Conquest 視覚確認 — BuildingModel → SVG")
    parser.add_argument("--scenario", choices=["simple_office", "with_floating_column", "low_confidence_mix", "all"],
                        default="all")
    parser.add_argument("--output-dir", default="/tmp/conquest_visual_verify")
    parser.add_argument("--probabilistic", action="store_true",
                        help="Phase 2: top-3 候補 + ambiguous mark を 3 layer SVG で出力")
    parser.add_argument("--physics", action="store_true",
                        help="Phase 3: 物理 self-validation を実行 + report 出力")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    scenarios = (
        ["simple_office", "with_floating_column", "low_confidence_mix"]
        if args.scenario == "all"
        else [args.scenario]
    )

    for s in scenarios:
        model = make_fixture_model(s)
        svg = model_to_svg(model, title=f"Conquest verify — {s}")
        out_path = out_dir / f"{s}.svg"
        out_path.write_text(svg, encoding="utf-8")

        anomalies = detect_anomalies(model)
        print(f"[{s}] elements={len(model.elements)} anomalies={len(anomalies)} → {out_path}")
        for a in anomalies[:5]:
            print(f"  ⚠️  {a.severity.value:8s} {a.anomaly_type.value:25s} {a.description[:80]}")

        # Phase 3: physics self-validation
        if args.physics:
            from plugins.conquest.physics_self_validator import (
                validate_with_physics, format_physics_report,
            )
            analyzed, presult = validate_with_physics(model)
            print("  ── Physics validation ──")
            print(format_physics_report(presult).replace("\n", "\n  "))
            phys_path = out_dir / f"{s}_physics_report.txt"
            phys_path.write_text(format_physics_report(presult), encoding="utf-8")
            print(f"  └── physics report → {phys_path}")

        # Phase 2: probabilistic SVG (fixture から仮想的な multi_persona vote を作って渡す)
        if args.probabilistic:
            from plugins.conquest.probabilistic_bim import (
                merge_persona_results_to_probabilistic,
            )
            # 4 ペルソナで同モデルを返す = 全員一致 → top-1 が 100%
            persona_views = {pid: model for pid in ["archi", "structure", "qs", "code"]}
            pmodel = merge_persona_results_to_probabilistic(persona_views)
            psvg = probabilistic_to_svg(pmodel, title=f"Probabilistic — {s}")
            ppath = out_dir / f"{s}_probabilistic.svg"
            ppath.write_text(psvg, encoding="utf-8")
            print(f"  └── probabilistic: {len(pmodel.elements)} elements, "
                  f"{pmodel.ambiguous_count} ambiguous → {ppath}")

    print(f"\n視覚確認: open {out_dir}/*.svg or self-review with Read tool.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
