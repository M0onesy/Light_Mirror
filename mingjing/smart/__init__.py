"""明镜直传智能管线（smart pipeline）。

上传即核查：文档类型/公司/期间自动识别 → 知识库去重入库 → 财报通用表格抽取 →
研报陈述抽取与分级 → 多定义指标与口径推定 → Decimal 区间核查 → 错误归因与修改建议。

模型只做语义绑定与叙述润色，数值真值、公式与判定全部由本包的确定性代码产生。
"""
PIPELINE_VERSION = "smart-1.0.0.dev3"
PARSER_VERSION = "smart-parse-2"
EXTRACTOR_VERSION = "smart-tables-2"
