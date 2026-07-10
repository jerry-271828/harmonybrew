---
name: harmonybrew
description: Use when packaging, cross-compiling, signing, or debugging software for HarmonyOS PC / OpenHarmony via Harmonybrew (Homebrew for HarmonyOS) — writing arm64_ohos formulas/taps/bottles, OHOS SDK cross-compilation in CI, lld --code-sign signatures, "permission denied"/"operation not permitted" exec failures, binary-sign-tool, or installing Harmonybrew on HarmonyOS devices. Triggers - harmonybrew, arm64_ohos, 鸿蒙 PC, OpenHarmony 交叉编译, OHOS bottle, 代码签名.
---

# Harmonybrew 打包与 OHOS 交叉编译

Harmonybrew 是 HarmonyOS PC（arm64_ohos）上的 Homebrew 移植版。本 skill 来自一次完整的 iperf3 适配实战（fork → CI 交叉编译 → bottle → tap → 真机调通），参考实现：
- workflow：https://github.com/jerry-271828/iperf/blob/master/.github/workflows/build-ohos.yml
- tap：https://github.com/jerry-271828/homebrew-harmony

## 1. 关键地址与路径

| 项 | 值 |
| --- | --- |
| 官方组织 | https://atomgit.com/Harmonybrew （brew、homebrew-core、install、docs 等仓库） |
| 安装脚本 | `zsh -c "$(curl -fsSL https://harmonybrew.atomgit.com/install.sh)"` |
| brew 前缀 | `/storage/Users/currentUser/.harmonybrew`（brew 本体在其下 `Homebrew/`） |
| bottle 域名 | `https://harmonybrew.atomgit.com/bottles` |
| API 域名 | `https://harmonybrew.atomgit.com/api` |
| OHOS SDK 正式版 | `https://repo.huaweicloud.com/openharmony/os/<release>/ohos-sdk-windows_linux-public.tar.gz` |

安装前提（鸿蒙 PC）：卸载 GitNext/DevBox；开"开发者选项"（设置→关于本机→连点"软件版本"7 次）；开"运行来自非应用市场的扩展程序"（设置→隐私和安全→高级）。装完 `eval "$(/storage/Users/currentUser/.harmonybrew/bin/brew shellenv)"` 写入 `~/.zshrc`。换自有 brew/core 源：安装前 `export HOMEBREW_BREW_GIT_REMOTE=... HOMEBREW_CORE_GIT_REMOTE=...`。

## 2. 代码签名 —— 最重要的一章

鸿蒙 PC 内核对 execve 强制校验代码签名（fs-verity 默克尔树，嵌在 ELF 的 `.codesign` 节区）。**错误对照表**：

| 现象 | 含义 |
| --- | --- |
| `zsh: permission denied`（EACCES） | 二进制**没有签名** |
| `zsh: operation not permitted`（EPERM） | 有签名但**校验失败**（根哈希错 / 非 PIE） |

可执行的两个必要条件：
1. **有效的 `.codesign` 签名**：链接时加 `-Wl,--code-sign`（lld 选项，来自 third_party_llvm-project PR 882，2025-11-27 合入 master）。
2. **必须是 PIE（ET_DYN）**：非 PIE（ET_EXEC）即使签名有效也会 EPERM。静态二进制用 `-static-pie`，**不要**用 `-static` 或 iperf 式的 `--enable-static-bin`。

**SDK 版本雷区**：
- 6.0-Release 及更早：lld 没有 `--code-sign`。
- **6.1-Release：有 `--code-sign` 但有 bug**——对签名数据 >512KB（默克尔树 ≥2 层）的二进制算错根哈希（`ArrayRef::slice(start, N)` 的 N 是长度，初版代码误传了结束偏移；2026-03-04 commit 03e5f739 修复）。小文件恰好不触发，所以 bug 随 6.1 发布而未被发现。
- **7.0-Beta1 起可用**。Harmonybrew 官方用 Master_Version 每日构建 SDK。

**铁律**：
- 签名在链接期生成，**链接后任何修改 ELF 的操作（strip、objcopy）都会使签名失效**。要 strip 就在链接参数里做，或干脆不 strip。
- 产物必须用捆绑的 `scripts/verify-codesign.py` 复算根哈希（`MATCH: True` 才算签好），并作为 CI 门禁——lld 的 bug 不会报错，只会产出坏签名。
- 事后补签（真机上）：`binary-sign-tool sign -inFile X -outFile X -selfSign 1`（binary-sign-tool 由 `ohos-sdk` 包提供，装 `devel-base` 也会带上）。真机上用 devel-base 工具链编译则自动签名（它的 ld.lld 封装默认加 `--code-sign`）。
- **补签前的 64KB 节区对齐绝不能碰 TLS 节区**（`.tdata`/`.tbss`，flag 含 `T`）。local-exec TLS 的 TP 相对偏移在编译期按原始 PT_TLS p_align（常见 0x20）写死在代码里；objcopy 把它对齐到 0x10000 会改变 musl 运行时 TLS 布局，所有 `thread_local` 读到垃圾 → 启动数秒后 SIGSEGV（chromium 上表现为 `ThreadIdNameManager::GetName` null deref，fault addr 0x17 这类小偏移）。binary-sign-tool 对未对齐的 TLS 节区照常接受，跳过即可。详见 [pip-code-sign.md](pip-code-sign.md)；修好的参考实现：[playwright-mcp-ohos 的 ohos_sign_sweep.py](https://github.com/jerry-271828/playwright-mcp-ohos/blob/main/scripts/ohos_sign_sweep.py)（harmonybrew 官方 ohos-pip-autosign 1.0.0 的 hook 仍有此 bug——它对齐所有 `A` 节区不排除 `T`）。
- 真机排查：失败后立刻 `hilog -x | grep -iE "xpm|code.?sign|verity"`。

## 3. CI 交叉编译配方（GitHub Actions）

```sh
export CC="$OHOS_NATIVE/llvm/bin/clang"
export CFLAGS="--target=aarch64-linux-ohos --sysroot=$OHOS_SYSROOT -O2 -fPIC -D__MUSL__ -D__OHOS__"
export LDFLAGS="--target=aarch64-linux-ohos --sysroot=$OHOS_SYSROOT -static-pie -Wl,--code-sign"
export AR="$OHOS_NATIVE/llvm/bin/llvm-ar" RANLIB="$OHOS_NATIVE/llvm/bin/llvm-ranlib"
./configure --host=aarch64-linux-musl --disable-shared ...
```

要点与坑：
- SDK zip 结构：完整包里抽 `ohos-sdk/linux/native-linux-x64-*.zip`（clang/lld + musl sysroot，约 840MB）。**GitHub 美国机房拉华为云 CDN 可能 1 小时以上**——把这个 zip 传到自己仓库的 GitHub release（如 tag `ci-toolchain-<release>`）做镜像，CI 从镜像拉只要几十秒；sha256 锁定。注意 workflow 的 tag 触发规则要排除 `!ci-toolchain-*`。
- `--host` 用 config.sub 认识的 `aarch64-linux-musl` 即可（只为开启交叉模式），真正的目标由 `--target=aarch64-linux-ohos` 决定。
- **libtool 链共享库时会丢掉 `--target`**，退回宿主 `/usr/bin/ld`（不认识 `--code-sign` 而炸）→ 加 `--disable-shared`。
- **`set -o pipefail` 下不要 `cmd | grep -q`**：grep -q 匹配即退出，上游进程 SIGPIPE 导致管道整体失败（"write on a pipe with no reader"）。用 `if ! cmd | grep pattern >/dev/null` 。
- 静态(-pie) 产物可直接用 `qemu-aarch64-static` 冒烟测试；动态产物不行（OHOS musl loader 与 qemu-user 不兼容）。
- 构建后断言四连：`aarch64`、无 `INTERP`、ELF 类型 `DYN`、`.codesign` 存在 + verify-codesign.py MATCH。
- OHOS musl 实现了 `pthread_cancel`（"OHOS musl 没有 pthread_cancel"是讹传，勿按 Android 方式打 pthread_kill 补丁）。SCTP 不可用（`--without-sctp`）。

## 4. Formula / tap / bottle

- tap 结构：仓库名 `homebrew-<tap名>`，formula 放 `Formula/*.rb`。用户侧：`brew tap <user>/<tap名> <git url>` + `brew install`。
- formula 要点：`compatibility_version 1`（Harmonybrew 特有）；bottle 用 `cellar: :any_skip_relocation`（静态二进制免重定位）；`root_url` 可指向 GitHub release。
- **bottle 命名天坑**：`brew bottle` 本地产物是双横线 `name--version.arm64_ohos.bottle.tar.gz`，但 brew 从自建 `root_url` 下载按**单横线** `name-version.arm64_ohos.bottle.tar.gz` 拼 URL。release 资产必须提供单横线命名（可两种都传），否则 install 404。
- bottle 打包：目录结构 `<name>/<version>/{bin,lib,...}`；删掉 `.la` 文件（硬编码构建期 prefix）；tar 用 `--sort=name --mtime='1970-01-01 00:00:00 UTC' --owner=0 --group=0 --numeric-owner` 保证可复现。
- 源码 sha256 用 GitHub 的 `archive/refs/tags/<tag>.tar.gz`，发版后实际下载算一遍填进 formula。

## 5. 验证流程（发版前必做）

1. CI 门禁：`python3 scripts/verify-codesign.py <binary>` 输出 `MATCH: True`。
2. 本地（任意 arm64 Linux 均可直接运行 static-pie 产物）：`--version`、回环打流 `iperf3 -s -1 &; iperf3 -c 127.0.0.1`。
3. `readelf -h`（Type: DYN）、`readelf -S | grep codesign`、`readelf -l | grep INTERP`（应无）。
4. release 资产的单横线 URL `curl -sI` 确认 200，sha256 与 formula 一致。
5. 真机 `brew install/upgrade` 后 `iperf3 -s` 类实际执行。

捆绑脚本：`scripts/verify-codesign.py`（独立重实现 lld CodeSign.cpp 的 fs-verity 默克尔根哈希计算，解析 `.codesign` 节区并比对）。

## 6. 在鸿蒙 PC 上运行 Claude Code

**结论**：可以跑，但需要绕过三个平台特有的坑。完整实现见 [jerry-271828/claude-code](https://github.com/jerry-271828/claude-code)。

### 6.1. 临时目录 uid 检查

`/storage/Users/currentUser/` 是 sharefs/FUSE 挂载，`stat` 返回的属主 uid 被存储服务统一映射（如 20001006），不等于应用进程 uid（如 20020228）。Claude Code 启动时会检查临时目录属主，失败则退出。

```
Temp directory /storage/.../claude-tmp/claude-20020228 is owned by uid 20001006, expected 20020228
```

**修法**：`export CLAUDE_CODE_TMPDIR=/data/storage/el2/base/cache/claude-tmp`（应用沙箱内真实 ext4）。验证路径可用的标准：`mkdir` 后 `ls -ldn` 属主等于 `id -u`。`/storage/` 下的路径一律不行。

### 6.2. npm 无法 spawn sh（node-gyp / node-pty 编译）

npm 执行 package scripts 时 spawn `/usr/bin/sh` 报 `ENOENT`——文件存在但内核限制（可能是不在允许的 exec 路径白名单里）。`/usr/bin/sh -c 'echo hello'` 直接在终端跑是正常的，仅 npm 子进程里不行。

后果：
- `npm install` 不加 `--ignore-scripts` 会失败
- `node-pty` 的 native addon 无法通过 npm postinstall 编译

**修法**：
```sh
npm install --ignore-scripts   # 先装 JS 依赖
cd node_modules/node-pty
npm exec node-gyp rebuild       # 直接调 npm 捆绑的 node-gyp，不走 sh
```

注意必须用 `npm exec node-gyp rebuild`，不能直接用 `node-gyp rebuild`——OHOS 上 npm 全局 bin 的 PATH 可能不包含 node-gyp。

### 6.3. Shebang 脚本无法执行

鸿蒙内核对 **所有** `execve` 的文件检查 `.codesign` 签名——包括 shebang 文本脚本。`#!/usr/bin/env node` 的 `.mjs`/`.js` 文件是纯文本没有签名 → `zsh: permission denied`（EACCES）。

**修法**：用 shell alias 代替可执行脚本。
```sh
alias claude='node /path/to/claude-code-ohos/bin/claude.mjs'
```
`node` 本身由 harmonybrew 安装并已签名，`.mjs` 文件只是参数不被 exec，不受签名检查。

### 6.4. JS 入口 vs 原生二进制的版本分水岭

上游 `@anthropic-ai/claude-code` npm 包：
- **≤ 2.1.112**：附带完整 `cli.js`（纯 JS，node ≥ 18）
- **≥ 2.1.113**：只剩 132KB 原生壳 + 按平台分发的 bun 编译二进制

那些原生二进制是 **非 PIE（ET_EXEC）**，鸿蒙内核一律 EPERM，签名也救不了。鸿蒙只能走 node + cli.js 路线。

第三方包 [@cometix/claude-code](https://www.npmjs.com/package/@cometix/claude-code) 从 bun 二进制中提取了 cli.js，恢复为纯 Node.js 运行，当前跟版 2.1.201。它的 `linux-arm64-musl` 平台包可以在 OHOS 上使用。

### 6.5. 其他环境变量

```sh
export USE_BUILTIN_RIPGREP=0    # 捆绑的 rg 是 PIE 但未签名，用 harmonybrew 装系统版
export DISABLE_AUTOUPDATER=1     # 新官方版只有原生二进制，自动更新会装坏
```

### 6.6. process.platform

OHOS 的 Node.js 报告 `process.platform === 'openharmony'`（不是 `'linux'`）。写跨平台代码时注意：`os`/`cpu` 字段的 npm 包需显式添加 `"openharmony"`，cometix 等包的 platform detection 也可能不识别此值。
