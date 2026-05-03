"""Pack #83 Phase 4 — 精度 benchmark framework.

CEO directive 「5 sample PDF で精度 95%+ 達成」(配布前 PoC) のため、
ground truth と pipeline 抽出結果を比較する.

Metrics:
  - precision = TP / (TP + FP) — 抽出した element のうち正しいもの率
  - recall    = TP / (TP + FN) — 正解のうち抽出できたもの率
  - F1        = 2PR / (P+R)
  - element_type_accuracy — type 一致率 (位置一致した中で)

Match 基準:
  spatial canonical_id (100mm grid) でグループ化
  → 同 cid に正解 element と抽出 element があれば match

シナリオ別評価:
  各 fixture の "expected_elements" を ground truth として、
  pipeline 結果の elements と比較.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from plugins.conquest.multi_persona_inference import _spatial_canonical_id
from plugins.conquest.schemas import BuildingElement, BuildingModel, ElementType

logger = logging.getLogger("conquest.accuracy_benchmark")


@dataclass(frozen=True)
class BenchmarkResult:
    """単一 model 比較の結果."""
    scenario: str
    n_ground_truth: int
    n_extracted: int
    n_true_positive: int
    n_false_positive: int
    n_false_negative: int
    type_correct: int       # TP の中で element_type 一致した数
    precision: float
    recall: float
    f1: float
    type_accuracy: float    # type_correct / TP


@dataclass(frozen=True)
class BenchmarkSuite:
    """複数シナリオの aggregate."""
    results: tuple[BenchmarkResult, ...]
    avg_precision: float
    avg_recall: float
    avg_f1: float
    avg_type_accuracy: float
    distribution_threshold: float = 0.95

    @property
    def passed(self) -> bool:
        return self.avg_f1 >= self.distribution_threshold


def _index_by_canonical_id(elements: tuple[BuildingElement, ...]) -> dict[str, BuildingElement]:
    out: dict[str, BuildingElement] = {}
    for e in elements:
        cid = _spatial_canonical_id(e)
        # 同 cid 既存なら 信頼度高いほうを採用
        if cid in out:
            existing_conf = (
                out[cid].inference.confidence if out[cid].inference else 0.0
            )
            new_conf = e.inference.confidence if e.inference else 0.0
            if new_conf > existing_conf:
                out[cid] = e
        else:
            out[cid] = e
    return out


def benchmark(
    scenario: str,
    ground_truth: BuildingModel,
    extracted: BuildingModel,
) -> BenchmarkResult:
    """1 シナリオの抽出結果を ground truth と比較."""
    gt_index = _index_by_canonical_id(ground_truth.elements)
    ext_index = _index_by_canonical_id(extracted.elements)

    gt_cids = set(gt_index.keys())
    ext_cids = set(ext_index.keys())

    matched_cids = gt_cids & ext_cids
    fp_cids = ext_cids - gt_cids
    fn_cids = gt_cids - ext_cids

    type_correct = 0
    for cid in matched_cids:
        if gt_index[cid].element_type == ext_index[cid].element_type:
            type_correct += 1

    tp = len(matched_cids)
    fp = len(fp_cids)
    fn = len(fn_cids)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    type_acc = type_correct / tp if tp > 0 else 0.0

    return BenchmarkResult(
        scenario=scenario,
        n_ground_truth=len(gt_index),
        n_extracted=len(ext_index),
        n_true_positive=tp,
        n_false_positive=fp,
        n_false_negative=fn,
        type_correct=type_correct,
        precision=precision,
        recall=recall,
        f1=f1,
        type_accuracy=type_acc,
    )


def run_suite(
    pairs: tuple[tuple[str, BuildingModel, BuildingModel], ...],
    *,
    distribution_threshold: float = 0.95,
) -> BenchmarkSuite:
    """複数シナリオを一括 benchmark."""
    results = tuple(
        benchmark(name, gt, extracted) for name, gt, extracted in pairs
    )
    if not results:
        return BenchmarkSuite(
            results=(),
            avg_precision=0.0, avg_recall=0.0, avg_f1=0.0, avg_type_accuracy=0.0,
            distribution_threshold=distribution_threshold,
        )
    avg_p = sum(r.precision for r in results) / len(results)
    avg_r = sum(r.recall for r in results) / len(results)
    avg_f1 = sum(r.f1 for r in results) / len(results)
    avg_t = sum(r.type_accuracy for r in results) / len(results)
    return BenchmarkSuite(
        results=results,
        avg_precision=avg_p, avg_recall=avg_r, avg_f1=avg_f1, avg_type_accuracy=avg_t,
        distribution_threshold=distribution_threshold,
    )


def format_suite_report(suite: BenchmarkSuite) -> str:
    """人が読めるシナリオ別 + aggregate report."""
    lines: list[str] = []
    lines.append("═" * 70)
    lines.append("Conquest Accuracy Benchmark Report (Pack #83 Phase 4)")
    lines.append("═" * 70)
    lines.append(
        f"配布閾値: F1 ≥ {suite.distribution_threshold:.2f}  "
        f"判定: {'✅ PASS (配布 READY)' if suite.passed else '❌ FAIL (要改善)'}"
    )
    lines.append("")
    lines.append(
        f"{'scenario':<30s}{'GT':>4s}{'Ext':>5s}{'TP':>4s}{'FP':>4s}{'FN':>4s}"
        f"{'P':>7s}{'R':>7s}{'F1':>7s}{'Tacc':>7s}"
    )
    lines.append("-" * 70)
    for r in suite.results:
        lines.append(
            f"{r.scenario:<30s}{r.n_ground_truth:>4d}{r.n_extracted:>5d}"
            f"{r.n_true_positive:>4d}{r.n_false_positive:>4d}{r.n_false_negative:>4d}"
            f"{r.precision:>7.2f}{r.recall:>7.2f}{r.f1:>7.2f}{r.type_accuracy:>7.2f}"
        )
    lines.append("-" * 70)
    lines.append(
        f"{'AVG':<30s}{'':<13s}"
        f"{'':<12s}"
        f"{suite.avg_precision:>7.2f}{suite.avg_recall:>7.2f}{suite.avg_f1:>7.2f}"
        f"{suite.avg_type_accuracy:>7.2f}"
    )
    return "\n".join(lines)
