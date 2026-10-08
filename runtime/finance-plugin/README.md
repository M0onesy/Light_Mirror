# 明镜原生 Harness 金融插件

此插件通过官方命名 `mingjing` profile 运行原版 `@deepseek-ai/dsh-agent-loop`，自有代码只负责 stdio RPC、受控模型路由、金融工具装配和会话生命周期。

可信 Python 宿主通过 `mj/session.open` 提供角色、资源锁、工具 JSON Schema 和允许工具。模型只提供业务参数。原生工具将执行身份注入 `mj/tool.dispatch`，宿主在访问资料和金融内核之前再次授权并校验完整 JSON Schema。Shell、Code Mode、Web Search、遥测和动态插件管理不进入金融 profile。

模型适配器复用上游 `DeepSeekAdapter` 和 `llm-pi-ai`。API Key 通过私有 `mj/credentials.resolve` 请求从 Python DPAPI 读取，只停留在进程内存，不进入配置、环境、会话、模型缓存或工具 schema。此首版不提供 OAuth 凭证写入。

`llm/stream` 插件先查询 `mj/model.cache.get`，成功请求保存 canonical request hash 和流块到 `mj/model.cache.put`。`replay_only=true` 时 cache miss 直接失败，禁止外连。数字判定仍须由 Python 内核重算；缓存模型文本不能充当真值。

会话接口提供 open、prompt、cancel、resume、close。恢复重新装配角色和金融工具，且要求资源锁、模型和原始配置描述匹配。`cancel` 返回前等待 `agent.whenIdle()`；关闭 owner handle 等待作用域完全释放。所有 `session/event` 和状态变化通过原生 SDK 通知字段投影。

开发和打包：`python scripts/build_harness_runtime.py`。原始源码没有 Git 元数据，receipt 使用 source archive SHA-256 和原版 lockfile SHA-256，明确 `git_commit=null`。只修改了上游 archive 构建元数据的支持，不改 AgentLoop。

运行时的 native executable、ripgrep 和完整 Office sidecar 是资源集合，唯一用户入口仍为 `MingJing.exe`。最终打包不能丢失它们的目录层次、ESM package manifests、原生 addon 和许可证。
