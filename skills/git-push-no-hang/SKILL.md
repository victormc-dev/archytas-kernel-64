---
name: git-push-no-hang
description: 解决本机（Windows + WorkBuddy 环境）GitHub 操作的两类疑难：(1) git push / fetch / credential **永久挂死**（不是报错，而是卡住不动）——根因是 credential.helper 列表首位是交互式 helper（WorkBuddy 的 helper-selector），非交互 shell 里永远等不到回答；(2) 无 gh / 无 requests 时用 curl 直连 GitHub API 发 Release，踩到 **schannel 证书吊销检查失败（curl (35) CRYPT_E_NO_REVOCATION_CHECK，HTTP 000）** 与 **Release 资源名被 sanitize（非 ASCII 换成都 . ）**。本技能给出无阻塞取 token（wincred/osxkeychain 优先）、清空 helper 列表 + 一次性 URL 推送、输出脱敏、`--ssl-no-revoke`、"推到 URL 不会更新 origin/* 引用"等踩坑。Trigger words: git push 卡住, git push 挂死, git push 不返回, credential.helper, helper-selector, git credential fill 卡住, 推送超时, nonblocking git push, git push hang, osxkeychain, wincred, git 认证阻塞, curl HTTP 000, CRYPT_E_NO_REVOCATION_CHECK, schannel, ssl-no-revoke, 发 release, GitHub Release, gh 未安装, uploads.github.com, release 资源名被改, asset name sanitized.
agent_created: true
---

# git 推送/认证无阻塞化

## Overview

症状：`git push`（或 `git fetch`、`git credential fill`）**不报错也不返回**，就挂在那里，
CI/脚本里表现为"本该几秒的命令跑了十分钟"。

不要先怀疑网络。本机（WorkBuddy 环境）的根因通常是**认证 helper 是交互式的**：

- `git config credential.helper` 可能解析到 WorkBuddy 的 `helper-selector`；
- 它不返回凭据，而是**弹 UI 等用户点**，非交互 shell 永远等不到；
- 于是 `git push` 无声地永久阻塞。

## 两个必须同时修掉的坑

### 坑 1：交互式 helper 排在列表首位

`git config --get-all credential.helper` 看列表。任何交互式 helper 在首位都会先跑。
修法：用**空值先重置列表**，再挂上确定非交互的 helper：

```bash
git -c credential.helper= -c credential.helper=wincred credential fill   # Windows
git -c credential.helper= -c credential.helper=osxkeychain credential fill   # macOS
```

先试平台凭据库（`wincred` / `osxkeychain`）——它装着**同一份**凭据且秒回；
最后才退到裸 `git credential fill`（照顾用 GCM 的机器）。

### 坑 2：`subprocess` 的 `timeout=` 根本管不住孙进程

```python
subprocess.run(cmd, capture_output=True, timeout=10)   # ← 假的超时
```

`timeout` 只杀**直接子进程**。它派生出的 helper 存活下来、**继续持有继承来的 stdout 管道**，
`communicate()` 就一直等那条管道关闭——于是超时形同虚设。

修法：**把 stdout/stderr 重定向到临时文件**，不留下任何管道，孤儿进程就无法阻塞调用方：

```python
with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as out, \
     tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as err:
    subprocess.run(cmd, input=QUERY, stdout=out, stderr=err, text=True, timeout=15)
```

## 标准做法

用 `scripts/git_push.py`（本技能自带，可直接复制进项目）：

```bash
python scripts/git_push.py                 # 推 HEAD 到 origin 同名分支
python scripts/git_push.py -r agui         # 换 remote
python scripts/git_push.py --branch main --remote origin
python scripts/git_push.py --dry-run       # 只取 token + 打印，不推
```

它做的三件事：

1. 用坑 1 的写法非交互取 token；
2. `git -c credential.helper= push <一次性带 token 的 URL> <branch>:<branch>`；
3. 打印前对输出**脱敏**——否则 git 自己的 `To https://user:token@github.com/...`
   会把密钥打进终端和日志。

**不要**加 `--no-verify`：pre-push 钩子被拒是真失败，必须可见。

## 用 curl 直连 GitHub：schannel 的证书吊销检查（HTTP 000）

没装 `gh`、也没装 `requests` 时，用 Python `urllib` 打 JSON API、用 `curl` 传大文件是
可行路线——但会撞上另外一个**看起来像网络问题**的坑：

```
curl: (35) schannel: next InitializeSecurityContext failed:
CRYPT_E_NO_REVOCATION_CHECK (0x80092012) - 吊销功能无法检查证书是否吊销
```

症状特征：**0.1 秒内失败**，`%{http_code}` 是 `000`，没有任何 HTTP 响应。

原因：Windows 的 curl 走 **schannel**，TLS 握手时会校验证书吊销（CRL/OCSP）；
网络拿不到那些端点时握手直接失败，于是**对 github.com 的每个请求都 000**。
而 Python 的 `urllib` 用 OpenSSL、不强制吊销检查，所以会出现
"Python 能访问 api.github.com、curl 却全部失败"的分裂现象——**别据此怀疑 token 或代理**。

修法：加 `--ssl-no-revoke`（schannel 专属开关）；写进 `curl -K -` 的配置文件里就是一行
`ssl-no-revoke`。**只在 Windows 加**，其他 TLS 后端不认这个选项。

```bash
curl --ssl-no-revoke -o /dev/null -w '%{http_code}\n' https://api.github.com   # 应得 200
```

## 发 Release：GitHub 会静默改写非 ASCII 资源名

- Release 的**标题 / 正文**支持中文，没问题；
- 但**资源名（asset name）会被 sanitize**：非 ASCII 字符替换成 `.`，连续点合并。
  `整合包.zip` 会变成 `.zip` 这种（上传仍返回 201，**完全静默**）。
- 所以发布前就把对外名定成 ASCII；**并且把校验文件里的文件名改成同一个 ASCII 名**，
  否则用户 `sha256sum -c` 会找不到文件。
- 想自定义对外名：上传 URL 带 `?name=<urlencoded>`，**本地文件名可以完全不同**
  （`--asset LOCAL=NAME` 这种写法）。
- 发布后**用 API 核对**，别只看上传是否返回 OK：

```bash
# 资源对象带 digest 字段（sha256:...），可直接与本地 sha256 比对，免下载 600 MB
curl --ssl-no-revoke -sS -H "Authorization: Bearer $TOKEN" \
  https://api.github.com/repos/OWNER/REPO/releases/tags/TAG
```

最小流程（无需 `gh`）：

1. `POST /repos/{o}/{r}/releases`（`tag_name` / `name` / `body` / `draft` / `prerelease`）；
2. `POST https://uploads.github.com/repos/{o}/{r}/releases/{id}/assets?name=...`，
   用 `curl -K -`（**配置从 stdin 读**，token 不进 `argv`）；
3. 已存在的资源要**显式删除**再传，或先比对 `assets[].name` 跳过——重复上传会报 422。

> 两个顺带的小坑：`subprocess.run(..., text=True)` 在 Windows 用 **locale 编码**（GBK）
> 编码 stdin，中文路径会传坏；改成传 `bytes`（UTF-8）并用 `errors="replace"` 解码输出，
> 否则 curl 的一条非 UTF-8 输出就能让读取线程抛 `UnicodeDecodeError`、把真正的错误吞掉。

## 踩坑清单

| 坑 | 说明 |
| --- | --- |
| 推到 **URL** 而非 remote 名时，**不更新** `origin/<branch>` 引用 | 核对远端要用 `git ls-remote origin refs/heads/main`，别信本地 `origin/main` |
| `cmd \| tail; echo $?` 拿到的不是命令的退出码 | 那是 `tail` 的。要退出码就别进管道 |
| token 出现在 `git push` 输出里 | git 会回显 `To https://user:token@...`。务必对每一行脱敏：`user:token@github.com` → `github.com` |
| 私仓/公仓都要鉴权推送 | 推送一律需要 token；`ls-remote` 可匿名（公仓） |
| 交互式 helper 也会卡 `fetch` / `pull` | 同一套修法，必要时加 `-c credential.helper=` |
| `curl` 访问 github.com 全部 `HTTP 000`、0.1 秒内失败 | schannel 查证书吊销失败（`CRYPT_E_NO_REVOCATION_CHECK`）。加 `--ssl-no-revoke`（Windows 专属），**别怀疑 token** |
| Release 资源名里的中文变成 `.` | GitHub 会**静默 sanitize** 资源名。对外名用 ASCII，校验文件里的文件名同步改 |
| 同一资源重复上传报 422 | 先 `GET .../releases/tags/<tag>` 看 `assets[].name`，命中就跳过或先 `DELETE` |

## 验收

```bash
python scripts/git_push.py 2>&1; echo "EXIT=$?"     # 应数秒内返回，EXIT=0
git -c credential.helper= ls-remote origin refs/heads/main   # 远端指向应与本地 HEAD 一致
```
