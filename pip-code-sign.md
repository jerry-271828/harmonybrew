# HarmonyOS pip 原生代码签名修复流程

## 问题

HarmonyOS PC 内核对 `execve` 和 `dlopen` 强制校验 ELF 文件的 `.codesign` 节区（fs-verity 默克尔树）。pip 安装的 `.so` 共享库和可执行二进制均无此签名，导致：

- `Permission denied` (EACCES) — 文件无签名
- `Operation not permitted` (EPERM) — 签名存在但校验失败

## 解决方法

使用 `binary-sign-tool` 对 pip 安装的所有 ELF 文件进行**自签名**（self-sign）：

```bash
binary-sign-tool sign -inFile 原文件.so -outFile 输出文件.so -selfSign 1
```

### ⚠️ 关键注意事项

1. **不能原地签名**：`-inFile X -outFile X` 在 OHOS SDK 26.0.0.18（6.x 版本）上存在 bug，报告成功但实际未写入有效的 `.codesign`。必须输出到**不同的临时文件**。

2. **必须先删除原文件再移动**：带 `security.isolate` 属性的文件无法直接覆盖，必须先 `rm` 再 `mv`。

3. **切勿双重签名**：已签名的文件再次签名会损坏 ELF。

4. **验证签名**：每次签名后必须用 `readelf -S <文件> | grep .codesign` 确认节区存在。

### 推荐脚本 (`sign_venv.py`)

```python
#!/usr/bin/env python3
"""Sign all ELF shared libraries in .venv for HarmonyOS code signing enforcement."""
import subprocess, os
from pathlib import Path

def sign_file(f: Path) -> bool:
    tmp_signed = Path.home() / f"sig_{f.name}"
    # 1. 签名到临时文件
    r = subprocess.run(
        ["binary-sign-tool", "sign", "-inFile", str(f),
         "-outFile", str(tmp_signed), "-selfSign", "1"],
        capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return False
    # 2. 验证 .codesign 节区存在
    r = subprocess.run(["readelf", "-S", str(tmp_signed)],
                       capture_output=True, text=True, timeout=10)
    if ".codesign" not in r.stdout:
        tmp_signed.unlink(missing_ok=True)
        return False
    # 3. 删除原文件（security.isolate 可能阻止 unlink）
    try:
        f.unlink()
    except (PermissionError, OSError):
        try:
            os.chmod(str(f), 0o666)
            f.unlink()
        except (PermissionError, OSError):
            tmp_signed.unlink(missing_ok=True)
            return False
    # 4. 移动签名文件到原位
    try:
        tmp_signed.rename(f)
        return True
    except OSError:
        tmp_signed.unlink(missing_ok=True)
        return False

def main():
    venv = Path(".venv")
    elf_files = []
    for root, dirs, files in os.walk(str(venv)):
        for name in files:
            fpath = Path(root) / name
            out = subprocess.run(["file", str(fpath)], capture_output=True,
                                 text=True, timeout=5)
            if "ELF" in out.stdout:
                elf_files.append(fpath)
    print(f"Found {len(elf_files)} ELF files")
    ok = sum(1 for f in elf_files if sign_file(f))
    print(f"Signed: {ok} / {len(elf_files)}")

if __name__ == "__main__":
    main()
```

## ⚠️ 64KB 节区对齐绝不能碰 TLS 节区（2026-07 实战确认）

`ohos-pip-autosign`（1.0.0）和照抄它的签名脚本在 `binary-sign-tool` 之前有一个"归一化"步骤：`llvm-strip --strip-all` + 用 `llvm-objcopy --set-section-alignment <sec>=65536` 把所有 Allocatable（`A`）节区对齐到 64KB（绕开 binary-sign-tool 对某些 PHT 布局的处理 bug）。

**这个全量 `A` 节区遍历会把 `.tdata`/`.tbss`（TLS 节区，flag 含 `T`）一起改掉，必须排除**：

- local-exec 模型下，`thread_local` 变量的 TP 相对偏移在**编译期**按原始 `PT_TLS p_align`（常见 0x20）算好写死在代码段里；
- objcopy 把对齐改成 0x10000 后，musl 运行时按新 p_align 摆放 TLS 块，代码里烧死的旧偏移全部错位；
- 结果是**签名校验通过、程序正常加载**，但所有 `thread_local` 读到垃圾——典型表现为启动后数秒内 SIGSEGV，fault addr 是 0x17 这类小偏移（对垃圾指针解引用成员）。chromium 上的实际崩溃点是 `ThreadIdNameManager::GetName`（用 Alpine `chromium-dbg` 包符号化定位）。

**症状鉴别**：这类崩溃和"签名坏了"完全不同——不是 EACCES/EPERM，也不在 exec/dlopen 时失败，而是运行一段时间后 SIGSEGV。用线程多、`thread_local` 用得多的程序（chromium、rust 程序）最容易踩中；简单 CLI 工具可能永远不触发。

**正确做法**：解析 `llvm-readelf -S` 时跳过 flag 含 `T` 的节区，binary-sign-tool 对未对齐的 TLS 节区照常接受：

```python
flags = line[line.find(name) + len(name):]
if name.startswith(".") and "A" in flags and "T" not in flags:
    sections_to_align.append(name)
```

修好的完整参考实现：[playwright-mcp-ohos 的 `scripts/ohos_sign_sweep.py`](https://github.com/jerry-271828/playwright-mcp-ohos/blob/main/scripts/ohos_sign_sweep.py)。

排查命令：`llvm-readelf -l <bin> | grep -A1 TLS` 看 PT_TLS 的 Align 列——如果签名后变成 0x10000 而重新链接的原始产物是 0x20/0x40，就是踩了这个坑。

## 已知限制：SDK 6.x 的默克尔树 bug

OHOS SDK 26.0.0.18（对应 OpenHarmony 6.x Release）的 `binary-sign-tool` 存在已知 bug：

> 对签名数据 >512KB（默克尔树 ≥2 层）的二进制算错根哈希
> （`ArrayRef::slice(start, N)` 的 N 是长度，初版代码误传了结束偏移）
> 2026-03-04 commit 03e5f739 修复，**7.0-Beta1 起可用**

### 症状

| 场景 | 结果 |
|------|------|
| 无签名 | `Permission denied` (EACCES) |
| 小文件 (<512KB) 签名 | ✅ 正常加载 |
| 大文件 (>512KB) 签名 | ❌ SIGSEGV (exit 139) — 校验通过但代码段被损坏 |

### 影响

numpy（`_multiarray_umath.so` 6MB）、scipy（`libopenblas` 15MB+）、pyarrow（`libarrow.so` 55MB）等科学计算库的核心 `.so` 文件均远超 512KB，在 SDK 6.x 下签名后会崩溃。

### 解决方案

1. **更新 OHOS SDK 到 7.0-Beta1+**：`brew upgrade ohos-sdk`（需等待 Harmonybrew 提供新版 formula）
2. **或使用 x86-64 Linux/Mac/Windows** 设备运行 ML 流水线

## 路径依赖的行为

代码签名检查在内核层面对不同路径有不同策略：

| 路径 | 行为 |
|------|------|
| `~/.harmonybrew/lib/` | 较宽松（`.so` 文件即使无签名也可加载） |
| `~/.harmonybrew/opt/` | 严格（需有效签名） |
| 项目目录 (`.venv/`) | 严格（需有效签名） |

创建 venv 时优先选择 `.harmonybrew/lib/` 下以获得最宽泛的兼容性。

## 诊断命令速查

```bash
# 查看文件是否有 .codesign 节区
readelf -S file.so | grep .codesign

# 查看内核代码签名日志
hilog -x | grep -iE "xpm|code.?sign|ldso"

# 用 ctypes 测试单个 .so 加载
python3 -c "import ctypes; ctypes.CDLL('file.so')"

# 查看 ELF 文件的扩展属性
getfattr -d file.so
```

## 与其他 harmonybrew skill 的关联

- 主 skill 中的"代码签名"章节描述了编译时的正确做法：`-Wl,--code-sign` 在链接期签名
- `binary-sign-tool sign -selfSign 1` 是编译后补签的开发调试方案
- `devel-base` 的 ld.lld 封装默认添加 `--code-sign`，编译产物自动有效
