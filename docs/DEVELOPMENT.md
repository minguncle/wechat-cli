# 构建与维护

使用 Python 3.12，安装开发依赖后执行 `python -m pytest -q`。测试使用临时目录和合成数据，不需要登录微信，不发送真实消息。

## 完整离线包

安装 uv 后执行：

```sh
python scripts/build_bundle.py --prepare --version 0.2.0a7
```

同一个包包含 Windows x64 与 Mac ARM64 运行组件。产物位于 dist，包含源码、许可证、Web 和 Skill。构建版本应与 novus_wechat/__init__.py 一致。

使用包内 Python 执行 `scripts/bundle_verify.py 包目录` 验证应用。Mac 真实账号验证与普通 CI 分开记录。

## 上游维护

`python scripts/upstream_sync.py` 只检查远端版本。`--apply` 在新分支中通过三方补丁更新并执行测试，冲突时停止，保留工作区供人工解决。审查通过后再合并，不在用户启动时自动更新。

## 原生组件

C 源码与构建说明位于 native/windows。客户端版本与二进制 SHA256 属于适配条件，不能只改校验值来宣称支持新版本。
