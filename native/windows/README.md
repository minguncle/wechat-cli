# Windows 文字回调组件

`text_callback.c` 是本项目编写的 x64 原生回调适配组件，不包含微信客户端代码。

当前发布候选二进制：`platform/windows/novus-text-callback-v1.dll`。
SHA-256：`3144981fe31231ad743aaf4049d8e14c91b89f0321d80b6dd4514389ee09cd97`。
PE 导入仅有系统的 `KERNEL32.dll` 和 `msvcrt.dll`，无需另装编译器运行库。

构建命令（在仓库根目录，用 MinGW x64 GCC）：

```powershell
gcc -std=c11 -shared -O2 -Wall -Wextra -Werror -static-libgcc native/windows/text_callback.c -o .build/novus-text-callback.dll
```

PE 时间戳及编译器差异可能改变二进制哈希。重新构建后需重新验证产物，不能只修改运行时哈希常量绕过校验。

验证：`python -m pytest tests/test_native_text_callback.py tests/test_native_text.py -q`。
前者需要 Windows 和 GCC，在独立子进程测试回调生命周期；后者替换原生调用，验证参数和调用控制，不会发送消息。

组件一旦用于回调，会固定加载直到宿主进程退出。修改组件后不要热覆盖已加载文件，应使用新版本文件名；旧回调必须保持其原有代码与存储有效。
