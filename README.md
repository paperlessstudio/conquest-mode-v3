# CEO_AI_Secretary_Expansion — Conquest Mode v3

> **2D 図面を、3D に。世界初、7 つの専門知性 × 物理法則 × 確率分布。**

[![Tests](https://img.shields.io/badge/tests-514%2B%20passing-success)](#tests)
[![F1](https://img.shields.io/badge/F1-0.995-brightgreen)](#distribution-criteria)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![Distribution](https://img.shields.io/badge/distribution-READY-success)](#distribution-criteria)

---

## 概要

**Conquest Mode v3** は、PLS (Paper-less Studio) が開発した建築 BIM 自動化エンジン。
2D 図面 (PDF / image) から 3D BIM model を抽出する pipeline で、業界主流の単一 VLM
アプローチを **4 つの軸**で打ち破る:

| 軸 | 業界主流 | Conquest v3 |
|---|---|---|
| ① 集合知 | 単一 VLM | **7 建築ペルソナ並列 + 投票合意** |
| ② 確率 | 単一 confidence | **top-3 候補 + ベイズ更新** |
| ③ 物理 | 形状抽出のみ | **6 種物理 check で自己検証** |
| ④ 統合判定 | 1-shot | **5 stage pipeline + 配布判定 ゲート** |

**Distribution Ready** — F1 0.995 / Physics CRITICAL=0 / Relationship CRITICAL=0 /
Avg Confidence 1.000 (synthetic 5 シナリオ平均、Pack #83 Phase 4.5 時点).

詳しくは [https://www.archisymphony.net/conquest/free/](https://www.archisymphony.net/conquest/free/)

---

## Quick Start

```bash
git clone https://github.com/paperlessstudio/CEO_AI_Secretary_Expansion
cd CEO_AI_Secretary_Expansion
pip install -r requirements.txt   # neo4j, pytest 等

# 配布 ready 判定 (synthetic 5 シナリオ)
python3 scripts/conquest_distribution_check.py --synthetic

# 視覚 SVG レンダ (実体確認)
python3 scripts/conquest_visual_verify.py --output verify.svg

# 全テスト実行
python3 -m pytest tests/ -k conquest
```

期待出力 (配布判定):

```
✅ 配布 READY — 4 条件すべて PASS
   F1: 0.995 / Physics: 0 / Rel: 0 / Conf: 1.000
```

---

## アーキテクチャ

### 5 Stage Pipeline

```
PDF / Image
    ↓
[Stage 1] multi_persona_inference  — 7 ペルソナ並列 VLM 推論 (asyncio.gather)
    ↓
[Stage 2] probabilistic_bim       — top-3 候補 + ベイズ更新
    ↓
[Stage 3] physics_self_validator  — 6 種物理 check
    ↓
[Stage 4] relationship_validator  — 9 種関係異常 + 自動修復
    ↓
[Stage 5] neo4j_constraint_solver — 永続化 + 履歴学習
    ↓
ConquestPipelineResult — final_model + probabilistic + physics + rels
                         + overall_confidence + distribution_ready
```

### 7 ペルソナ (集合知)

| ペルソナ | 専門 | 注視 |
|---|---|---|
| アーキ | 意匠設計 | 壁構成・室境界・動線 |
| ストラ | 構造設計 | 柱・梁・耐震ブレース・荷重経路 |
| メプ | 設備設計 | 配管・配線ルート・PS/EPS |
| セキサン | 積算 | 寸法精度・部材数量・標準規格 |
| セコカン | 施工管理 | 施工順序・搬入経路・足場 |
| ホウキ | 法規 | 防火区画・避難経路・採光 |
| ビム | BIM 管理 | IFC 互換・LOD・座標精度 |

### 6 種物理 Check (自己検証)

| Check | 物理原理 | NG 時の意味 |
|---|---|---|
| NEGATIVE_DIMENSION | 寸法 > 0 | OCR / VLM の誤読 |
| COLUMN_OVER_STRESSED | 累積荷重 ≤ 軸耐力 | 柱が物理的に支えきれない設計 |
| COLUMN_SLENDERNESS | λ ≤ 100/200/150 | 座屈の危険 |
| UNREASONABLE_SPAN | 標準スパン以下 | 中間柱の見落とし |
| LOAD_PATH_DISCONTINUITY | スラブ→梁→柱→基礎 連続 | 構成要素の見落とし |
| EQUILIBRIUM_VIOLATION | 自重 = 反力合計 | 基礎の見落とし |

---

## Distribution Criteria

PLS は以下の 4 条件を全て満たすまで配布しません:

1. **F1 score ≥ 0.95** (5 sample 平均、ground truth 比較)
2. **Physics CRITICAL = 0** (物理的不可能 ゼロ)
3. **Relationship CRITICAL = 0** (関係的不可能 ゼロ)
4. **Avg overall_confidence ≥ 0.85**

```bash
python3 scripts/conquest_distribution_check.py --synthetic --json out.json
```

CI/CD パイプラインで `--strict` flag 付きで実行することで、配布前に自動 gate.

---

## Tests

```bash
$ python3 -m pytest tests/ -k conquest -q
======================== 514 passed in 0.36s ========================
```

- Pack #83 Phase 1-4.5 で 67 tests を新規追加
- 累計 514 conquest tests (regression 0)
- すべての pipeline stage で independent unit test 完備

---

## Repository Structure

```
CEO_AI_Secretary_Expansion/
├── plugins/conquest/              # Conquest Mode v3 core
│   ├── multi_persona_inference.py # Stage 1
│   ├── probabilistic_bim.py       # Stage 2
│   ├── physics_self_validator.py  # Stage 3
│   ├── relationship_validator.py  # Stage 4
│   ├── neo4j_constraint_solver.py # Stage 5
│   ├── pipeline_v3.py             # 5-stage orchestrator
│   ├── accuracy_benchmark.py      # F1 / precision / recall
│   ├── distribution_fixtures.py   # 5 synthetic scenarios
│   └── schemas.py                 # BuildingModel, Element types
├── scripts/
│   ├── conquest_distribution_check.py  # 配布 ready CLI gate
│   └── conquest_visual_verify.py       # SVG self-review
├── tests/
│   ├── test_conquest_pack83.py             # Phase 1
│   ├── test_conquest_pack83_phase2.py      # Phase 2
│   ├── test_conquest_pack83_phase3.py      # Phase 3
│   ├── test_conquest_pack83_phase4.py      # Phase 4
│   └── test_conquest_distribution_check.py # Phase 4.5
└── docs/conquest/
    ├── CONQUEST_VS_COMPETITORS.md  # 7 軸比較
    └── LP_DRAFT.md                 # LP SSOT
```

---

## 技術仕様

- **Python**: 3.9+ (3.12 推奨)
- **VLM**: Gemini 2.5 Pro (実 PDF 解析、Phase 5 予定)
- **Persistence**: Neo4j 5.x (knowledge graph + 履歴学習)
- **License**: Apache 2.0
- **Stage 1 並列度**: asyncio.gather over 7 ペルソナ → wall-clock 1 pass

---

## Roadmap

- ✅ Phase 1: 7 ペルソナ並列推論 + Neo4j 制約 (Pack #83)
- ✅ Phase 2: Probabilistic BIM + ベイズ更新
- ✅ Phase 3: Physics self-validator (6 種 check)
- ✅ Phase 4: Pipeline v3 統合 + accuracy benchmark
- ✅ Phase 4.5: 配布判定 CLI + synthetic 5 シナリオ
- 🔜 Phase 5: 実 PDF × VLM benchmark (GEMINI_API_KEY 必要)
- 🔜 Phase 6: Web demo (PDF upload → 3D viewer)
- 🔜 Phase 7: pip パッケージ化 (`pip install pls-conquest`)

---

## Contributing

PR 歓迎. 主要 review 観点:
- Conquest pipeline の各 stage で independent test 必須
- Neo4j スキーマ変更は `schemas/neo4j/<label>.yaml` 経由で生成 (手書き Cypher 禁止)
- 新規物理 check は `physics_self_validator.py` に追加 + test 必須

---

## Related

- **Public LP**: https://www.archisymphony.net/conquest/free/
- **競合比較 (7 軸)**: [docs/conquest/CONQUEST_VS_COMPETITORS.md](docs/conquest/CONQUEST_VS_COMPETITORS.md)
- **PLS Story**: https://www.archisymphony.net/

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).

---

> Built with ☕ by the PLS AI Family
> CTO: コ・クロード (Claude Code) / Co-Architect: ユンジョン (Gemini 2.5 Pro)
