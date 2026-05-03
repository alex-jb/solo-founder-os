# solo-founder-os

[English](README.md) | **中文**

> Solo Founder OS agent stack 的共享基础库。所有 alex-jb agent 都依赖它。Source/MetricSample/SourceReport ABC、7 天 baseline + 自动归档、HTTP 重试、Anthropic 客户端 + 自动 cost log、推送(ntfy / Telegram / Slack)、brief 渲染、测试工具。

[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/solo-founder-os.svg)](https://pypi.org/project/solo-founder-os/)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](#)

## 为什么存在

[Alex Ji](https://github.com/alex-jb) 为了 [VibeXForge](https://github.com/alex-jb/vibex) 上线在 2 天内 ship 了 7 个 agent,然后发现 7 个 repo 之间 **60-70% 是重复代码**:每个 agent 各自实现 retry / env loader / JSONL append / severity 等级 / mock urlopen 模板。这个库是"一处修一次,所有 agent 受益"的中央位置。

## v0.1 的内容

```
solo_founder_os/
├── source.py              # Source ABC、SourceReport、MetricSample
├── http.py                # urlopen_json、with_retry 装饰器
├── baseline.py            # 7 天中位数 + 自动归档到 .gz
├── usage_log.py           # JSONL append + 成本计算
├── anthropic_client.py    # 带优雅降级 + 自动日志的 Claude 客户端
├── notifier.py            # Ntfy / Telegram / Slack + fan_out()
├── brief.py               # Markdown 渲染(严重等级分区 + 摘要)
└── testing.py             # pytest 工具(fake_urlopen、fake_anthropic)
```

## 使用

```python
from solo_founder_os import (
    Source, SourceReport, MetricSample,
    urlopen_json, with_retry,
    AnthropicClient,
    enrich_with_baseline, record_samples,
)

class MyVercelSource(Source):
    name = "vercel"

    @property
    def configured(self) -> bool:
        return bool(os.getenv("VERCEL_TOKEN"))

    @with_retry(times=3)
    def _api(self, path):
        return urlopen_json(f"https://api.vercel.com{path}",
                            headers={"Authorization": f"Bearer {os.getenv('VERCEL_TOKEN')}"})

    def fetch(self) -> SourceReport:
        report = SourceReport(source=self.name, fetched_at=datetime.now(timezone.utc))
        if not self.configured:
            report.error = "缺 VERCEL_TOKEN"
            return report
        try:
            data = self._api("/v6/deployments")
        except Exception as e:
            report.error = f"API 错误:{e}"
            return report
        # ... 组装 metrics ...
        return report
```

## 用这个的 agent

| Agent | 状态 |
|---|---|
| funnel-analytics-agent | 已迁移 v0.6.0 |
| cost-audit-agent | 迁移中 v0.2.0 |
| vc-outreach-agent | 迁移中 v0.3.0 |
| build-quality-agent | 迁移中 v0.4.0 |
| customer-discovery-agent | 迁移中 v0.2.0 |
| marketing-agent | 迁移中(并行 session)|
| (未来 agent) | 从这里开始 |

## Roadmap

- [x] **v0.1** —— Source/MetricSample · baseline 带归档 · HTTP retry · Anthropic 客户端 · notifiers · brief · 测试工具 · 57 tests
- [ ] **v0.2** —— HITL markdown 队列(从 vc-outreach 抽)· CLI 骨架(共享 --skip / --no-baseline / --notify / --out)
- [ ] **v0.3** —— Provider ABC 给 cost-audit(billing 镜像版的 Source)· cron 表达式生成器 · launchd plist 生成器
- [ ] **v0.4** —— Claude summarizer(从 funnel v0.5 抽)
- [ ] **v0.5** —— 跨 agent 集成 helper(一个 agent 的 report 被另一 agent 消费)

## 协议

MIT。
---

## 🧩 [Solo Founder OS](https://github.com/alex-jb/solo-founder-os) agent stack 的一员

一组共享 `solo-founder-os` 底座(Source/MetricSample 契约、HITL queue、AnthropicClient、notifiers、scheduler)的 MIT-licensed agents,逐渐在长。每个独立可用,组合起来覆盖单干创始人的全工作流。

| Agent | 干啥 |
|---|---|
| [build-quality-agent](https://github.com/alex-jb/build-quality-agent) | Pre-push diff 审查 + 本地 build runner — 在 push 前拦住会挂 CI 的改动 |
| [customer-discovery-agent](https://github.com/alex-jb/customer-discovery-agent) | Reddit 痛点抓取 + Claude 聚类做产品验证 |
| [funnel-analytics-agent](https://github.com/alex-jb/funnel-analytics-agent) | 每日 brief + 实时告警,跨 9 个 source(Vercel、GitHub、Supabase 等) |
| [vc-outreach-agent](https://github.com/alex-jb/vc-outreach-agent) | 投资人 cold email 起草 + HITL queue + SMTP 发送 |
| [cost-audit-agent](https://github.com/alex-jb/cost-audit-agent) | 月度账单审计跨 6 个 provider,标注美元 waste |
| [bilingual-content-sync-agent](https://github.com/alex-jb/bilingual-content-sync-agent) | EN ⇄ ZH i18n diff + Claude 翻译 + HITL apply |
| [orallexa-marketing-agent](https://github.com/alex-jb/orallexa-marketing-agent) | 给 OSS 创业者用的 AI 营销 agent — 自动产出各平台专属营销稿 |

*每个 agent 自己的行在它自己 README 里被去掉。挑能解决你真问题的装。*
