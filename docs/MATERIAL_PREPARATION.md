# 大目录材料准备

`load_or_extract_facts`、`extract_facts`、`extract_text` 和 `partition_document` 提供可选参数 `cancel_event / progress_callback / deadline_monotonic`。回调接受 `(phase, **fields)`；字段只有文件名、页码、筛选计数和公司，不提供财务事实值。

有 `index.csv` 时先按公司筛选，再验证已选文件的大小与 SHA-256。索引存在但没有匹配记录时返回空材料，不退回读取未登记文件。索引元数据仍不是数字事实。

无索引时一次遍历目录，根据股票代码与公司目录身份跳过其他公司；不会分别为 PDF、Markdown 和文本重走目录。剪枝不会把混合语料根目录里的匿名文件授权成所选公司。目录链接和 junction 不被跟随。

取消与截止时间在目录、索引、哈希分块、文本读取、PDF 页面及事实行之间检查。PDF 降级转换使用可追踪的独立子进程，取消时只停止宿主创建的转换进程，不终止其他程序。原生 PDF 单次文本提取和 JSON 单次解码仍在调用返回后检查，不能保证任意复杂页面在固定毫秒内停止。

Web 源码运行的 CLI 使用内部 `mingjing-progress.v1` 进度行，宿主将其转换为任务阶段事件。冻结模式和原生 Agent 直接使用回调与同一任务取消信号、截止时间。准备失败或取消不返回半截事实库。

新 CLI 核查在 API 或内核判定前冻结源公司、科目、期间、口径、关系和基期，保存 `source_dimensions.json` 并将哈希纳入材料清单。Agent 使用同一源维度实现，旧冻结任务的源策略不被重写。

开发测试只使用小型虚构语料和已曝光材料。`runs/development-parser-cancellation.json` 登记了一份先前曝光的并行科技年报在第一页停止解析的实测；它不代表全量语料吞吐测试或金融质量验收。没有扫描或复制全量外部语料。
