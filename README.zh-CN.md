# solo-founder-os

[English](README.md) | **中文**

> 一人公司真正用得起来的 6 层自演化 agent 栈。
> 本地优先,零云基础设施,自主开销 < $0.06/周。

[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/solo-founder-os.svg)](https://pypi.org/project/solo-founder-os/)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](#)

## 它做什么

Solo Founder OS 是支撑[完整 11 agent 一人公司栈](#7-层栈11-个-agent)的共享库 + CLI 套件。
做了 5 件其他 agent 平台都没同时做的事:

1. **直接读纯 Python agent。** 不需要任何 SDK 注入。Agent 把 JSONL/markdown 写到
   `~/.<agent>/`,SFOS 直接 tail 那些文件。无需重新埋点。
2. **真闭合 L1↔L4↔L5↔L6 自演化环。** 反思日志喂 evolver(产 PR-gated 提议),
   eval 法官检测质量漂移,council 在严重下跌时辩论,council 结论回流到
   evolver 的 Haiku prompt 当上下文。全在 cron 上。
3. **本地优先观测。** `sfos-ui` 就是一个 Streamlit 仪表板,直接读那些 JSONL —— 不需要
   Phoenix 实例、不需要 Langfuse Docker、不需要 LangSmith 账号。
4. **Cron-aware 而非 request-aware。** 为周日早 8 点醒来的 agent 设计,不是实时聊天 bot。
   避开"告警疲劳"反模式。
5. **一人公司视角。** "今天有什么需要我"收件箱,不是企业级 team/project 层级。

## 快速开始

```bash
pip install 'solo-founder-os[anthropic,ui]'

# 把每周自演化环排上 launchd:
sfos-cron install   # 没 pip-install 会被 pre-flight 拒绝
# 周日 08:00  sfos-eval         Sonnet 给 skill 打分
# 周日 08:30  sfos-council      严重漂移多角度辩论
# 周日 09:00  sfos-evolver      Haiku 合成 PR 提议
# 周日 09:30  sfos-retro        跨 agent 周报 digest

# 打开本地仪表板:
sfos-ui
# → http://localhost:8501

# 多机同步:
sfos-sync init git@github.com:you/sfos-state.git
sfos-sync push
```

零账号、零云、< $0.06/周。

## 生产安装(从源码)

PyPI Trusted Publishing 配好之前,从本地克隆装 editable:

```bash
git clone https://github.com/alex-jb/solo-founder-os.git ~/Desktop/solo-founder-os
cd ~/Desktop/solo-founder-os
pip install --user -e '.[anthropic,ui]'
```

`pip install -e` 不能省 —— launchd 的中性 CWD 看不见 dev tree。
`sfos-cron install` 有 sterile-import pre-flight 检查,没装就拒绝写 plist。
加 `--ensure-pip-install` 让它自动修:

```bash
sfos-cron install --ensure-pip-install
```

如果 agent 的命令(如 `payments-agent` / `vc-outreach-agent`)装完后 PATH 上找不到,
脚本通常在 `~/Library/Python/<版本>/bin/`,大多数 shell 不会自动加这个。最简单:

```bash
ln -s ~/Library/Python/3.9/bin/payments-agent ~/.local/bin/
# (每个 agent 重复一次,或整个目录软链)
```

## 6 层自演化环

```
L1  Reflexion         ─┐  log_outcome(agent, task, FAILED, signal)
L2  Supervisor        ─┤  launchd cron 调度
L3  Skills            ─┤  record_example(skill, inputs, output)
L4  Evolver           ─┼─→  Haiku 从 L1 模式 + L6 漂移合成 PR 提议
L5  Council           ─┤    (严重漂移时 council 结论被注入 prompt)
L6  Eval (Sonnet)     ─┘
```

每个周日早上环跑一次:

1. **08:00 — `sfos-eval`** 用 Sonnet 5 维 rubric(清晰度 / 具体性 / 声音 / 准确性 /
   完整度)给每个 skill 打分。落每周分数。检测 vs 上周 > 0.5 漂移。
2. **08:30 — `sfos-council --auto-from-drift`** 给跌幅 > 0.7 的 skill 召开
   `BUG_TRIAGE` 会议(3 角色辩论 + 1 综合)。笔记落 `council-meetings/`。
3. **09:00 — `sfos-evolver`** 扫反思日志找 ≥3× 重复失败模式,同时读 L6 漂移信号。
   每个模式让 Haiku 出具体修复方案。**如果 L5 council 已经辩论过这个 skill,
   综合内容会作为额外上下文注入 Haiku prompt** —— 补丁反映多角度推理,不是单点猜测。
   产物:`evolver-proposals/` 下的 markdown(PR-gated;绝不自动合并)。
4. **09:30 — `sfos-retro`** 走每个 agent 的反思 / 偏好 / skill / 赌徒数据,产出
   一份 markdown digest:谁在跑、各自卡在什么、哪些 skill 浮现、哪个变体在赌徒赢面大。

成本:eval ~$0.04/周 + evolver ~$0.01/周 + council ~$0.005/周(严重漂移时才花)。
合计 **< $0.06/周**。

## ICPL 偏好学习(经 Inbox)

`sfos-ui` 的 Inbox tab 是规范的 HITL 审批面板。点 ✅ Approve 之前编辑了 draft,
diff 会作为 ICPL(In-Context Preference Learning)对落到
`~/.<agent>/preference-pairs.jsonl`。

下次同 agent 起草同类任务,`preference_preamble()` 把这些对作为 few-shot
exemplar 注入 system prompt 头部。Agent 输出会向你的 voice 偏移,你不用再写一句新 prompt。

任务消歧:frontmatter 里的 `task` 字段优先;退到 `platform`(marketing-agent
按 X / LinkedIn / Reddit 分);再退 `kind`;最后默认 `<slug>-draft`。

## CLI 套件

| CLI | 干什么 |
|---|---|
| `sfos-doctor` | 给所有已知 agent dir 体检 |
| `sfos-supervisor` | L2 — 自动找活给 agent |
| `sfos-evolver` | L4 — 从反思 + 漂移产 PR-gated 修复方案 |
| `sfos-council` | L5 — 多角色辩论;`--auto-from-drift` 模式 |
| `sfos-eval` | L6 — Sonnet 给 record_example 行打分 |
| `sfos-retro` | 跨 agent 周报 digest |
| `sfos-bus` | 跨终端 markdown 广播 |
| `sfos-inbox` | HITL governance 通道(sfos-ui 的命令行版) |
| `sfos-cron install` | 把 4-job 周日环排上 launchd |
| `sfos-ui` | 本地 Streamlit 仪表板(4 tab) |
| `sfos-sync` | 基于 git 的多机同步 `~/.solo-founder-os/` |

## 7 层栈(11 个 agent)

| 层 | Agent |
|---|---|
| 1. 内容 / 营销 | [orallexa-marketing-agent](https://github.com/alex-jb/orallexa-marketing-agent) |
| 2. 客户支持 | [customer-support-agent](https://github.com/alex-jb/customer-support-agent) |
| 3. 客户发现 | [customer-discovery-agent](https://github.com/alex-jb/customer-discovery-agent) |
| 4. 客户外联(冷邮) | [customer-outreach-agent](https://github.com/alex-jb/customer-outreach-agent) |
| 5. 投资人外联 | [vc-outreach-agent](https://github.com/alex-jb/vc-outreach-agent) |
| 6. 分析 + 成本 | [funnel-analytics-agent](https://github.com/alex-jb/funnel-analytics-agent), [cost-audit-agent](https://github.com/alex-jb/cost-audit-agent) |
| 7. 变现 | [payments-agent](https://github.com/alex-jb/payments-agent) |
| (横切) | [build-quality-agent](https://github.com/alex-jb/build-quality-agent), [bilingual-content-sync-agent](https://github.com/alex-jb/bilingual-content-sync-agent) |

每个 agent 都是独立 pip 包 + CLI + 可选 MCP server。共享这个库的 HITL queue / Anthropic
client / 反思日志 / skill 蒸馏 / L1-L6 环原语。

## 测试隔离

测试套件的 `conftest.py` 里加 `SFOS_TEST_MODE=1`:

```python
# tests/conftest.py
import os
os.environ.setdefault("SFOS_TEST_MODE", "1")
```

这会拦截 `log_outcome`、`record_example`、`log_edit` 在 pytest 期间往
`~/.<agent>/` 的写。否则 agent 测试夹具会污染生产反思数据,喂给 L4 evolver
假阳性提议。(栈里 8 个 agent 都已经这么做。)

## 隐私

- 所有状态只在本机 `~/.<agent>/` 和 `~/.solo-founder-os/`。
- `sfos-sync` 自带 `.gitignore` 排除 `usage.jsonl` / `cron-logs/` / `cron/` /
  `bandit.sqlite` —— Stripe 形状的 token、成本日志、机器特定状态永远不会上远程 git。
- Anthropic 客户端只把成本(input/output tokens)记到 `usage.jsonl` —— 不记 prompt 内容。
- L4 evolver 的安全闸门拒绝任何触碰 `auth` / `secret` / `credential` / `smtp` /
  `stripe` / `billing` / `anthropic_client` / `migrations` 的补丁,即使 Haiku 这么提议也不行。

## 这些设计取舍的理由

- **Streamlit 不是 React/FastAPI** —— 调研显示一人公司每天 ~30 分钟,分两批用。
  实时监控在这个量级下是告警疲劳反模式。`st.fragment(run_every="3s")` 用本地轮询给
  "实时感",不用起第二个进程。
- **垂直时间线而不是 chat-bubble** —— SFOS agent 之间是异步、文件中介通信。
  Chat bubble 会"骗你",假装是同步聊天。
- **Council 默认阈值 0.7 / Evolver 0.5** —— 一场 council ≈ 5 个 Haiku 调用。
  阈值更严保证就算每周都漂移,自主开销也 < $0.06/周。
- **PR-gated 永不自动合并** —— evolver 写的是 markdown 给你 review。文件白名单+黑名单
  硬编码,不让 Haiku 改。

## 状态

v0.27 (2026-05-04)。生产环已在维护者机器上跑;**首次自动 fire 2026-05-11 周日**。
**512 个 SFOS 测试**;全 11 agent 栈合计 **1100+ 测试**。

## License

MIT。随便用。
