# 第三方依赖与许可

明镜自有代码采用根目录AGPL-3.0-only，许可适用范围见 [LICENSE_GUIDE.md](LICENSE_GUIDE.md)。第三方原文件保留其原许可证，不由根LICENSE替换。确定性数字内核不依赖网络或模型SDK；PDF、Agent与产品层按以下组件分别登记。

| 组件 | 用途 | 许可与发布方式 |
| --- | --- | --- |
| Python | 应用运行和金融内核 | Python Software Foundation License，生产包保留原文 |
| PyMuPDF / MuPDF | PDF文本、坐标、证据图片 | AGPLv3或Artifex商业双许可；当前不宣称商业授权 |
| DeepSeek Harness | 原生Agent Loop与会话 | 按锁定 `vendor/harness/LICENSE` 保留版权及许可 |
| Node及载体依赖 | 原生金融运行时 | manifest登记必要依赖及原始许可，不含开发node_modules |
| Harness界面、React等UI组件 | 复用界面与本地渲染 | 来源清单、原始LICENSE与依赖锁保留 |
| 官方MCP SDK | 标准工具协议 | 按当前锁定版本保留MIT许可 |
| PyInstaller | 单EXE构建 | 构建工具，GPL含bootloader分发例外 |
| pdftotext / OCR | 可选解析降级或增强 | 默认不内嵌；实际引入时逐项登记对应许可 |
| 模型API | 可选语义绑定与报告组织 | 用户选择供应商并遵循该服务条款，不随包提供账号或Token |

完整Python清单由构建脚本依据安装metadata生成于 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，原始文本与SHA-256位于 `docs/licenses/index.json`。finance carrier和Harness源码锁记录其各自来源、版本与许可文件；不能用旧完整Office运行时清单宣称新金融载体已审计。

分发EXE须同时提供对应版本源码和许可。核心审阅包只供审阅，不能替代完整对应源码。真实报告与准则材料的再分发权限另行核对，个人配置、密钥、API响应缓存和私人运行记录不公开。
