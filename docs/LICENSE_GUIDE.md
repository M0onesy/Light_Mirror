# 明镜开源与第三方许可说明

明镜自有代码以 **GNU Affero General Public License version 3（AGPL-3.0-only）** 发布，完整文本见根目录 [LICENSE](../LICENSE)。本声明覆盖明镜编写的Python金融核查代码、金融插件、项目脚本及原创界面修改；第三方原文件按其原有许可保留，不能因本项目的LICENSE改换许可证。

DeepSeek Harness、Node、Cordis及其生态包、React等依赖的版权声明和许可证应保留。锁定上游源码位于 `vendor/harness`，界面复用来源位于 `frontend/vendor/harness`，生产载体以其manifest及许可文件为准；来源清单中的缺失Git commit保持 `null`，不能伪造上游版本身份。

PyMuPDF/MuPDF采用AGPLv3或Artifex商业许可双许可。本项目没有声明取得Artifex商业许可，分发当前使用AGPL组件的程序应按相应许可提供对应源码和许可文本。PyInstaller的bootloader分发例外不改变其他组件的许可要求。

分发EXE时，应提供同版本明镜源码、必要构建脚本、依赖锁、Harness源码来源及其许可，使接收者能取得对应源码；仅提供30MB核心审阅包不足以替代完整对应源码。可使用GitHub的准确commit/标签与完整源码归档建立EXE到源码的映射，不能只链接一个会持续变化的首页。

修改AGPL程序后让用户通过网络交互时，应按AGPL第13条向这些用户提供相应修改版源码的获取机会。协作者提交的原创修改继续采用本项目许可；若贡献带入第三方代码，应说明来源、许可和修改范围。

模型API服务不因本项目开源而变为开源模型或免费服务。DeepSeek、Moonshot、OpenAI等供应商的账号、计费、数据和服务条款仍由相应服务约定。API Token不属于交付内容。真实研报、财报和会计准则文本的再分发权单独核对，不把公开可下载等同于可重新打包分发。

第三方组件清单见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，原始Python许可副本及SHA-256见 `docs/licenses/index.json`；载体内的Node/npm原始许可依据生产manifest保存。本说明解释当前发布方式，不额外授予第三方权利。
