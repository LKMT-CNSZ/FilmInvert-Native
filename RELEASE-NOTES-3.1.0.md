# FilmInvert Native 3.1.0

2026-10-30 · Windows x64 · 正式版 / Stable release

## 本版更新

- 片边排除测光：减少扫描片边对自动亮度的干扰，保留全幅兼容方式。
- 新增高光、阴影、白场、黑场、局部色调映射和多曝光 HDR 合成。
- 统一导出入口支持 16bit SDR TIFF、float32 线性 HDR TIFF 与 Windows HDR JPEG XR (.jxr)。
- Windows 原生 scRGB HDR 主编辑画布，支持实时调色、取样、缩放和撤销；HDR 显示仍为实验功能。
- 1.2 秒吉祥物启动显影，以及每次实际切帧的 1.6 秒柔边显影；调色及直接重开已有编辑不重播。
- 修复底部导航卡片重叠，优化 SDR / HDR 显影动画绘制。
- 使用新的红白 FilmInvert 图标；快捷方式采用带内容哈希的独立 ICO 路径，修复 Windows 旧图标缓存。
- 保留简体中文 / English 界面、各图独立编辑、工程保存与批量工作流。

正式版沿用 rc14 的处理与交互，更新发行版本、文档和打包元数据。安装版与免安装版均附带独立 Python / Qt 运行时。安装到新的空目录；免安装版完整解压后运行启动 CMD，首次启动生成本机快捷方式。原片和已有编辑请继续保留。

## 验证与限制

本机验证包含 44 项随包回归、隔离安装/卸载、实际快捷方式图标引用、最终免安装 ZIP 及源码/依赖 SHA256。具体通过结果以同版 validation.json、验证说明和 SHA256 清单为准。

HDR 显示尚未完成仪器与跨设备视觉验收。JXR 面向 Windows HDR 查看，暂不支持重新导入 FilmInvert，继续编辑请保留工程和 HDR TIFF 母版。普通整卷与批量输出仍为 SDR。应用和安装包未添加发行者签名。

## English

FilmInvert Native 3.1.0 is a stable Windows x64 release based on rc14. It includes film-edge-aware metering, expanded tonal controls, local tone mapping, multi-exposure HDR merging, and unified 16-bit SDR TIFF, float32 HDR TIFF and Windows HDR JPEG XR export.

The native Windows scRGB HDR editing canvas remains experimental. This release also includes the 1.2-second mascot startup reveal, the 1.6-second soft reveal on photo/frame navigation, navigation layout fixes, smoother animation rendering, and the new red-and-white application icon. Content-specific ICO paths fix stale Windows shortcut icons.

Both installer and portable editions include Python and Qt. Fully extract the portable ZIP and run its launch CMD to create the local shortcut. The release includes matching source, verification evidence and SHA256 checksums. Verification covers 44 shipped regression checks, isolated installation/uninstallation, actual shortcut icon references and final archive integrity.

HDR display has not undergone instrument-based or cross-device visual acceptance. JXR is intended for Windows HDR viewing and cannot yet be reopened in FilmInvert; retain the project and HDR TIFF master for editing. Whole-film and batch output remain SDR. The installer and application are unsigned.
