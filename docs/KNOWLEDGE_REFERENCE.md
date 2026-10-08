# 外部会计知识库

任务使用 Web 中保存的 `workspace.knowledge_root`，每次运行冻结为 `knowledge_root`。CLI 可用 `--knowledge-root`；留空时仍可执行完整纯内核核查。只读取这一主根，支持 PDF、DOCX、UTF-8 TXT 和 Markdown，不混入另一个目录的副本。

每份原文件和元数据索引保存 SHA-256，字节相同的副本合并为一份检索文档，全部来源路径仍进入锁。原文按物理 PDF 页或逻辑文本段落定位。Markdown/DOCX 没有可靠物理页时保存 `page: null`，不生成虚假的 PDF 页码。没有文本层的 PDF、无法解析的文件和条件日期保留为明确诊断。

元数据仅来自明确的文档头或根目录的 `knowledge_index.json`，正文年份不会变成公开日期或生效日期。项目现有 Markdown 头中的“发布、生效、来源、状态、文号”均属于未独立核验的来源声明。“现行有效”不能使材料成为签发规则。截止日前未知或未来公开日期不返回可引用正文；未来生效日期的已公开原文可以作为明确标注的原文引用，但不能视为当期适用准则。

明确的索引格式：

```json
{
  "schema_version": "mingjing-knowledge-metadata.v1",
  "documents": [
    {
      "file": "准则.pdf",
      "sha256": "完整64位SHA-256",
      "title": "准则名称",
      "publish_date": "2024-01-01",
      "effective_date": null,
      "version": null,
      "source_url": "https://example.org/source"
    }
  ]
}
```

`mj_kb_list`、`mj_kb_search`、`mj_kb_read` 通过既有金融 dispatcher 校验真实角色、会话、资源锁、截止日、取消和预算。模型参数只接受分页、关键词和登记 ID。绑定者收到屏蔽数字的原文；追问角色得到受控 `source.kb.*` 引用槽位；报告角色不能读取外部知识原文。标准 MCP 的 `mj-rules` 复用这些工具。

引用保持 `reference_status: unverified_source_quotation`，增加 `material_kind: accounting_knowledge`、知识库锁、声明元数据和“准则声明未核验”标识。原文不能生成事实值、公式或判定。与内嵌规则的关联只有在原文 SHA-256 精确匹配已登记规则来源时产生；计算和判定仍使用内嵌规则卡。

每次任务生成 `knowledge_lock.json` 和 `knowledge-cache/`。原始材料、索引、解析缓存与知识锁进入运行清单。首次建立索引始终读取原文，冻结恢复和缓存重放才复用已锁定缓存。修改材料、索引、缓存或加载器后拒绝恢复，不改写旧任务。数字重放核验冻结输入；原生 Agent 与消息重放同时核验加载器和工具轨迹。

嵌套目录的冻结别名统一为 POSIX 相对路径，Windows 中文目录也可直接恢复。逻辑文本的重复段落保留独立 occurrence 和证据 ID，不因原文相同丢失定位。双盲角色的列表、搜索和读取均屏蔽原文及声明标题中的金额，登记 ID 和哈希保持可用于后续工具调用。DOCX 在 XML 解析阶段拒绝 DTD 和实体，包括 UTF-16 编码。证据材料变动返回 409，当前路径权限拒绝返回 403。
