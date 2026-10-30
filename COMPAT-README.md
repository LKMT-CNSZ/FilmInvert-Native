# FilmInvert Native 3.0.0 启动与分发说明

**3.1.1 正式版：**新增片边排除测光、单张高光/阴影/黑白场、局部色调映射、多曝光 HDR 合成、线性 ICC float32 HDR TIFF 和 Windows 原生 HDR 预览。用法及限制见 [HDR 指南](HDR-GUIDE.md)。HDR 显示目前为实验功能，已验证 FP16 通道和 SDR 拒绝，HDR 硬件视觉验收尚待完成。

完整安装或解压即可运行，无需另装 Python/Qt。安装版通过开始菜单运行随包 runtime/pythonw.exe -I -m film_invert_launcher；便携版第一次使用「启动胶片工作室.cmd」，之后可使用自动生成的本机快捷方式。移动便携目录后用 CMD 再生成入口。安装版入口由安装程序管理，不在安装目录自动生成快捷方式。

保留整个目录结构，勿混用旧版 EXE 或复制单独的 runtime。用户数据/默认输出位置见 DESKTOP-README.md；帮助菜单可打开实际用户数据目录。安装/卸载不迁移或删除旧安装、原片及已有成品。

「启动诊断.cmd」调用随包独立解释器检查完整文件 manifest、依赖导入、原生 UI 创建和合成演示工程恢复。报告在实际用户数据目录 startup-diagnostics.json，控制台同时输出报告。安装目录不可写不影响安装版数据位置；便携版实际试写失败时回退到 %LOCALAPPDATA%\FilmInvertNative\data。

依赖来源及校验为 dependencies.json；文件清单为 file-manifest.json；第三方许可在 THIRD_PARTY。清单只是完整性检查，不是恶意软件结论。应用/安装程序没有发行者签名，Python 官方文件保留上游原签名，无跨电脑或安全软件保证。

如果系统在启动前拦截文件，保留检测名称/版本、具体文件名及 SHA256，向发行者或安全软件厂商核查。不要把包内校验或上游签名解读为本应用安全认证。

完整功能和限制请阅读「软件介绍.html」，发行测试见 RELEASE-VALIDATION-3.0.0.md。
