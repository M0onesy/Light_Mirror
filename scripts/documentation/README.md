# 文档处理工具

当前原件只在 `docs/project/`。历史dev3修订脚本保留转换方案，不能直接回写本版原件、重生成整套开发说明或复制 `requirements/`。

- `修订文档_dev3.py --original <历史融合包目录> --out artifacts/documents/<新草稿>`：重现dev3的历史文档转换，生成独立草稿，不自动替换当前文件。
- `验证文档_dev3.py --original <历史原DOCX> --out evidence/documentation/<新验证>`：对当前原件做结构与图片保存检查。
- `生成评审PDF_dev3.py --out artifacts/documents/<新渲染>`：从当前DOCX生成独立PDF、HTML和渲染收据，不能覆盖现PDF。
- `导出计划书_dev3.ps1 -OutputDirectory artifacts/documents/<新Word导出>`：复制DOCX到新目录后刷新Word字段并导出。
- `封存文档_dev3.py --out evidence/documentation/<新收据>.json`：只登记原件SHA-256，不声称视觉或金融验收通过。
- `完整源码打包_dev3.py`：兼容入口，实际实现为 `scripts/create_full_source.py`。

以上工具并不把版本、成绩、团队姓名或专业签认改成“通过”。计划书内容确认、视觉复核与正式提交仍需对应实际记录。
