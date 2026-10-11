# 明镜开源与第三方许可说明

明镜自有代码以 **GNU Affero General Public License version 3（AGPL-3.0-only）** 发布，完整文本见根目录 [LICENSE](../LICENSE)。本声明覆盖明镜编写的Python金融核查代码、金融插件、项目脚本及原创界面修改；第三方原文件按其原有许可保留，不能因本项目的LICENSE改换许可证。

DeepSeek Harness、Node、Cordis及其生态包、React等依赖的版权声明和许可证应保留。锁定上游源码位于 `vendor/harness`，界面复用来源位于 `frontend/vendor/harness`，生产载体以其manifest及许可文件为准；来源清单中的缺失Git commit保持 `null`，不能伪造上游版本身份。

PyMuPDF/MuPDF采用AGPLv3或Artifex商业许可双许可。本项目没有声明取得Artifex商业许可，分发当前使用AGPL组件的程序应按相应许可提供对应源码和许可文本。PyInstaller的bootloader分发例外不改变其他组件的许可要求。

当前阶段EXE使用 **PyMuPDF 1.28.0 / MuPDF 1.28.0**。同版候选Release另提供 `MingJing-1.0.0.dev3-AGPL-third-party-sources.zip`，与明镜完整对应源码归档并列；本地位置为 `artifacts/source/third-party/`，不改写已冻结的明镜源码包。第三方包保留上游原始源码归档及逐归档下载校验收据：

- `pymupdf-1.28.0.tar.gz`来自 [PyPI官方源码下载](https://files.pythonhosted.org/packages/8e/e9/6d6c5d6c0a3551bffd47681a6240caf941727f195b45593cf20ab36f018f/pymupdf-1.28.0.tar.gz)，大小 **87,637,751字节**，SHA-256为 `e53f3567403a92da15caa9e7ae0164327fff48817e9f40175367fb9de524258d`。此sdist未内含MuPDF原生源码，其构建脚本指定MuPDF 1.28.0。
- MuPDF来源冻结为 [Artifex官方GitHub仓库](https://github.com/ArtifexSoftware/mupdf)的 `1.28.0`标签，指向 `205b8cf43551279d1215e88fe2845c5d595bade9`。本轮下载 [官方整包](https://mupdf.com/downloads/archive/mupdf-1.28.0-source.tar.gz)在 `casper.mupdf.com`重定向后超时，GitHub大包流也未能完整取得。第三方包实际包含6个取得并校验可读的子模块归档；MuPDF主源码及其他子模块**未完整内嵌**，状态明确为 `partial_official_mupdf_sources_not_fully_embedded`。
- 同包 `mupdf-source-receipt.json`依据官方 `.gitmodules`及树记录逐项冻结主库与18个子模块的仓库、准确提交、下载URL、装配位置及已取得归档的大小和SHA-256。其 `embedded=false`项目需另行获取：解包主归档到MuPDF根，再将各子模块归档去掉顶层目录后放入登记的 `thirdparty/...` 位置，并遵循官方构建说明。此获取清单不等于已经验证的完整MuPDF原生源码交付；补齐前不得作此声明。

请同时取得第三方源码包、明镜完整对应源码、依赖锁、构建脚本和原始许可，不能用核心审阅包替代。GitHub Release的 `SHA256SUMS`验证上述交付资产；重建时遵循上游构建说明和当前锁定版本。

分发EXE时，应提供同版本明镜源码、必要构建脚本、依赖锁、Harness源码来源及其许可，使接收者能取得对应源码；仅提供30MB核心审阅包不足以替代完整对应源码。可使用GitHub的准确commit/标签与完整源码归档建立EXE到源码的映射，不能只链接一个会持续变化的首页。

修改AGPL程序后让用户通过网络交互时，应按AGPL第13条向这些用户提供相应修改版源码的获取机会。协作者提交的原创修改继续采用本项目许可；若贡献带入第三方代码，应说明来源、许可和修改范围。

模型API服务不因本项目开源而变为开源模型或免费服务。DeepSeek、Moonshot、OpenAI等供应商的账号、计费、数据和服务条款仍由相应服务约定。API Token不属于交付内容。真实研报、财报和会计准则文本的再分发权单独核对，不把公开可下载等同于可重新打包分发。

第三方组件清单见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，原始Python许可副本及SHA-256见 `docs/licenses/index.json`；载体内的Node/npm原始许可依据生产manifest保存。本说明解释当前发布方式，不额外授予第三方权利。
