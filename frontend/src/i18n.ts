export const copy = {
  title:'明镜金融 Agent', subtitle:'研报核查 · 财报分析 · 证据追问', newTask:'新建金融任务', settings:'设置', history:'任务与会话',
  check:'研报纠错核查', analysis:'财报分析', task:'任务', source:'原文', coverage:'覆盖账本', chat:'材料追问',
  verified:'已核实', violated:'有误', abstained:'弃权', pending:'待处理', advisory:'复核提示', source_quote:'原文引用', source_fact:'原页补证事实', out_of_scope:'范围外', rule_excluded:'规则排除',
  running:'运行中', queued:'排队中', completed:'完成', partial:'部分完成', failed:'失败', cancelled:'已取消', interrupted:'中断', ready:'就绪',
  empty:'尚无任务', close:'关闭', save:'保存设置', cancel:'取消', resume:'恢复', start:'开始任务', demo:'运行内置离线演示',
  evidence:'来源证据', verdict:'核查详情', trace:'执行记录', unknown:'未知',
}
export function label(value:string) {return copy[value as keyof typeof copy]||value}
export const markdownLabels={code:{copyLabel:'复制',copiedLabel:'已复制'},footnotes:'脚注'}
export const dockLabels={emptyPane:'选择陈述或证据查看详情',splitPane:'拆分证据面板',splitPaneDisabled:'面板数已达上限',splitPaneNarrow:'当前面板太窄',closeTab:'关闭标签',addTab:'新建面板',dockFloat:'停靠面板',closeFloat:'关闭浮动面板',dropZone:{left:'左侧',right:'右侧',top:'上方',bottom:'下方',center:'此处'}}
