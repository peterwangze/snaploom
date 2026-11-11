# 构建 Snaploom 离线安装包

每个目标平台需要在相应系统和 CPU 架构上运行。Windows 打包还需要 Inno Setup 6。GitHub Actions 发布工作流安装各平台的构建依赖并使用原生运行器。

```sh
python -m pip install -r requirements.txt -r packaging/requirements-build.txt
python packaging/build.py --smoke
```

Windows 打包时可以用 `SNAPLOOM_ISCC` 指定 `ISCC.exe`。Linux 使用常规图形桌面共享库，不需要 Python、pip 或 FUSE 来安装。x64 包以 Ubuntu 22.04 为构建基线，ARM64 包以 Ubuntu 24.04 为基线。

构建产物位于 `release/`，同时生成 `SHA256SUMS.txt`。`--smoke` 会从打包产物启动界面并执行离线 OCR，在 Windows 和 Linux 会执行静默安装和卸载，在 macOS 会挂载 DMG 验证。请在没有现存 Snaploom 安装的 Windows 测试环境执行 `--smoke`。

## 签名

默认 Windows 产物没有 Authenticode 签名。可在本地构建机安装代码签名证书，并设置 `SNAPLOOM_WINDOWS_CERT_SHA1`。如果未自动找到 Windows SDK 的签名工具，可用 `SNAPLOOM_SIGNTOOL` 指定路径。

默认 macOS 应用使用 ad-hoc 签名。可在 macOS 构建机安装 Developer ID Application 证书并设置 `SNAPLOOM_MAC_IDENTITY`。可选的 `SNAPLOOM_NOTARY_PROFILE` 指向已在钥匙串配置的 `notarytool` 凭据，构建脚本会提交 DMG 并装订公证票据。

## 发布

可手动运行 GitHub Actions 的 Offline installers 工作流取得对应提交的安装产物。推送与应用版本一致的 `vX.Y.Z` 标签后，工作流会构建并验证所有安装包，生成汇总校验文件，再创建 GitHub Release。
