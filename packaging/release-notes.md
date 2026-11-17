## 更新内容

- 区域截图需要有明确的选区。按确认键前可框选或选择高亮窗口，也可用 Ctrl/⌘ + A 明确选择屏幕。后画的区域放大会保留先前的实色遮盖。
- 纵向选区上下空间不足时，工具栏会优先放到有空间的侧边。修复了浮动工具栏获得焦点时复制快捷键可能失效的问题。
- 滚动截图可预览拼接结果、撤回上一帧并继续添加。高分屏的拼接按原像素保留前一帧。
- 在「已保存文件」双击或按 Enter 直接打开图片。状态栏会显示无法访问的文件路径。
- 贴图「独显」可随时退出并恢复之前的显示状态。一次选择多张贴图后，可以移到同一组。

## 安装与兼容性

离线安装包包含应用、运行环境和 OCR 模型。

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
