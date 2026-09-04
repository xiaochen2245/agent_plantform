# 金标集（#35）

20-30 对 docx 评分表 → 人工标注 → `#33` 抽取器的评分项级召回周回归（监控线 ≥95%，非交付门槛）。

## 目录结构

```
scripts/golden/cases/<用例名>/
  source.txt     评分表文本块（从 RAGFlow chunks 导出拼接，或直接从 docx 复制表格文本）
  expected.json  人工标注（评分项必须齐全——召回的分母）
```

`expected.json` 格式：

```json
{
  "total": 100,
  "items": [
    {"seq": "1", "item": "投标报价", "score": 30, "criteria": "以基准价计算", "category": "价格"}
  ]
}
```

标注口径：`score` 取数字；`category` ∈ 价格/技术/商务（判断不出留空）；`criteria` 照抄原文（含公式）。

## 运行

```bash
cd backend && .venv/bin/python ../scripts/golden/regress.py            # 真跑（需 .env 的 SiliconFlow key）
cd backend && .venv/bin/python ../scripts/golden/regress.py --dry-run  # 只验标注格式与匹配逻辑
```

周回归低于 95% exit 1；失败项打印漏检清单，先看是标注口径问题还是抽取退化，再决定是否回滚/调 prompt。

## 状态

- harness + 合成样例已入库（`example-mock` 用例，`--dry-run` 自检用）
- 真实标注（20-30 对，需领域专家）：**blocked-owner**，人力决策见 issue #35
