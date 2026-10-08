# 追问原文引用证据

追问可引用锁定报告或原草稿的原文。引用始终标为 `unverified_source_quotation`，界面说明为“原文引用，未作数字核查”。引用中的日期、金额与结论属于披露原文，不成为事实、公式或 verified 判定。

原生金融工具按原文块建立引用槽位。成功提交的报告只能保存本轮已经读取且实际使用的槽位。Python 宿主再验证原结果哈希、公司、资源登记、全量文档 SHA-256、页码、块 ID、块哈希、引用哈希及坐标，将引用清单和 PDF 图片写入会话自己的 `message-*` 目录。原运行的 `results.json`、证据清单和输出清单保持只读。

助手消息提供 `message_id`、`source_references` 和 `source_reference_manifest_sha256`。引用包含 `evidence_id`、`title`、`name`、页码与各来源哈希；不向客户端公开消息目录或图片文件路径。

沿用已有只读证据接口：

```text
GET /api/evidence/{evidence_id}?session={session_id}&message={message_id}&run={source_run_id}
GET /api/evidence/{evidence_id}?session={session_id}&message={message_id}&run={source_run_id}&format=png
```

请求不能提交路径、坐标或页码。接口检查本条助手消息确实登记该证据，并再次验证原材料、引用清单和图片哈希。不同会话、不同消息、不同运行或不同公司的引用被拒绝；材料或缓存变化返回错误。非 PDF 原文保留结构化引用，`image_available=false`，不能请求 PDF 图片。

本地回归直接执行金融 dispatcher 搜索与受控报告提交，再由真实证明记录生成引用，最后通过 HTTP 读取 JSON 和 PNG；这不等于供应商真实模型验收。
