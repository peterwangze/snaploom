# Snaploom

**框选屏幕上的内容，就地标注，再复制或贴到桌面。**

用 Snaploom 把问题截图发给同事、把示例悬浮在正在操作的窗口旁，或从屏幕上提取可编辑的文字。支持 Windows、macOS 和 Linux，安装包自带运行环境和中英文 OCR 模型。

## 下载 v1.0.9 离线包

### Windows

- **x64（Windows 10 / 11）** · [安装版（EXE）](https://github.com/peterwangze/snaploom/releases/download/v1.0.9/Snaploom-1.0.9-windows-x64-setup.exe) · [免安装版（ZIP）](https://github.com/peterwangze/snaploom/releases/download/v1.0.9/Snaploom-1.0.9-windows-x64-portable.zip)

### macOS 13 及以上

- **Apple Silicon（ARM64）** · [安装版（DMG）](https://github.com/peterwangze/snaploom/releases/download/v1.0.9/Snaploom-1.0.9-macos-arm64.dmg)
- **Intel（x64）** · [安装版（DMG）](https://github.com/peterwangze/snaploom/releases/download/v1.0.9/Snaploom-1.0.9-macos-x64.dmg)

两种架构均通过 DMG 分发。打开后可将 Snaploom 拖入「应用程序」或所需位置。

### Linux

- **x64（glibc 2.35+）** · [安装版（RUN）](https://github.com/peterwangze/snaploom/releases/download/v1.0.9/Snaploom-1.0.9-linux-x64-setup.run) · [免安装版（TAR.GZ）](https://github.com/peterwangze/snaploom/releases/download/v1.0.9/Snaploom-1.0.9-linux-x64.tar.gz)
- **ARM64（glibc 2.39+）** · [安装版（RUN）](https://github.com/peterwangze/snaploom/releases/download/v1.0.9/Snaploom-1.0.9-linux-arm64-setup.run) · [免安装版（TAR.GZ）](https://github.com/peterwangze/snaploom/releases/download/v1.0.9/Snaploom-1.0.9-linux-arm64.tar.gz)

[安装说明](#离线安装) · [SHA-256 校验文件](https://github.com/peterwangze/snaploom/releases/download/v1.0.9/SHA256SUMS.txt) · [历史版本](https://github.com/peterwangze/snaploom/releases)

![Snaploom 的截图编辑器，选区中有编号和箭头，工具栏提供复制、贴图和保存。](docs/images/capture-editor.png)

## 用 Snaploom 完成日常工作

### 发一张一眼能看懂的截图

按 **F1** 拖出选区。预览保留原画面亮度，按住 **Alt** 可临时查看放大镜。画布使用十字光标，工具栏切换为箭头。用箭头标注指出问题、用编号排好操作顺序，再用实色遮盖挡住敏感内容。选中标注工具后，可以调整线宽、颜色和样式。点线宽框的上下箭头可逐级调整。也可以直接输入数值，按 **Enter** 确认，按 **Esc** 放弃本次输入。

「撤销一步」和「重做一步」分别处理最近一次标注操作，也可用 **Ctrl/⌘ + Z** 和 **Ctrl/⌘ + Shift + Z**。右键菜单里的「清空全部标注」一次清空，随后可以一步撤回。键盘框选时，**Enter** 先确认选区，再按一次复制。**Esc** 先取消正在进行的框选或输入。

### 按用途复制或保存

- **直接贴进聊天或文档。** 点「复制」，剪贴板里是图像。
- **作为文件发送。** 点「更多 → 另存为并复制文件…」，选好文件名、目录和格式后，就能将保存好的文件粘贴到支持文件粘贴的应用。
- **留在本机。** 点「另存为…」选择位置，或点「更多 → 快速保存」写入预设目录。工作台会显示完整路径，工作台和托盘都有「打开保存位置」。

### 把参考图留在手边

贴图是浮在其他窗口上方的小窗。比如把操作步骤截下来，贴到正在使用的窗口旁边，就能边看边操作。可以拖动贴图、用滚轮缩放，或按住 **Ctrl／⌘** 滚动调整不透明度。

- **新建贴图。** 截图后点「贴图」，或复制图片、文字或色值，再点「悬浮剪贴板内容」或按 **F3**。
- **收起一张，再显示。** 点贴图上的 **×** 或按 **Esc** 收起。主窗口和托盘都可以「恢复最近隐藏的贴图」，也可以在「贴图」列表按缩略图选中一张并按 **Enter** 显示。永久删除会要求确认。
- **暂时收起整组。** 按 **Shift + F3** 切换当前分组贴图的显示。更换显示器后，可以用托盘的「找回屏幕外贴图」把移出屏幕的贴图拉回来。

### 从图片里取文字，把长页面拼成一张

框选内容后点「更多 → 提取文字」，即可在本机识别中英文并复制结果。同一菜单也能识别条形码或二维码。关闭结果窗口后还可以继续标注或保存原截图。想截下超出一屏的页面时，选「滚动截图」，回到原窗口向下滚动一小段，再点「添加下一帧」。Snaploom 会匹配画面重叠部分，拼好后可以复制、保存或贴图。

## 适合反复使用的细节

- **重复截图省去重新画框。** 按 **Shift + F1** 重用上次选区，适合记录同一个界面的前后变化。也可以预设尺寸和延时，截下悬停菜单。Windows 高分屏上的单屏选区按物理像素导出。
- **快捷键和按钮都能开始。** 单击托盘图标打开工作台，托盘菜单可以直接截图或贴图。主窗口还提供当前窗口、当前屏幕、所有屏幕和白板。设置中可以分别修改全局快捷键和编辑器快捷键，保存前会提示冲突。
- **常用标注样式可以记住。** 在「设置 → 标注」可调整默认线宽、按工具记忆线宽和样式，或清除已记住的样式。
- **自己决定留下什么。** 自动保存和下载剪贴板网址图片默认关闭。截图历史可调整条数，设为 **0** 即禁用并清理已有记录。贴图单独保存在本机。

## 离线安装

- **Windows** 运行 EXE 安装，或解压 ZIP 后运行 `Snaploom.exe`。安装包未签名。
- **macOS** 打开对应架构的 DMG 后拖出 Snaploom。应用使用临时签名，尚未公证，截图需要屏幕录制权限。
- **Linux** 在普通用户的终端执行 `sh Snaploom-1.0.9-linux-架构-setup.run` 安装，或解压 TAR.GZ 后运行 `Snaploom/Snaploom`。需要图形桌面及系统图形库。Wayland 上的截图和全局快捷键取决于系统门户。

[首次启动说明](https://github.com/peterwangze/snaploom/releases/tag/v1.0.9) · [平台兼容性与验收记录](docs/release-verification.md)

## 进阶用法

### 命令行自动化

可从脚本截取固定区域、同时保存并复制，或直接把文字贴到桌面。以下命令使用源码入口。安装后可用 Windows 的 `Snaploom-cli.exe`、macOS 应用包内的 `Contents/MacOS/Snaploom` 或 Linux 包内的 `Snaploom`。

```sh
python run_snaploom.py snip --full --block -o ./screen.png
python run_snaploom.py snip --area 20 20 800 600 --hold -o "quick-save;clipboard"
python run_snaploom.py paste --plain "Reference" --pos 160 90
python run_snaploom.py ocr ./screen.png --block
```

<details>
<summary>配置可选的云端 OCR</summary>

在「设置 → 输出」选择云端 OCR 后，主动发起识别会向所选服务发送本次图像。OCR.space 从环境变量 `SNAPLOOM_OCR_SPACE_KEY` 读取凭据。腾讯云读取 `TENCENTCLOUD_SECRET_ID`、`TENCENTCLOUD_SECRET_KEY` 和可选的 `TENCENTCLOUD_REGION`。

</details>

## 开发与验证

```sh
python -m pip install -r requirements.txt
python run_snaploom.py
python run_snaploom.py --smoke-test-ocr
```

交互测试以 `QT_QPA_PLATFORM=offscreen` 运行 `python -m pytest -q`，测试依赖见 `packaging/requirements-build.txt`。原生桌面检查和平台边界见 [交付验收记录](docs/release-verification.md)。 [打包说明](packaging/README.md) · [Snipaste 调研](docs/snipaste-research.md) · [产品设计](docs/product-plan.md)
