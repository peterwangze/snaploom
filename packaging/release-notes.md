Snaploom 是离线运行的截图、标注和贴图工具。安装包包含应用、运行环境和 OCR 模型。

- 滚动截图尚未结束时，再次截图或启动 Snaploom 会回到正在使用的滚动截图面板。面板新增「取消」，可结束本次滚动并重新框选。
- Windows 安装器会检查当前工作区是否仍有 Snaploom 进程运行。安装前可先完成或取消截图，再从系统托盘退出 Snaploom。
- Windows 发布验证新增对安装后程序的真实桌面截图检查和运行中升级检查。[交互验证](https://github.com/peterwangze/snaploom/blob/v1.0.12/docs/release-verification.md#1012-滚动截图与-windows-升级)

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
