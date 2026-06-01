# 资源成本结算实现 Review

日期：2026-06-01

本文记录本轮在 Foundry、ccfoundry-agent-kit 以及 LiteLLM 网关侧的实现状态，方便 review。原始设计和阶段计划见 `docs/resource-cost-accounting-plan.md`，本文只写“已经实现了什么、验证了什么、还差什么”。

## 结论

bounty settlement 路径现在已经具备按任务归因 Foundry 资源成本的主链路：

```text
gross_reward_usd
- llm_cost
- sandbox_cost
- feature_cost
= net_payout_usd
```

Foundry 与 agent-kit 侧已经把 `invocation_id` 贯穿到 bounty execution、sandbox start/stop、deliverable submit、settlement ledger。sandbox 成本现在可以按任务归因并从 reward 中扣除。

本轮继续推进后，外部 agent 使用 Foundry 提供 LiteLLM gateway 的 LLM 成本归因链路已经具备代码实现和 LiteLLM host 侧 staged 配置。上线时仍需要把 Foundry 新代码部署，并在 Foundry 与 LiteLLM 两侧配置同一个 shared secret。

这个链路不只是“改 llm 机器”：

- LiteLLM host 要发出每次模型调用的 usage/cost，并带上 `invocation_id`。
- Foundry repo 要接收、鉴权、校验、去重，并把 model usage 写入 `agent_usage_ledger`。
- agent-kit / agent 示例要保证调用 Foundry-provided LLM 时携带同一个 `billing_context`。

## 已实现：Foundry 侧

代码仓：`/opt/cochiper_foundry`

### 阶段 1：任务级 Billing Context

已实现 bounty pull runtime 的任务级 billing context 注入：

- `server/services/agent_runtime_service.py`
  - bounty invocation 被 claim 后，会把 `billing_context` 注入到 job payload。
  - `billing_context` 包含 `invocation_id`、`requirement_id`、`job_name`。
  - 同步把该 context 放进 agent-facing payload，避免 agent 只能从外层字段猜测。

- `server/routers/agent_discovery.py`
  - deliverable submit 现在要求传入 `invocation_id`。
  - submit 时校验 invocation 存在、属于当前 agent，并且匹配当前 bounty requirement。
  - 缺失或不匹配的 `invocation_id` 不再进入 paid settlement fallback。

### 阶段 2：Sandbox 归因与入账

已实现 external agent sandbox 与 bounty invocation 的绑定：

- `server/services/sandbox_service.py`
  - `start_external_agent_sandbox()` 支持 `invocation_id`、`requirement_id`、`billing_context`。
  - active sandbox 如果已经绑定到另一个 invocation，会拒绝复用，避免并发任务串账。
  - `stop_external_agent_sandbox()` 支持 `invocation_id`。
  - sandbox finalize 时把 `invocation_id`、`requirement_id` 等 metadata 写入 compute/feature ledger。
  - 对旧的未绑定 active sandbox，stop 时允许一次性绑定当前 invocation 后再 finalize，便于平滑过渡。

- `server/services/agent_internal_service.py`
- `server/routers/agent_internal.py`
- `server/routers/agents.py`
  - sandbox start/stop API 都支持可选 billing payload。

### 阶段 3：资源成本聚合与净结算

已实现结算扣成本：

- `server/services/agent_accounting_service.py`
  - 新增 `get_resource_cost_for_invocation(invocation_id)`。
  - 聚合同一 invocation 下的 `model`、`compute`、`feature` ledger。
  - 排除 `agent_service_fee`、`unpriced`、非正数成本。
  - 返回 model/sandbox/feature 分类成本、总资源成本、entries、resource entry IDs。
  - `record_settlement()` 支持写入 `invocation_id`，并把 settlement items 和 mandate 写入 metadata。

- `server/routers/agent_discovery.py`
  - deliverable 验证通过后，先 finalize 对应 invocation 的 sandbox。
  - 然后聚合资源成本并计算：
    - `gross_reward_usd`
    - `resource_cost_usd`
    - `net_payout_usd`
    - `unrecovered_resource_cost_usd`
  - settlement items 中：
    - `task_reward` 是正数。
    - `llm_cost`、`sandbox_cost`、`feature_cost` 是负数。
  - Stripe 只支付 net payout。
  - 如果 net payout 为 0，会跳过 Stripe，并记录 `skipped_zero_payout`。
  - 同时写 `bounty_submissions` 和 `agent_usage_ledger` 的 settlement entry。
  - bounty settlement list 优先从 DB-backed `bounty_submissions` 读取。

### 阶段 4：Schema

启动迁移已加入 `server/core/lifespan.py`：

- `foundry.bounty_submissions.invocation_id BIGINT`
- accepted 且非空 `invocation_id` 的唯一索引
- `agent_usage_ledger(invocation_id, category, created_at DESC)` 查询索引
- `foundry.litellm_usage_events`，用于按 LiteLLM event id 做幂等入账

### 阶段 5：LiteLLM Usage Ingestion

已新增 Foundry 内部接收端：

- `server/routers/internal_billing.py`
  - 新增 `POST /api/internal/billing/litellm-usage`。
  - 使用 `FOUNDRY_LITELLM_USAGE_SECRET` 做 bearer token 鉴权。
  - 从 payload metadata 中提取 `foundry_invocation_id` / `foundry_agent_name` / `foundry_requirement_id`。
  - 校验 invocation 存在、agent 匹配、requirement 匹配。
  - 标准化 token usage 和 LiteLLM reported cost。

- `server/services/agent_accounting_service.py`
  - 新增 `record_litellm_usage_once()`。
  - 先写 `foundry.litellm_usage_events`，用 `event_id` 去重。
  - 同事务写 `agent_usage_ledger` 的 `model/chat_completion` row。

- `server/services/bridge_service.py`
  - `/api/bridge/v1` LLM proxy 会识别 request body 中的 billing metadata。
  - 如果请求带 `foundry_invocation_id`，bridge 自己写 model ledger 时会带同一个 `invocation_id`。
  - 同时给上游 LiteLLM metadata 加 `foundry_skip_usage_callback=true`，避免 bridge 路径与 LiteLLM callback 双重入账。

## 已实现：ccfoundry-agent-kit 侧

代码仓：`/opt/ccfoundry-agent-kit`

### 阶段 1：SDK Models 与 Pull Runtime

- `packages/python-sdk/src/ccfoundry_agent_kit/models.py`
  - 新增 `BillingContext`。
  - 新增 `SettlementBreakdown`。
  - 更新 settlement item 示例，把 Foundry 资源成本表达为负项。

- `packages/python-sdk/src/ccfoundry_agent_kit/__init__.py`
  - 导出新增 models。

- `packages/python-sdk/src/ccfoundry_agent_kit/pull_runtime.py`
  - bounty handler 会收到 `billing_context`。
  - 强制 `billing_context.invocation_id` 与实际 invocation ID 对齐。
  - complete metadata 中包含 billing context。

### 阶段 2：Sandbox Client

- `packages/python-sdk/src/ccfoundry_agent_kit/sandbox_client.py`
  - `start()` 支持 `invocation_id`、`requirement_id`、`billing_context`。
  - `stop()` 支持 `invocation_id`。

### 阶段 3：示例 Agent

- `examples/me_agent/src/me_agent_example/app.py`
  - 读取 bounty `billing_context`。
  - start sandbox 时带 invocation-scoped context。
  - submit deliverable 前 stop sandbox。
  - submit deliverable 时带 `invocation_id`。
  - response metadata 中展示 gross/resource/net settlement breakdown。

### 阶段 4：文档

已更新：

- `docs/resource-cost-accounting-plan.md`
- `docs/sdk.md`
- `docs/foundry-onboarding.md`
- `packages/python-sdk/README.md`
- `examples/me_agent/README.md`
- `README.md`

### 阶段 5：LLM Metadata Helper

已新增：

- `packages/python-sdk/src/ccfoundry_agent_kit/llm_metadata.py`
  - 提供 `foundry_llm_metadata(billing_context, agent_name=..., extra=...)`。
  - 输出 LiteLLM callback 可识别的安全 metadata：
    - `foundry_invocation_id`
    - `foundry_requirement_id`
    - `foundry_agent_name`
    - `billing_context`

- `examples/me_agent/src/me_agent_example/app.py`
  - bounty LLM generation 调用会通过 `extra_body.metadata` 传入 `foundry_llm_metadata(...)`。

## 验证结果

Foundry targeted tests：

```bash
PYTHONPATH=. pytest \
  tests/test_agent_accounting_service.py \
  tests/test_bounty_verification.py \
  tests/test_sandbox_service.py \
  tests/test_agent_runtime_service.py \
  tests/test_agents_router.py \
  tests/test_agent_internal_router.py \
  -q
```

结果：`52 passed`。

此前包含 discovery router 覆盖的 targeted run：

```text
58 passed
```

agent-kit tests：

```bash
PYTHONPATH=packages/python-sdk/src pytest packages/python-sdk/tests -q
```

结果：`14 passed`。

本轮新增 helper 后结果：`16 passed`。

新增 Foundry tests：

```bash
PYTHONPATH=. pytest tests/test_agent_accounting_service.py tests/test_internal_billing_router.py -q
```

结果：`5 passed`。

修改过的 Foundry、SDK、example 文件都做过 compile check。

Foundry full test suite 当前状态：

```text
648 passed, 2 failed
```

失败项在 `tests/test_sub_agent_execution_service.py`：

- `test_extract_task_plan_ignores_bare_step_list_for_repair`
- `test_task_plan_result_repairs_invalid_shape`

这两个失败不在 bounty accounting、sandbox、settlement、LiteLLM 路径上。

## LiteLLM Host 检查结果

检查位置：

```text
llm:/opt/litellm_mcp
```

看到的主要文件：

- `server.py`
- `.agent/litellm_mcp/server.py`
- `config.yaml`
- `Dockerfile`
- `docker-compose.yml`
- `README.md`

运行中的容器：

- `litellm_proxy`
- `litellm_mcp`

### 当前 MCP Server 行为

`server.py` 暴露的是 MCP 工具：

- 查询 available models。
- 查询 service health。
- 通过 `/key/info` 查询 key quota/spend。

它本身仍只是 MCP 工具层，不承担 usage 回调；usage 回调通过 LiteLLM proxy 的 custom callback 实现。

### 当前 LiteLLM Config

`config.yaml` 包含：

- model list
- router settings
- master key
- database URL

本轮已 staged：

- `success_callback: foundry_usage_callback.foundry_usage_callback`
- `docker-compose.yml` 挂载 `./foundry_usage_callback.py:/app/foundry_usage_callback.py:ro`
- `/opt/litellm_mcp/foundry_usage_callback.py`

仍需上线时配置：

- `FOUNDRY_LITELLM_USAGE_URL`
- `FOUNDRY_LITELLM_USAGE_SECRET`

### 当前容器能力

`Dockerfile` 固定了：

```text
litellm[proxy]==1.82.6
```

容器日志显示 LiteLLM 已经在更新 key/user/team/agent spend queue，说明 LiteLLM 自己能算 spend，但 Foundry 还没有收到按 invocation 归因的 ledger rows。

### LiteLLM Callback 能力确认

LiteLLM 1.82.6 支持：

- config-level `success_callback`
- 通过 `module.instance` 加载自定义 callback
- `CustomLogger.async_log_success_event()`

callback 里可以拿到 `standard_logging_object`，其中包括：

- `id`
- `trace_id`
- `model`
- `model_group`
- `response_cost`
- `prompt_tokens`
- `completion_tokens`
- `total_tokens`
- `metadata`
- `end_user`

本轮已经按这个路线在 `llm:/opt/litellm_mcp` staged custom callback。

### 2026-06-01 Cloud Run E2E 监测结果

新 bounty：

```text
5e880987-dc74-4444-8691-9a8046149ff6
```

监测结果：

- Cloud Run `test11` 通过 scheduler 自动 poll 并 claim 任务。
- invocation `219459`，类型 `bounty_execute`，状态 `succeeded`。
- gvm 本地 `test11` runtime 为 `stopped`，没有本地进程抢单。
- submission `6b713d98-39d1-494e-b777-65882e99162d` 状态 `accepted`。
- settlement `bounty-a48c4ab3a8d4` 已记录。
- Stripe PaymentIntent `pi_3TdOfxE9l62Y3b4S0SWW40H4` 已创建。
- iverilog sandbox 验证通过，交付文件为 `rra.v`、`rra_tb.v`。

本次 ledger：

```text
compute/external_sandbox_runtime       47.546062 seconds   $0.00
feature/external_sandbox_usage_event   4 events            $0.00
feature/external_sandbox_terminal_input 2 events           $0.00
settlement/task_completion             1 task              $2.00
```

当前 settlement breakdown：

```text
gross_reward_usd   $2.00
resource_cost_usd  $0.00
net_payout_usd     $2.00
```

注意：这单执行时确实发生了 Foundry-provided LLM 调用，LiteLLM 日志显示：

```text
model: gemini/gemini-3.5-flash
end_user: foundry-invocation-219459
prompt_tokens: 1926
completion_tokens: 1996
spend: 0.020853
metadata.foundry_invocation_id: 219459
```

但 `foundry.litellm_usage_events` 对 invocation `219459` 仍为空。本次原因不是 agent metadata 缺失，而是运行中的 `litellm_proxy` 容器尚未加载 callback：

- 容器没有挂载 `/app/foundry_usage_callback.py`。
- `FOUNDRY_LITELLM_USAGE_URL` 为空。
- `FOUNDRY_LITELLM_USAGE_SECRET` 为空。

已修复部署状态：

- Foundry `.env` 已配置 `FOUNDRY_LITELLM_USAGE_SECRET`。
- LiteLLM `.env` 已配置 `FOUNDRY_LITELLM_USAGE_URL` 和同一个 secret。
- `litellm_proxy` 已重新创建。
- 当前容器已挂载 `/app/foundry_usage_callback.py`。
- 当前容器内 callback import 通过：`callback_import ok FoundryUsageCallback`。
- Foundry 内部 ingest endpoint auth smoke test 返回 `400 foundry_invocation_id is required`，说明 secret 校验已通过，endpoint 已可达。

这次 bounty 的 LLM 成本没有 retroactive 回填到 settlement，因为 settlement 已在 callback 修复前完成。下一张 bounty 应验证 `litellm_usage_events` 和 `agent_usage_ledger` 的 `model/chat_completion` row 能在 settlement 前出现，并被扣进 `resource_cost_usd`。

### 2026-06-01 rra4 / v1 Cloud Run 复测

新 bounty：

```text
01c61431-23b1-4910-a5d4-9136f9d7ab5a
```

复测结果：

- Cloud Run `v1_agent_ext` 自动 poll 并 claim `rra4`。
- invocation `219860`，类型 `bounty_execute`，状态 `succeeded`。
- submission 状态 `accepted`，settlement `bounty-1c7350247c14` 已记录。
- agent 结果显示 `llm_generate` 使用 `gemini-3.5-flash`，sandbox iverilog 验证通过。

本次 settlement 已走新的 bounty resource-cost aggregation 路径，但最终成本仍为 `$0.00`：

```text
gross_reward_usd   $4.00
resource_cost_usd  $0.00
net_payout_usd     $4.00
```

ledger 中已经有同一 invocation 的 sandbox resource rows，说明 Foundry 侧核算链路被调用了：

```text
compute/external_sandbox_runtime          43.19168 seconds   $0.00
feature/external_sandbox_usage_event      4 events            $0.00
feature/external_sandbox_terminal_input   2 events            $0.00
settlement/task_completion                1 task              $4.00
```

未扣 sandbox 的直接原因：

- `USER_SANDBOX_RUNTIME_CREDIT_PER_HOUR` 当前系统配置为 `0.0`。
- `external_sandbox_runtime` 使用 `_runtime_cost(...)` 的显式结果入账，所以 ledger 标记为 `pricing_source=explicit`，但 cost 为 0。
- `AGENT_ACCOUNTING_COMPUTE_PRICING_JSON` / `AGENT_ACCOUNTING_FEATURE_PRICING_JSON` 目前未配置兜底价格。

未扣 LLM 的直接原因：

- LiteLLM 日志显示这次请求确实产生了 spend：`response_cost=0.020853`，并且 metadata 里有 `foundry_invocation_id=219860`。
- `foundry.litellm_usage_events` 对 invocation `219860` 仍为空。
- root cause 是 LiteLLM 1.82.6 proxy success callback 把 Foundry metadata 放在 top-level `kwargs["metadata"]`；原 callback 只读取 `standard_logging_object.metadata` 和 `litellm_params.metadata`，因此被调用后提前 return。

已修复：

- 已在 `llm:/opt/litellm_mcp/foundry_usage_callback.py` 增加 top-level `kwargs["metadata"]` 和 proxy request body metadata fallback。
- callback 会优先使用 LiteLLM key metadata 中的 canonical Foundry agent name，例如 `v1_agent_ext`，避免 source 名 `v1` 与 invocation agent 不一致导致 ingest 拒绝。
- callback 现在只上报 Foundry 相关安全 metadata，不把 proxy headers 或 key metadata 带回 Foundry。
- 已重启 `litellm_proxy`。
- 已用容器内 monkeypatch smoke test 验证 callback 能从 top-level metadata 构造 payload：`invocation_id=219860`、`agent_name=v1_agent_ext`、cost/tokens 正常，且 headers 不进入 payload metadata。

这次 rra4 settlement 已经在修复前完成，因此没有 retroactive 修改。下一张 bounty 应看到：

- `foundry.litellm_usage_events` 出现同一 invocation 的 usage event。
- `agent_usage_ledger` 出现 `model/chat_completion` row。
- settlement `llm_cost` 扣除 LiteLLM reported cost。
- sandbox 是否扣费取决于是否把 sandbox runtime rate / compute pricing 从 0 改为非 0。

### 2026-06-01 rra5 / sandbox `$4/hour` 复测

新 request：

```text
36e5b166-7a7f-4e0d-8c55-aa7dcaf3a531
```

复测结果：

- `USER_SANDBOX_RUNTIME_CREDIT_PER_HOUR=4`，更新时间 `2026-06-01 08:29:29 UTC`。
- Cloud Run `v1_agent_ext` 自动接单。
- invocation `219990`，状态 `succeeded`。
- submission `4740c3e8-c88f-4ad9-a62f-1736ef1dbbcb`，状态 `accepted`。
- settlement `bounty-c2f99180ce07`。

本次 sandbox cost 已正确体现在 settlement 中：

```text
gross_reward_usd   $5.0000
sandbox_cost_usd   $0.0465
resource_cost_usd  $0.0465
net_payout_usd     $4.9535
```

ledger：

```text
compute/external_sandbox_runtime          41.809681 seconds   $0.0465
feature/external_sandbox_usage_event      4 events            $0.0000
feature/external_sandbox_terminal_input   2 events            $0.0000
settlement/task_completion                1 task              $4.9535
```

这验证了 bounty settlement 的 sandbox 成本扣款路径已经可用。

LLM 仍未进入本次 settlement：

- agent 结果显示 `llm_generate` 使用 `gemini-3.5-flash`。
- LiteLLM proxy 日志显示 invocation `219990` 的请求产生 `response_cost=0.020853`。
- `foundry.litellm_usage_events` 对 invocation `219990` 仍为空。

进一步排查后发现，LiteLLM 1.82.6 custom callback 实际还能把原始请求 metadata 放在 `kwargs["kwargs"]["metadata"]`。上一版 callback 覆盖了 top-level metadata，但没有覆盖这一层。因此本次 request 仍未把 usage POST 到 Foundry。

已追加修复：

- `llm:/opt/litellm_mcp/foundry_usage_callback.py` 现在同时读取：
  - `standard_logging_object.metadata`
  - top-level `kwargs["metadata"]`
  - nested `kwargs["kwargs"]["metadata"]`
  - `kwargs["kwargs"]["optional_params"]["metadata"]`
  - `litellm_params.metadata`
  - proxy request body metadata
- 已重新部署并重启 `litellm_proxy`。
- health check 正常。
- 容器内 smoke test 验证 nested metadata 能解析出 `invocation_id=219990`、canonical `agent_name=v1_agent_ext` 和 reported cost。

没有对 invocation `219990` 做 retroactive LLM 入账，避免污染已经完成的 settlement。下一张 bounty 应验证 LLM usage 在 settlement 前进入 `litellm_usage_events` / `agent_usage_ledger`，并出现在 `llm_cost` 中。

### 2026-06-01 rra6 / callback hook 修复复测

新 request：

```text
f6bb9435-284d-45c5-88e7-750f08de0b74
```

复测结果：

- Cloud Run `v1_agent_ext` 自动接单。
- invocation `220021`，状态 `succeeded`。
- submission `fea812d2-4685-4b01-ae54-13c169292a34`，状态 `accepted`。
- settlement `bounty-972073beecf7`。
- sandbox iverilog 验证通过。

本次 sandbox cost 继续正确体现在 settlement 中：

```text
gross_reward_usd   $6.0000
sandbox_cost_usd   $0.0482
resource_cost_usd  $0.0482
net_payout_usd     $5.9518
```

ledger：

```text
compute/external_sandbox_runtime          43.37657 seconds   $0.0482
feature/external_sandbox_usage_event      4 events            $0.0000
feature/external_sandbox_terminal_input   2 events            $0.0000
settlement/task_completion                1 task              $5.9518
```

但本次 LLM 仍没有进入 settlement：

- LiteLLM 日志显示 invocation `220021` 的调用产生 `response_cost=0.020853`。
- request metadata 包含 `foundry_invocation_id=220021` 和 `foundry_requirement_id=f6bb9435-284d-45c5-88e7-750f08de0b74`。
- `foundry.litellm_usage_events` 对 invocation `220021` 为空。
- settlement items 中 `llm_cost=0.0`。

最终 root cause：

- LiteLLM 1.82.6 proxy 内部实际使用 async completion path。
- `litellm_settings.success_callback` 会把 custom callback 先尝试注册到 success callbacks。
- LiteLLM 只有在 callback 实例本身是 async callable 时，才会把它路由进 `_async_success_callback`。
- 原 `FoundryUsageCallback` 只实现了 `async_log_success_event()`，实例本身不是 async callable，因此启动时被放进 sync success callback；在 async completion path 中没有执行实际 `log_success_event()`，导致没有 POST 到 Foundry。

已修复并部署到 `llm:/opt/litellm_mcp/foundry_usage_callback.py`：

- 增加 async `__call__()`，让 LiteLLM 启动时把 callback 放进 `Async Success Callbacks`。
- 保留 sync `log_success_event()` fallback。
- event id 缺失时 fallback 到 `response_obj.id`，再兜底到 `foundry:{invocation_id}:{start_time}`。
- 增加低噪声日志：成功记录、Foundry 标记存在但缺 invocation、Foundry ingest 非 2xx。

修复后 smoke test：

- 使用不存在的 invocation `999999997` 发起低成本 LiteLLM completion。
- LiteLLM 日志显示 `FoundryUsageCallback` 已进入 `Async Success Callbacks`。
- callback 对 Foundry 发起 `POST /api/internal/billing/litellm-usage`。
- Foundry 返回预期 `404 Invocation 999999997 not found`。
- Foundry app log 也记录了该 internal billing POST。

这证明 LiteLLM -> Foundry usage callback 通道已经打通。本次 `220021` settlement 已在修复前完成，没有 retroactive 修改。下一张真实 bounty 应验证 `model/chat_completion` row 在 settlement 前出现，并使 `llm_cost` 与 `sandbox_cost` 同时扣入 net payout。

## 剩余工作是不是只有 LLM？

核心代码已补上，剩余是部署配置与 e2e 验证，不是单仓单点。

### Foundry 已完成，部署时还需要做

- 内部 LiteLLM usage ingestion endpoint 已新增：

```text
POST /api/internal/billing/litellm-usage
```

- 部署环境需要配置 shared secret：

```text
Authorization: Bearer <FOUNDRY_LITELLM_USAGE_SECRET>
```

- 已实现校验：
  - invocation 必须存在。
  - agent 必须匹配。
  - requirement metadata 如果存在也要匹配。

- 已通过 `foundry.litellm_usage_events.event_id` 对 LiteLLM callback retry 做去重。

- 已调用：

```python
AgentAccountingService.record_model_usage(
    invocation_id=...,
    agent_name=...,
    username=...,
    model_name=...,
    usage=...,
    reported_cost=response_cost,
    metadata=...
)
```

- 现有 `/api/bridge/v1` LLM proxy 路径已补充 bounty metadata 提取。

### LiteLLM Host 已 staged，部署时还需要做

- 已新增 callback module：

```text
/opt/litellm_mcp/foundry_usage_callback.py
```

- 已在 `config.yaml` 中加入：

```yaml
litellm_settings:
  success_callback:
    - foundry_usage_callback.foundry_usage_callback
```

- 仍需配置：
  - Foundry callback URL
  - callback shared secret
  - timeout
  - retry/backoff 策略

- callback 行为已实现：
  - 从 `standard_logging_object.metadata` 读取 `foundry_invocation_id`。
  - 没有 invocation 的调用直接忽略，不入 bounty ledger。
  - 只发送 usage/cost/accounting metadata，不发送 prompt 或 completion。
  - callback 失败不阻塞模型调用，但要记录日志，便于补账。

### agent-kit / agents 已完成，后续还可推广到更多示例

- 已增加 SDK helper，让 OpenAI-compatible LLM request 可带：

```text
metadata.foundry_invocation_id
metadata.foundry_requirement_id
metadata.foundry_agent_name
```

- `examples/me_agent` bounty LLM generation 已把 bounty `billing_context` 传入模型请求 metadata。

## 推荐下一阶段实现

### Phase A：Foundry LLM Usage Ingestion

先在 Foundry 加 callback 接收端，并写单元测试：

- 缺 secret：拒绝。
- secret 错误：拒绝。
- 缺 invocation：拒绝或忽略，按 endpoint 语义定。
- invocation/agent 不匹配：拒绝。
- 正常 payload：写入 `agent_usage_ledger` 的 `model` row。
- 重复 `litellm_event_id`：不重复入账。

### Phase B：LiteLLM Callback

在 `llm:/opt/litellm_mcp` 加 custom callback，并用一条低成本 completion 验证：

- callback 能加载。
- callback 能拿到 `metadata`。
- Foundry 能收到 usage/cost。
- Foundry ledger 中 model row 带同一个 `invocation_id`。

### Phase C：agent-kit LLM Metadata Helper

提供小工具函数，避免每个 agent 手写 metadata：

```python
metadata = foundry_llm_metadata(billing_context)
```

这个 helper 只返回计费安全 metadata，不包含 prompt、completion、用户隐私文本。

### Phase D：E2E

最小 e2e：

1. claim bounty。
2. start sandbox，带 billing context。
3. 调一次 LiteLLM completion，带 `foundry_invocation_id`。
4. stop sandbox。
5. submit deliverable。
6. 校验：
   - `agent_usage_ledger` 有同一 `invocation_id` 的 `model`、`compute`、`settlement` rows。
   - settlement 同时扣 LLM 和 sandbox。
   - Stripe amount 等于 `net_payout_usd`。

## 注意事项

- 不要把 agent 自报 LLM cost 当成 Foundry verified cost。
- 不要扣除 agent 自带 API key 直连模型产生的成本。
- paid bounty settlement 不应绕过 `invocation_id`。
- sandbox 没 finalize 前不要做最终 Stripe payout。
- `service_fee` 是 agent 收入项，不是 Foundry resource cost。
