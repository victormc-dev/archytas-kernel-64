# skills/ —— 移植过程中沉淀下来的技能包

这些是本项目（Archytas 64 / 小爱老师 Wi-Fi 版内核与系统移植）实操中打出来的
可复用技能，每个都是一个目录 + `SKILL.md`，可直接拷进
`~/.workbuddy/skills/`（用户级）或 `.workbuddy/skills/`（项目级）被自动加载。

| 技能 | 解决什么 | 关键坑（一句话） |
| --- | --- | --- |
| `gsi-slim-on-device` | **把过大的 GSI/system 镜像精简到能刷进 system 分区** | 本机无 e2fsprogs + WSL 被禁 ⇒ **把已 root 的 Android 设备当 ext4 工具宿主**；按 `ro.vndk.version` 删多余 VNDK 是最大杠杆 |
| `mtk-gpt-repartition` | 在只有 TWRP 的环境里给 MTK 设备改 GPT 重分区 | 目标分区后面压着别的分区时必须**整体平移**；TWRP 的 `/sbin/sh` 是 **32 位算术**，会让上界守卫被乘成负数绕过 |
| `twrp-partition-restore` | 用 adb + dd 直接读写裸分区刷机/回退/救砖 | TWRP 的 toybox `dd` **不认 `bs=4M`**，报错后**静默什么都不写** |
| `ko-codegen-audit` | 内核模块「编译器误编」审计与构建门禁 | `insmod` 报 `unsupported RELA relocation` / 无出口死循环，先**读反汇编**再读源码 |
| `git-push-no-hang` | 本机 `git push`/`fetch` 永久挂死 & 无 gh 时发 Release | `credential.helper` 首位是**交互式** helper；curl 走 schannel 需 `--ssl-no-revoke` |

## 部署

```bash
cp -r skills/* ~/.workbuddy/skills/          # 用户级，所有项目可用
```

## 注

- 技能里的绝对路径、设备序列号等是本机/本次实测的取值，换环境时按现场替换。
- 与刷机现场数据相关的目录（`_diag/`、`_rollback/`、`rescue/` 等）不入库或见仓库根 `.gitignore`。
