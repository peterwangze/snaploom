Snaploom 是离线运行的截图、标注和贴图工具。安装包包含应用、运行环境和 OCR 模型。

- 框选期间按 Esc 先取消当次框选。重调选区时标注和遮盖仍对准原画面，比例锁定在贴近屏幕边缘时也生效。
- OCR 和条码识别后可继续编辑原截图，滚动截图取消保存或写入失败后保留已拼好的画面。命令行待确认截图有独立的输出按钮。
- 贴图列表显示缩略图、文字和显隐状态。单张隐藏与整组暂时隐藏分别处理，永久删除需确认。托盘提供关闭所有贴图鼠标穿透的入口。
- 历史刷新保留选中项，减少历史上限前确认删除范围。快捷键设置逐项编辑，保存前提示冲突。单击托盘打开工作台，从托盘菜单可直接截图。 [交互验收](https://github.com/peterwangze/snaploom/blob/v1.0.5/docs/release-verification.md#105-功能与交互走查)

| 系统 | 安装包 | 运行条件 |
| --- | --- | --- |
| Windows x64 | `*-setup.exe` 或 `*-portable.zip` | Windows 10 或 Windows 11 |
| macOS Apple Silicon | `*-macos-arm64.dmg` | macOS 13 及以上 |
| macOS Intel | `*-macos-x64.dmg` | macOS 13 及以上 |
| Linux x64 | `*-linux-x64-setup.run` 或 `*-linux-x64.tar.gz` | 图形桌面，glibc 2.35 及以上 |
| Linux ARM64 | `*-linux-arm64-setup.run` 或 `*-linux-arm64.tar.gz` | 图形桌面，glibc 2.39 及以上 |

- Windows 安装包没有 Authenticode 签名，系统可能显示 SmartScreen 提示。便携版解压后启动 `Snaploom.exe`。从终端调用或读取命令输出时使用 `Snaploom-cli.exe`。
- macOS 应用使用临时签名，未通过 Apple 公证。拖入「应用程序」后首次启动时，可按 Apple 文档在「系统设置 → 隐私与安全性」中批准。截图和系统级快捷键分别需要屏幕录制与辅助功能权限。[Apple 安装说明](https://support.apple.com/zh-cn/102445)
- Linux 运行 `sh Snaploom-版本-linux-架构-setup.run` 安装到当前用户。便携版解压后直接启动 `Snaploom/Snaploom`，或运行同目录的 `sh install.sh` 安装。Wayland 下的屏幕和快捷键能力取决于合成器与系统门户。
- 安装前可用 `SHA256SUMS.txt` 核对下载。组件许可证和 Qt 对应源码入口随包提供。
