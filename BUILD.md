# FilmInvert Native 3.1.0-rc12 可复现构建说明

目标为 Windows x64，使用官方独立 CPython 3.13.17 + 固定 wheel；运行用户不需要另装 Python。源码包包含当前应用、测试、打包/安装脚本、合成样例及许可，不包含私人原片、validation、用户库或运行时二进制。重建需下载依赖，之后启动/修片/导出离线完成。所称可复现是能够以同一源码与固定依赖重新构建及验证，未宣称安装 EXE 在不同机器上逐字节相同。

## 1. 准备

使用 Windows x64 上 Python 3.11+ 运行构建脚本。项目原工作区可用 runtime/python.exe；源码 ZIP 不包含该开发解释器。requirements-build.txt 保留历史 PyInstaller 构建依赖记录，当前正式流程不使用 PyInstaller，不要求先全局 pip 安装。

```powershell
python tools/fetch_compat.py --proxy ""
```

依赖下载记录已固定在 build/compat-dependencies.json；fetch 脚本核对上游固定版本及 SHA256，下载到 build/wheels。默认代理是该工作区原有 127.0.0.1:7897，其他电脑用上面的空代理参数或自己的代理。核对下载记录与源码附带记录后再构建。

安装编译器为 Inno Setup 6.7.3（https://jrsoftware.org/），官方文件：
https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe

已记录 SHA256：9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732；本机下载文件 Authenticode 为 Valid，发行者 Pyrsys B.V.。它是构建工具的签名，不是 FilmInvert 安装包签名。可用 tools/fetch_installer.ps1 下载并验证，然后以官方 portable 模式解压到 build/release-tools/InnoSetup，不全局安装。官方说明：https://jrsoftware.org/ishelp/topic_technotes.htm 。中文安装翻译来自同版本官方仓库，已附在 tools/ChineseSimplified.isl，保留贡献者说明。

## 2. 构建、验证、打包

在源码根目录执行（命令必须等上一条成功后再继续）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/fetch_installer.ps1
python tools/render_guide.py
python tools/build_compat.py
python tools/test_compat_distribution.py
python tools/test_compat_portability.py
python tools/package_stable.py --stage-only
python tools/build_stable_installer.py
python tools/test_stable_installation.py
python tools/package_stable.py
python tools/verify_compat_archive.py
python tools/verify_stable_release.py
```

VERSION.txt 为 3.1.0-rc12，desktop_model.VERSION、portable-layout、installed-layout、安装器版本及文档必须一致。构建输出 dist/3.1.0-rc12-compat/FilmInvertNative；最终 package_stable 只允许清单内文件，排除 data/out/app/build、缓存、日志与本机入口。先 stage-only 打包准备可安装文件，再编译；测试完成后把实测结论写入 RELEASE-VALIDATION-3.1.0-rc12.md，再重新打包/编译并对最终安装包执行安装测试，最后生成 SHA256 和验证最终归档。

安装编译：python tools/build_stable_installer.py --iscc "其他目录/ISCC.exe"。文件列表由干净 bundle 的 manifest 生成，不把测试产生的库或导出物一起安装。默认使用安装脚本固定的版本目录，用户级安装；安装到非空目录会失败，卸载不递归删除用户目录。

## 3. 验证路径与安全范围

44 项随包隔离检查含诊断及 43 个脚本入口。它们使用随包解释器，污染 PYTHONHOME/PYTHONPATH、限制 PATH、切到中文空格无关工作目录。回归包含合成整卷、真实 Qt 事件、独立进程队列、暂停/恢复、备份/迁移与 TIFF 像素比较；真实 RAW 既有证据与本轮回归要区分。

安装测试仅在 build/stable-install-test 的中文空格目录，当前用户卸载登记临时建立后移除；若检测到同 AppId 现有安装就停止，避免触及现用安装。测试环境把应用 LOCALAPPDATA 指向隔离目录，安装程序仍使用 Windows 当前用户的注册/快捷方式设施。不会升级 D 盘安装。测试包启动、库恢复、非空目录拒绝、卸载及数据/原片/成品保留。真实 rc13 库兼容探针需已有 dist/3.0.0-rc13-compat/FilmInvertNative；源码重建机器缺少基线时应明示跳过，不能假记通过。

源码与分发逐文件 SHA256 对照由 verify_stable_release.py 执行；旧基线哈希列在本次验证记录中。release/SHA256-3.1.0-rc12.json 和 .txt 覆盖当前正式交付，不覆盖旧版本文件。第三方许可保留，不把校验或本机测试写成多机/安全软件/色彩认证。

## 4. 工具与证据

build_stable_installer.py、stable-setup.iss 为当前安装流程；package_stable.py 为正式打包入口。build_native.py/package_release.py/package_compat.py 为历史布局脚本，不能替代正式版隐私清单流程。validation 只留工作区，不装入产品或源码 ZIP；本次发布的可携带验证记录为 RELEASE-VALIDATION-3.1.0-rc12.md 与 release/3.1.0-rc12-交付验证.json。
# 3.1.0-rc12 language update

The current workspace builds 3.1.0-rc12 with bilingual UI and experimental HDR features. Neutral processing retains legacy results; new imports opt into edge-aware metering. Render both guides with `tools/render_guide.py` and `tools/render_guide.py --source README.en.md --output README.en.html --language en`, then use `tools/build_compat.py`, `tools/test_compat_distribution.py`, `tools/package_stable.py --stage-only`, `tools/build_stable_installer.py`, and `tools/package_stable.py`. `tools/test_i18n.py` is included in the isolated suite. Package names follow `VERSION.txt`; historical 3.0.0 deliverables remain in place. Historical 3.0.0 evidence remains in its original archives.

