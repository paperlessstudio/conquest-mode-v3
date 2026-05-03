"""Pack #83 Phase 4.5 — 配布判定 CLI script (CI/CD 統合可能).

CEO directive 「無料配布が迫る」「逆ブランディング回避」 — 配布前ゲート.

判定基準 (4 条件すべて満たす必要):
  1. F1 score ≥ 0.95 (5 scenario 平均、ground truth 比較)
  2. Physics CRITICAL = 0 (物理的不可能 ゼロ)
  3. Relationship CRITICAL = 0 (関係的不可能 ゼロ)
  4. Avg overall_confidence ≥ 0.85

使い方:
  # synthetic regression (実 PDF 不要、CI でも動く)
  python3 scripts/conquest_distribution_check.py --synthetic

  # 実 PDF 集合 (Phase 5 — 要 GEMINI_API_KEY + ground truth fixtures)
  python3 scripts/conquest_distribution_check.py --pdf-dir samples/

  # JSON 出力 (CI artifact 用)
  python3 scripts/conquest_distribution_check.py --synthetic --json out.json

Exit code:
  0 — 配布 READY (4 条件 PASS)
  1 — 配布 NOT READY (1 つでも FAIL)
  2 — 入力エラー / fixture 不足
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

# repo root を sys.path に追加 (script として実行する用)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.conquest.accuracy_benchmark import (  # noqa: E402
    BenchmarkSuite,
    format_suite_report,
    run_suite,
)
from plugins.conquest.distribution_fixtures import all_scenarios  # noqa: E402
from plugins.conquest.pipeline_v3 import (  # noqa: E402
    ConquestPipelineResult,
    format_pipeline_summary,
    run_pipeline_from_persona_results,
)


def _evaluate_distribution_conditions(
    suite: BenchmarkSuite,
    pipeline_results: tuple[ConquestPipelineResult, ...],
    *,
    f1_threshold: float = 0.95,
    confidence_threshold: float = 0.85,
) -> tuple[bool, list[str]]:
    """4 条件評価. (passed, failure_reasons) を返す."""
    failures: list[str] = []

    # 1. F1
    if suite.avg_f1 < f1_threshold:
        failures.append(
            f"F1 fail: avg_f1={suite.avg_f1:.3f} < {f1_threshold}"
        )

    # 2. Physics CRITICAL
    total_phys_critical = sum(
        r.physics.critical_count for r in pipeline_results
    )
    if total_phys_critical > 0:
        failures.append(
            f"Physics CRITICAL fail: {total_phys_critical} issues across "
            f"{len(pipeline_results)} scenarios"
        )

    # 3. Relationship CRITICAL
    total_rel_critical = sum(
        r.relationships.critical_count for r in pipeline_results
    )
    if total_rel_critical > 0:
        failures.append(
            f"Relationship CRITICAL fail: {total_rel_critical} issues across "
            f"{len(pipeline_results)} scenarios"
        )

    # 4. Avg confidence
    if pipeline_results:
        avg_conf = sum(
            r.overall_confidence for r in pipeline_results
        ) / len(pipeline_results)
        if avg_conf < confidence_threshold:
            failures.append(
                f"Confidence fail: avg={avg_conf:.3f} < {confidence_threshold}"
            )

    return (len(failures) == 0, failures)


def _run_synthetic_check(
    *,
    f1_threshold: float,
    confidence_threshold: float,
    verbose: bool,
) -> dict:
    """Synthetic 5 scenario で配布判定."""
    scenarios = all_scenarios()

    if verbose:
        print(f"Loaded {len(scenarios)} synthetic scenarios:")
        for name, _, _ in scenarios:
            print(f"  - {name}")
        print()

    # benchmark
    suite = run_suite(scenarios, distribution_threshold=f1_threshold)

    # pipeline (per scenario, 7 ペルソナ全員 同じ extracted を投票したと仮定)
    pipeline_results: list[ConquestPipelineResult] = []
    for name, _gt, extracted in scenarios:
        persona_results = {
            pid: extracted
            for pid in ["archi", "structure", "qs", "code", "mep", "constmgmt", "bim"]
        }
        result = run_pipeline_from_persona_results(
            persona_results,
            confidence_threshold=confidence_threshold,
        )
        pipeline_results.append(result)
        if verbose:
            print(f"--- {name} ---")
            print(format_pipeline_summary(result))
            print()

    pipeline_tuple = tuple(pipeline_results)

    passed, failures = _evaluate_distribution_conditions(
        suite, pipeline_tuple,
        f1_threshold=f1_threshold,
        confidence_threshold=confidence_threshold,
    )

    return {
        "mode": "synthetic",
        "passed": passed,
        "failures": failures,
        "f1_threshold": f1_threshold,
        "confidence_threshold": confidence_threshold,
        "n_scenarios": len(scenarios),
        "suite": {
            "avg_precision": suite.avg_precision,
            "avg_recall": suite.avg_recall,
            "avg_f1": suite.avg_f1,
            "avg_type_accuracy": suite.avg_type_accuracy,
            "scenarios": [asdict(r) for r in suite.results],
        },
        "physics_critical_total": sum(
            r.physics.critical_count for r in pipeline_tuple
        ),
        "relationship_critical_total": sum(
            r.relationships.critical_count for r in pipeline_tuple
        ),
        "avg_overall_confidence": (
            sum(r.overall_confidence for r in pipeline_tuple) / len(pipeline_tuple)
            if pipeline_tuple else 0.0
        ),
        "report": format_suite_report(suite),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Conquest Mode v3 配布判定 (Pack #83 Phase 4.5)",
    )
    parser.add_argument(
        "--synthetic", action="store_true",
        help="synthetic 5 scenario で判定 (実 PDF 不要、CI 用)",
    )
    parser.add_argument(
        "--pdf-dir", type=Path, default=None,
        help="実 PDF 集合 (Phase 5 — 要 GEMINI_API_KEY + ground_truth/*.json)",
    )
    parser.add_argument(
        "--f1-threshold", type=float, default=0.95,
        help="F1 配布閾値 (default 0.95)",
    )
    parser.add_argument(
        "--confidence-threshold", type=float, default=0.85,
        help="overall_confidence 配布閾値 (default 0.85)",
    )
    parser.add_argument(
        "--json", type=Path, default=None,
        help="JSON 結果を path に出力 (CI artifact 用)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="サマリ以外を抑制",
    )

    args = parser.parse_args(argv)

    if not args.synthetic and args.pdf_dir is None:
        print("ERROR: --synthetic か --pdf-dir のどちらか必須", file=sys.stderr)
        return 2

    if args.pdf_dir is not None:
        print(
            "ERROR: --pdf-dir は Phase 5 (実 VLM 統合) で実装予定. "
            "現状は --synthetic を使ってください.",
            file=sys.stderr,
        )
        return 2

    result = _run_synthetic_check(
        f1_threshold=args.f1_threshold,
        confidence_threshold=args.confidence_threshold,
        verbose=not args.quiet,
    )

    if not args.quiet:
        print(result["report"])
        print()
        print("=" * 70)
        print("配布判定 (4 条件)")
        print("=" * 70)
        print(f"  1. F1 ≥ {args.f1_threshold}: avg_f1={result['suite']['avg_f1']:.3f}")
        print(f"  2. Physics CRITICAL = 0: {result['physics_critical_total']}")
        print(f"  3. Relationship CRITICAL = 0: {result['relationship_critical_total']}")
        print(
            f"  4. Confidence ≥ {args.confidence_threshold}: "
            f"avg={result['avg_overall_confidence']:.3f}"
        )
        print()
        if result["passed"]:
            print("✅ 配布 READY — 4 条件すべて PASS")
        else:
            print("❌ 配布 NOT READY")
            for f in result["failures"]:
                print(f"  ✗ {f}")

    if args.json is not None:
        # report は人間用なので JSON からは除外 (size 抑制)
        json_payload = {k: v for k, v in result.items() if k != "report"}
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(json_payload, indent=2, ensure_ascii=False))
        if not args.quiet:
            print(f"\nJSON output: {args.json}")

    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
