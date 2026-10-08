# CRRV-Bench 评测模块

`mingjing.bench` 是明镜的离线评测基础模块。它只读取事实核查流水线输出并计算指标，不参与事实抽取或判定，因此可以在没有模型、MCP 或网络连接的环境运行。

## 输入格式

输入可以是 JSON 数组、包含 `records`/`samples`/`items`/`data` 字段的 JSON 对象、由样本 ID 映射到对象的 JSON，或 JSONL/NDJSON 文件。每条记录至少包含金标准和预测状态之一：

```json
{
  "id": "c-001",
  "gold": "violated",
  "prediction": "verified",
  "error_type": "E1",
  "is_trap": false,
  "score": 0.82,
  "evidence_hit": true,
  "repair_pass": true
}
```

状态会归一化为 `violated`、`verified`、`abstained`。布尔值 `true/false` 分别表示违规/正确；缺失预测按 `abstained` 处理。`is_trap` 用于计算陷阱集误报率，`score` 可用于工作点扫描。

## 命令行

在 `mainProj` 目录执行：

```powershell
python -m mingjing.bench.evaluate --samples mingjing/bench/example.jsonl --pretty
```

也支持金标准和预测分离的文件：

```powershell
python -m mingjing.bench.evaluate `
  --gold runs/gold.jsonl `
  --predictions runs/predictions.jsonl `
  --thresholds 0.3,0.5,0.7 `
  --out runs/bench-report.json `
  --pretty
```

报告包含总体混淆矩阵、precision/recall/F1、Wilson 95% 区间、按错误类型指标、陷阱误报、证据命中率、修复通过率和 `working_points`。加入 `--no-records` 可省略报告中的规范化样本记录。

## Python API

```python
from mingjing.bench import evaluate_records, load_samples, scan_working_points

records = load_samples("runs/predictions.jsonl")
report = evaluate_records(records)
curve = scan_working_points(records, thresholds=[0.25, 0.5, 0.75])
```

`mingjing.bench.stats` 还提供 `wilson_interval`、`confusion_counts`、`binary_metrics` 和 `precision_recall_f1`，全部只依赖标准库。
