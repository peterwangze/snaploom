Snaploom 是离线运行的截图、标注和贴图工具。安装包包含应用、运行环境和 OCR 模型。

- 「保存并复制」或 **Enter** 将截图存到默认目录，再复制这个已保存的文件。Windows 同时提供文件路径和图像，兼顾 iCode 等终端工具及图像应用。滚动截图也使用这一流程。
- 「另存为…」可选其他目录。默认目录写入失败时会保留选区和标注，可以直接改用「另存为…」。[验收记录](https://github.com/peterwangze/snaploom/blob/v1.0.18/docs/release-verification.md#1018-保存并复制)
- 复制图片、保存和复制识别结果默认只更新工作台状态栏。「设置 → 输出 → 显示操作成功通知」可开启桌面通知。失败和需要操作的提示照常显示。
- 工作台和托盘可直接打开最近保存目录。「已保存文件」保留最近 50 个不同的路径，支持打开所在文件夹、复制路径和清空记录。

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
