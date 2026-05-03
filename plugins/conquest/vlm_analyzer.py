"""Conquest Mode v2.0 — VLM図面解析パイプライン（4パス認知モデル）.

「図面は影である。影から実体を想像せよ。」

Gemini 2.5 Pro Visionを使用した4パス認知パイプライン:
  Pass 1 (See):       影の観察 — 描かれた線だけを読む
  Pass 2 (Imagine):   影→実体 — 構造的に存在するはずの要素を推論
  Pass 3 (Understand): 実体の本質 — 構造種別・荷重経路を分析
  Pass 4 (Validate):  検証 — 再投影して原図と比較

multi_pass=False で v1.0互換のシングルパスモードにフォールバック。
"""
import base64
import json
import logging
import os
import re
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
    Space,
    StructuralRole,
    StructuralSystem,
)
from plugins.conquest.prompts import (
    PASS1_SEE,
    PASS2_IMAGINE,
    PASS3_UNDERSTAND,
    PASS4_VALIDATE,
    LEGACY_SINGLE_PASS,
)

logger = logging.getLogger("conquest.vlm_analyzer")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
VISION_MODEL = os.environ.get("CONQUEST_VISION_MODEL", "gemini-2.5-pro")
FLASH_MODEL = os.environ.get("CONQUEST_FLASH_MODEL", "gemini-2.5-flash")


def analyze_drawing(image_data: bytes, mime_type: str = "image/png",
                    source_file: str = "", *, multi_pass: bool = True) -> Optional[BuildingModel]:
    """2D図面画像からBuildingModel（中間表現）を生成する.

    Args:
        image_data: 図面画像のバイナリデータ
        mime_type: 画像のMIMEタイプ
        source_file: 元ファイル名
        multi_pass: True=v2.0 4パス認知モデル, False=v1.0互換シングルパス

    Returns:
        BuildingModel or None on failure
    """
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY not configured")
        return None

    if not multi_pass:
        return _analyze_single_pass(image_data, mime_type, source_file)

    return _analyze_multi_pass(image_data, mime_type, source_file)


def _analyze_single_pass(image_data: bytes, mime_type: str, source_file: str) -> Optional[BuildingModel]:
    """v1.0互換: シングルパス抽出。"""
    raw = _call_vlm(image_data, mime_type, LEGACY_SINGLE_PASS, model=VISION_MODEL)
    if raw is None:
        return None
    try:
        extracted = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("Single-pass JSON parse failed: %s", e)
        return None
    return _json_to_building_model(extracted, source_file)


def _analyze_multi_pass(image_data: bytes, mime_type: str, source_file: str) -> Optional[BuildingModel]:
    """v2.0: 4パス認知モデル — 見る→想像する→理解する→検証する。"""

    # Pass 1: 見る (See) — 影を観察
    logger.info("Pass 1/4: See (影の観察)")
    pass1_raw = _call_vlm(image_data, mime_type, PASS1_SEE, model=VISION_MODEL)
    if pass1_raw is None:
        return None
    try:
        pass1_data = json.loads(pass1_raw)
    except json.JSONDecodeError:
        logger.error("Pass 1 JSON parse failed")
        return None

    pass1_model = _json_to_building_model(pass1_data, source_file, pass_number=1)

    # Pass 2: 想像する (Imagine) — 影→実体
    logger.info("Pass 2/4: Imagine (影→実体)")
    pass1_elements_json = json.dumps(
        [{"id": e.id, "element_type": e.element_type.value, "name": e.name,
          "start": {"x": e.start.x, "y": e.start.y},
          "end": {"x": e.end.x, "y": e.end.y},
          "thickness": e.thickness, "height": e.height, "storey": e.storey,
          "material": e.material.value}
         for e in pass1_model.elements],
        ensure_ascii=False,
    )
    pass2_prompt = PASS2_IMAGINE.replace("{pass1_elements_json}", pass1_elements_json)
    pass2_raw = _call_vlm(image_data, mime_type, pass2_prompt, model=VISION_MODEL)

    inferred_elements = ()
    if pass2_raw:
        try:
            pass2_data = json.loads(pass2_raw)
            inferred_elements = _parse_inferred_elements(pass2_data.get("inferred_elements", []))
        except json.JSONDecodeError:
            logger.warning("Pass 2 JSON parse failed, continuing with Pass 1 only")

    # 合体
    all_elements = pass1_model.elements + inferred_elements
    model = BuildingModel(
        metadata=pass1_model.metadata,
        storeys=pass1_model.storeys,
        storey_heights=pass1_model.storey_heights,
        elements=all_elements,
        spaces=pass1_model.spaces,
    )

    # Pass 3: 理解する (Understand) — 構造種別・荷重経路
    logger.info("Pass 3/4: Understand (実体の本質)")
    all_elements_json = json.dumps(
        [{"id": e.id, "element_type": e.element_type.value, "thickness": e.thickness,
          "material": e.material.value, "storey": e.storey}
         for e in model.elements],
        ensure_ascii=False,
    )
    pass3_prompt = PASS3_UNDERSTAND.replace("{all_elements_json}", all_elements_json)
    pass3_raw = _call_vlm(image_data, mime_type, pass3_prompt, model=FLASH_MODEL)

    if pass3_raw:
        try:
            pass3_data = json.loads(pass3_raw)
            model = _apply_structural_analysis(model, pass3_data)
        except json.JSONDecodeError:
            logger.warning("Pass 3 JSON parse failed, continuing")

    # Pass 4: 検証する (Validate) — 再投影チェック
    logger.info("Pass 4/4: Validate (再投影検証)")
    model_summary = json.dumps({
        "element_count": len(model.elements),
        "element_types": {et.value: sum(1 for e in model.elements if e.element_type == et)
                         for et in ElementType},
        "storeys": list(model.storeys),
    }, ensure_ascii=False)
    pass4_prompt = PASS4_VALIDATE.replace("{full_model_json}", model_summary)
    pass4_raw = _call_vlm(image_data, mime_type, pass4_prompt, model=FLASH_MODEL)

    if pass4_raw:
        try:
            pass4_data = json.loads(pass4_raw)
            model = _apply_validation_adjustments(model, pass4_data)
        except json.JSONDecodeError:
            logger.warning("Pass 4 JSON parse failed, continuing")

    logger.info("Multi-pass analysis complete: %d elements (%d visual, %d inferred)",
                len(model.elements),
                sum(1 for e in model.elements if e.inference.extraction_method == ExtractionMethod.VISUAL),
                sum(1 for e in model.elements if e.inference.extraction_method != ExtractionMethod.VISUAL))

    return model


def _call_vlm(image_data: bytes, mime_type: str, prompt: str,
              *, model: str = "") -> Optional[str]:
    """Gemini Vision APIを呼び出し、テキスト結果を返す。"""
    import requests

    target_model = model or VISION_MODEL
    b64_image = base64.b64encode(image_data).decode("utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent"

    try:
        resp = requests.post(
            f"{url}?key={GEMINI_API_KEY}",
            json={
                "contents": [{"parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime_type, "data": b64_image}},
                ]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192},
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return None
        raw = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        return re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`")
    except Exception as e:
        logger.error("VLM call failed (%s): %s", target_model, e)
        return None


def _parse_inferred_elements(elements_data: list) -> tuple[BuildingElement, ...]:
    """Pass 2の推論要素をBuildingElementに変換する。"""
    result = []
    for e in elements_data:
        try:
            element_type = ElementType(e.get("element_type", "wall"))
        except ValueError:
            continue
        start = e.get("start") or {}
        end = e.get("end") or {}
        try:
            material = MaterialType(e.get("material", "unknown"))
        except ValueError:
            material = MaterialType.UNKNOWN

        result.append(BuildingElement(
            id=e.get("id", ""),
            element_type=element_type,
            name=e.get("name", ""),
            start=Point2D(float(start.get("x", 0)), float(start.get("y", 0))),
            end=Point2D(float(end.get("x", 0)), float(end.get("y", 0))),
            height=float(e.get("height", 0)),
            thickness=float(e.get("thickness", 0)),
            material=material,
            storey=int(e.get("storey", 1)),
            inference=InferenceMetadata(
                extraction_method=ExtractionMethod.INFERRED,
                confidence=float(e.get("confidence", 0.5)),
                inferred_from=tuple(e.get("inferred_from", [])),
                inference_rule=e.get("inference_rule", ""),
                pass_number=2,
            ),
        ))
    return tuple(result)


def _apply_structural_analysis(model: BuildingModel, analysis: dict) -> BuildingModel:
    """Pass 3の構造分析結果をモデルに適用する。"""
    try:
        system = StructuralSystem(analysis.get("structural_system", "unknown"))
    except ValueError:
        system = StructuralSystem.UNKNOWN
    confidence = float(analysis.get("structural_confidence", 0))

    # 要素の構造的役割を更新
    updates = {u["id"]: u for u in analysis.get("element_updates", [])}
    updated_elements = []
    for e in model.elements:
        if e.id in updates:
            try:
                role = StructuralRole(updates[e.id].get("structural_role", "load_bearing"))
            except ValueError:
                role = e.structural_role
            updated_elements.append(BuildingElement(
                **{**{f.name: getattr(e, f.name) for f in e.__dataclass_fields__.values()},
                   "structural_role": role}
            ))
        else:
            updated_elements.append(e)

    return BuildingModel(
        **{**{f.name: getattr(model, f.name) for f in model.__dataclass_fields__.values()},
           "elements": tuple(updated_elements),
           "structural_system": system,
           "structural_confidence": confidence}
    )


def _apply_validation_adjustments(model: BuildingModel, validation: dict) -> BuildingModel:
    """Pass 4の検証結果でconfidenceを調整する。"""
    adjustments = {a["element_id"]: a for a in validation.get("confidence_adjustments", [])}
    if not adjustments:
        return model

    updated = []
    for e in model.elements:
        if e.id in adjustments:
            new_conf = float(adjustments[e.id].get("new_confidence", e.inference.confidence))
            from dataclasses import replace
            updated.append(replace(e, inference=replace(e.inference, confidence=new_conf)))
        else:
            updated.append(e)

    return BuildingModel(
        **{**{f.name: getattr(model, f.name) for f in model.__dataclass_fields__.values()},
           "elements": tuple(updated)}
    )


def _safe_float(val, default: float = 0.0) -> float:
    """安全なfloat変換。None, 空文字, 非数値文字列に対応."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_int(val, default: int = 1) -> int:
    """安全なint変換。'1-3'のような範囲文字列は最初の数値を取る."""
    if val is None:
        return default
    if isinstance(val, int):
        return val
    try:
        return int(val)
    except (ValueError, TypeError):
        # '1-3' → 1, 'B1' → -1 等の対応
        import re
        m = re.search(r'-?\d+', str(val))
        return int(m.group()) if m else default


def _json_to_building_model(data: dict, source_file: str = "", pass_number: int = 1) -> BuildingModel:
    """VLM出力のJSONからBuildingModelに変換する."""
    metadata_raw = data.get("metadata", {})
    metadata = DrawingMetadata(
        source_file=source_file,
        drawing_type=metadata_raw.get("drawing_type", ""),
        scale=metadata_raw.get("scale", ""),
        unit=metadata_raw.get("unit", "mm"),
        confidence=float(metadata_raw.get("confidence", 0)),
    )

    # 要素変換
    elements = []
    for e in data.get("elements", []):
        try:
            element_type = ElementType(e.get("element_type", "wall"))
        except ValueError:
            continue

        start = e.get("start") or {}
        end = e.get("end") or {}

        try:
            material = MaterialType(e.get("material", "unknown"))
        except ValueError:
            material = MaterialType.UNKNOWN

        elements.append(BuildingElement(
            id=e.get("id", "") or f"elem_{len(elements):03d}",
            element_type=element_type,
            name=e.get("name", ""),
            start=Point2D(_safe_float(start.get("x")), _safe_float(start.get("y"))),
            end=Point2D(_safe_float(end.get("x")), _safe_float(end.get("y"))),
            height=_safe_float(e.get("height")),
            thickness=_safe_float(e.get("thickness")),
            width=_safe_float(e.get("width")),
            material=material,
            storey=_safe_int(e.get("storey"), 1),
            host_element_id=e.get("host_element_id", "") or "",
            notes=e.get("notes", "") or "",
            inference=InferenceMetadata(
                confidence=_safe_float(e.get("confidence"), 0.5),
                pass_number=pass_number,
            ),
        ))

    # 空間変換
    spaces = []
    for s in data.get("spaces", []):
        spaces.append(Space(
            id=s.get("id", ""),
            name=s.get("name", ""),
            storey=int(s.get("storey", 1)),
            boundary_element_ids=tuple(s.get("boundary_element_ids", [])),
            area=float(s.get("area", 0)),
        ))

    # 階情報
    storeys = tuple(data.get("storeys", [1]))
    storey_heights_raw = data.get("storey_heights", {"1": 3000})
    storey_heights = {int(k): float(v) for k, v in storey_heights_raw.items()}

    model = BuildingModel(
        metadata=metadata,
        storeys=storeys,
        storey_heights=storey_heights,
        elements=tuple(elements),
        spaces=tuple(spaces),
    )

    logger.info("Drawing analyzed: %d elements, %d spaces, confidence=%.2f",
                len(elements), len(spaces), metadata.confidence)

    return model
