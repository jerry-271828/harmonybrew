# harmonybrew

Claude Code skill for HarmonyOS PC / OpenHarmony development via Harmonybrew.

## Install

```sh
mkdir -p ~/.claude/skills/harmonybrew
git clone https://github.com/jerry-271828/harmonybrew.git ~/.claude/skills/harmonybrew
```

Then restart Claude Code (`/logout` or new session). The skill auto-loads based on trigger keywords.

## What it covers

- **Harmonybrew packaging** — formula, tap, bottle creation for arm64_ohos
- **Cross-compilation** — OHOS SDK in GitHub Actions CI, `-static-pie -Wl,--code-sign`
- **Code signing** — kernel-level `.codesign` requirements, PIE vs non-PIE, debug
- **Running Claude Code on OHOS** — tmpdir FUSE fix, npm/spawn workarounds, shell alias pattern
- **Binary signing tool** — `binary-sign-tool` post-signing, verification scripts

## Trigger keywords

`harmonybrew`, `arm64_ohos`, `鸿蒙 PC`, `OpenHarmony 交叉编译`, `OHOS bottle`, `代码签名`
