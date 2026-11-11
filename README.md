# Snaploom

Snaploom 是面向 Windows、macOS 和 Linux 的桌面截图工具。按 **F1** 选区，按 **Enter** 复制，按 **F3** 把剪贴板内容贴在桌面上。截图、标注、历史和贴图管理可从托盘或主窗口打开。

## 离线安装

登录有仓库权限的 GitHub 账号后，从 [Releases](https://github.com/peterwangze/snaploom/releases) 下载对应架构的包。安装包包含应用运行时和中英文 OCR 模型。

| 系统 | 安装包 | 便携包 |
| --- | --- | --- |
| Windows 10/11 x64 | `*-windows-x64-setup.exe` | `*-windows-x64-portable.zip` |
| macOS 13+ Apple Silicon | `*-macos-arm64.dmg` | DMG 内的应用可拖到所需位置 |
| macOS 13+ Intel | `*-macos-x64.dmg` | DMG 内的应用可拖到所需位置 |
| Linux x64，glibc 2.35+ | `*-linux-x64-setup.run` | `*-linux-x64.tar.gz` |
| Linux ARM64，glibc 2.39+ | `*-linux-arm64-setup.run` | `*-linux-arm64.tar.gz` |

Linux 安装可在普通用户的终端执行 `sh Snaploom-版本-linux-架构-setup.run`。需要系统已有可用的图形桌面和相应的图形库。macOS 需要授予屏幕录制权限，Wayland 由系统截图门户请求权限。每个版本的发布页提供 SHA-256 和签名状态。

## 日常操作

- **截图**。从 F1、托盘或主窗口开始，拖动选择区域。窗口候选可用 Tab 切换，箭头键微调选区。主窗口也提供当前屏幕、全部屏幕、当前窗口、重复选区、延时和白板入口。
- **标注**。截图工具栏提供形状、箭头、画笔、荧光笔、文字、序号、马赛克、模糊、实色遮盖和局部放大。输出支持复制、保存、贴图、复制为文件、打印和自定义命令。
- **贴图**。F3 从剪贴板贴图。图片、文本、富文本、色值和本地图片文件均可作为输入。拖动移动，滚轮缩放，Ctrl/Cmd 加滚轮调整不透明度，双击隐藏。托盘可恢复离屏贴图或解除鼠标穿透。
- **识别与长截图**。截图工具栏的「更多」提供离线 OCR、二维码和条码识别。滚动截图逐帧采集并匹配重叠部分，找不到足够重叠时可缩短滚动距离重试。
- **本地数据**。在设置中把历史条数改成 0 即可禁用历史并清理已有条目。贴图单独保存在本地。自动保存和下载剪贴板网址图像默认关闭。

## 命令行

Windows 用 `Snaploom-cli.exe`，macOS 用应用包的 `Contents/MacOS/Snaploom`，Linux 用包内的 `Snaploom`。从源码执行 `python run_snaploom.py` 也使用相同的参数。

```sh
snaploom --help
snaploom snip --full --block -o ./screen.png
snaploom snip --area 20 20 800 600 --hold -o "quick-save;clipboard"
snaploom paste --plain "Reference" --pos 160 90
snaploom ocr ./screen.png --block
```

## 开发与验证

```sh
python -m pip install -r requirements.txt
python run_snaploom.py
python -m unittest discover -s tests -v
python run_snaploom.py --smoke-test-ocr
```

构建入口见 [打包说明](packaging/README.md)。项目的参考范围和平台验收目标见 [Snipaste 调研](docs/snipaste-research.md) 与 [产品设计](docs/product-plan.md)。各平台最终验证范围见 [交付验收记录](docs/release-verification.md)。
