# Snaploom 日常使用体验审计

核对日期为 2026 年 11 月 17 日。以 Snaploom 1.0.23 为基线，沿主窗口、托盘和快捷键检查截图、标注、保存、滚动截图及贴图管理。对照 Snipaste、ShareX、系统截图工具的操作文档，并用原始问题记录查找容易出错的场景。问题记录反映报告中列出的版本和环境。各平台的实测覆盖另有记录。[交付验收](release-verification.md)

## 本轮调整

- **区域截图先明确要截哪里。** 用户可拖选、确认可见的窗口建议，或按 Ctrl／⌘ + A 选择当前屏幕。区域编辑器没有可见选区时，导出操作提示先选择区域，避免 Enter 或输出快捷键意外保存整屏。工作台仍有明确的屏幕截图入口。macOS 和 Windows 截图工具都将区域、窗口和整屏作为独立模式。[macOS 截图操作](https://support.apple.com/en-us/102646) · [Windows 截图模式](https://support.microsoft.com/en-us/windows/apps/use-snipping-tool-to-capture-screenshots)
- **区域放大保留此前的遮盖效果。** 临时取景放大镜用于观察选区。「区域放大」标注会写进结果，并从当前已标注画面取样，先画的实色遮盖、模糊和马赛克会随图放大。ShareX 将局部放大和隐藏敏感内容列为编辑任务，Windows 截图工具提供本机快速涂黑。[ShareX 编辑器](https://getsharex.com/docs/image-editor) · [Windows 截图工具](https://support.microsoft.com/en-us/windows/apps/use-snipping-tool-to-capture-screenshots)
- **滚动截图可以先检查，再撤回一帧。** 滚动面板显示帧数和尺寸。点「预览拼接结果…」可以检查长图，添加有误时点「撤回上一帧」并继续取图。保存和复制沿用普通截图的按钮含义。ShareX 有重复和漏行的报告，另有用户发现常规的复制设置没有自动应用到滚动截图。[重复与漏行反馈](https://github.com/ShareX/ShareX/issues/6944) · [滚动截图复制说明](https://github.com/ShareX/ShareX/issues/9241)
- **贴图操作可恢复，也可处理多张。** 从贴图列表或托盘选择「退出独显」，即可恢复此前的可见状态。多选贴图后，点「移动…」可一次移到指定分组。Snipaste 将独显、多选和分组管理设为独立操作。[Snipaste 贴图操作](https://github.com/Snipaste/feedback/wiki/PRO#paste)
- **打开文件前说明会生成贴图。** 主窗口和托盘使用「从图片文件新建贴图…」，用户在选文件前就能知道会打开悬浮窗口。ShareX 的打开图像入口进入编辑器，Snipaste 的贴图会把内容显示为置顶窗口。[ShareX 打开图像的流程](https://getsharex.com/docs/image-editor#opening-the-image-editor) · [Snipaste 贴图说明](https://github.com/Snipaste/feedback/wiki/Getting-Started#paste)
- **刚保存的图片可直接打开。** 在「已保存文件」双击图片或按 Enter，会用系统默认应用打开所选图片。列表也提供「打开所在文件夹」和「复制文件路径」。如果图片已移动，状态栏会显示无法打开的完整路径。系统截图工具同样把查找和打开截图作为捕获后流程的一部分。[Windows 保存位置](https://support.microsoft.com/en-us/windows/apps/use-snipping-tool-to-capture-screenshots) · [macOS 查找截图](https://support.apple.com/en-us/102646)

## 待进一步验证的使用场景

- **重新连接显示器后继续准确框选。** Snaploom 已记录单屏 150% 和合成混合缩放帧的结果。真实双屏仍需检查跨屏框选、交换主屏或拔插后重复上次区域，逐项比对物理尺寸和像素。Snipaste 有 Windows 双屏放大的报告，Flameshot 有 100%/125% 双屏选区偏移的报告。[Snipaste #3794](https://github.com/Snipaste/feedback/issues/3794) · [Flameshot #4868](https://github.com/flameshot-org/flameshot/issues/4868) · [Snaploom 验收边界](release-verification.md)
- **系统授权失败后恢复截图。** Snaploom 在 macOS 已检查屏幕录制权限，在 Wayland 已用系统截图门户。下一步应在真实登录会话记录首次授权、拒绝、撤回和重试。Flameshot 已修复某些请求缺少父窗口标识时的失败。门户规范允许无合适窗口句柄时使用空字符串，Snaploom 的现有请求仍需在 GNOME 和 KDE 核对。剪贴板也应通过另一个应用验证，包括复制后延迟粘贴。[Apple 权限说明](https://support.apple.com/en-euro/guide/mac-help/mchld6aa7d23/27/mac/27) · [Flameshot 门户修复](https://github.com/flameshot-org/flameshot/pull/4664) · [XDG 窗口标识规范](https://flatpak.github.io/xdg-desktop-portal/docs/window-identifiers.html) · [Flameshot 外部粘贴反馈](https://github.com/flameshot-org/flameshot/issues/4116)
- **快捷键与贴图焦点在前台应用中仍可用。** Snaploom 已提示注册冲突并有工作台和托盘入口。可按前台应用、桌面会话测试注册成功后的实际触发，并检查创建、独显和恢复贴图后能否立即在原窗口输入。Snipaste 有快捷键只在部分应用失效的报告，也有 XFCE/X11 贴图焦点切换失败的报告。[Snipaste 快捷键反馈](https://github.com/Snipaste/feedback/issues/3643) · [XFCE 贴图焦点反馈](https://github.com/Snipaste/feedback/issues/3778)

截图文件、历史副本和云识别各有独立的留存或发送条件。Snaploom 提供历史条数设置和本地 OCR，常规「保存并复制」会写入文件。说明保存和清理功能时应把影响范围写清楚，便于用户在截到敏感内容前决定如何处理。[Snipaste 零历史诉求](https://github.com/Snipaste/feedback/issues/3774) · [ShareX 误上传反馈](https://github.com/ShareX/ShareX/issues/8363)
