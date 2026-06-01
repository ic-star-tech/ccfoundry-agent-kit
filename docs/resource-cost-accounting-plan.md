# Foundry Resource Cost Accounting Implementation Plan

本文档记录 verilog-module-writer 跑通 Foundry bounty / Stripe 收款流程后发现的资源成本缺口，并把后续实现拆成阶段。这里的“资源成本”主要指 agent 在执行 Foundry 任务时使用 Foundry 侧资源产生的成本，例如 LLM gateway、Foundry sandbox runtime，以及后续可能加入的托管 feature/tool 调用。

## v2 决策

2026-06-01 的细化协议确认了以下口径，后续实现以这些规则为准：

- 一个 bounty invocation 对应一个 agent 和一个 Foundry sandbox session，不做跨任务分摊。
- Foundry 只扣 Foundry 自己提供的资源成本；agent 自带 API key 直连 LLM 的成本不从 reward 扣除。
- `invocation_id` 是必填关联键；没有 `invocation_id` 的 paid deliverable settlement 直接拒绝。
- `bounty_submissions` 负责幂等控制，`agent_usage_ledger` 负责财务真相，两边都要写。
- `service_fee` 只适用于 chat 场景；bounty 场景由 Foundry 自动从 gross reward 中扣除 verified resource cost。

## 目标

当前流程里，agent 可以收到 Foundry 侧通过 Stripe 支付的任务金额，但 Foundry 提供给 agent 的 LLM、sandbox 等资源成本没有进入最终结算。目标是让每次任务结算都能清楚地区分：

```text
gross_reward = Foundry/client 为任务承诺的任务金额
platform_resource_cost = llm_cost + sandbox_cost + feature_cost
net_agent_payout = max(0, gross_reward - platform_resource_cost)
```

结算记录应同时能回答三个问题：

- 这个任务承诺给 agent 的毛收入是多少。
- agent 使用了哪些 Foundry 资源，各自产生多少成本。
- 最终实际支付给 agent 的净收入是多少。

## 当前发现

### 已经存在的能力

- 协议层已有 agent service fee 设计：agent 可以返回 `service_fee` / `service_fee_detail`，Foundry 侧按上限记录。
- agent-kit SDK 的 settlement models 已经预留了 `llm_tokens`、`sandbox_compute`、`task_reward` 等 item 类型。
- Foundry 侧已经有 `agent_usage_ledger`，可以记录 `model`、`compute`、`feature`、`settlement` 等类别。
- Foundry 侧 sandbox runtime 已经有成本计算逻辑；外部 agent sandbox 结束时会写入 `external_sandbox_runtime` compute 事件。
- discovery / registry 已经能为 `foundry_sandbox` 暴露 sandbox runtime 单价。

### 当前缺口

- public deliverable bounty 路径验证 deliverable 后直接按 bounty budget / payment criteria 计算 Stripe 支付金额，没有读取 `agent_usage_ledger` 里的 LLM / sandbox 成本。
- 当前 public bounty 结算主要写入 `bounty_submissions` 和内存 settlement 列表，没有统一写入 `agent_usage_ledger` 的 `settlement` 类别。
- LLM / sandbox 使用记录缺少稳定的任务级关联键，例如 `task_ref`、`requirement_id`、`invocation_id`。因此即使 ledger 中存在 sandbox runtime，也很难精确归因到某一次 bounty。
- SDK 的 pull bounty runtime 当前只提交 deliverable metadata，没有提交或携带 resource usage / billing scope。
- Sandbox 成本通常在 stop/finalize 时入账。如果 agent 在提交 deliverable 前没有 stop sandbox，结算时看到的成本可能是不完整的。
- Foundry quota 侧当前主要合并 audit log、spend cache 和部分 feature ledger；外部 agent 的 model / compute ledger 并没有自然进入某个人类用户的预算口径。需要明确资源成本由 bounty 扣除、client 支付、平台补贴，还是三者组合。

## 推荐产品语义

短期建议采用 “任务金额内扣资源成本”：

```text
net_agent_payout = gross_reward - verified_foundry_resource_cost
```

其中 `verified_foundry_resource_cost` 必须优先来自 Foundry 可验证账本：

- LLM：Foundry LLM gateway / LiteLLM reported cost，带 task metadata。
- Sandbox：Foundry sandbox session finalize 后的 runtime cost。
- Feature/tool：Foundry 托管 feature 侧主动记录的 cost。

agent 自报 usage 可以作为辅助展示或 fallback，但不能作为优先结算依据。需要在记录里标识 `pricing_source = foundry_verified | agent_reported | unpriced`。

长期可以增加不同 billing mode：

- `deduct_from_reward`：从任务奖励内扣资源成本。
- `sponsor_pays_resources`：任务奖励全额给 agent，资源成本由 client 或 Foundry 另付。
- `platform_subsidized`：平台补贴一部分资源成本，只扣除超额部分。

第一阶段先实现 `deduct_from_reward`，避免当前 Stripe 付款路径继续漏算成本。

## 阶段 0：对齐协议与结算口径

目标：先把资源成本、任务收入、净支付的语义固定下来，避免 Foundry 侧和 agent-kit 侧各自实现一套解释。

### ccfoundry-meta / protocol

- 在协议文档中定义 bounty / settlement 的标准字段：
  - `gross_reward_usd`
  - `resource_cost_usd`
  - `net_payout_usd`
  - `billing_mode`
  - `pricing_source`
  - `settlement_items`
- 规定 settlement item 的符号语义：
  - `task_reward` 为正数。
  - `llm_cost`、`sandbox_cost`、`feature_cost` 在扣款模式下为负数。
  - `net_payout` 为最终支付金额，可选作为汇总项。
- 明确 agent 自报 usage 与 Foundry verified usage 的优先级。
- 明确 `task_ref` / `invocation_id` 是资源账本、deliverable、settlement 的共同关联键。

### Foundry 侧

- 确认 public bounty 当前默认 billing mode 为 `deduct_from_reward`。
- 确认当资源成本超过任务金额时的行为：
  - 推荐默认 `net_payout = 0`。
  - settlement metadata 里保留 `unrecovered_resource_cost_usd`。
- 明确是否需要把资源成本计入 human user quota；如果任务奖励来自 client budget，推荐先在 bounty budget 内扣，不再重复记入 human user quota。

### ccfoundry-agent-kit 侧

- 对齐 SDK model 字段命名，避免后续一边叫 `service_fee`，另一边叫 `resource_usage`。
- 保留 `service_fee` 用于 agent 自己的服务费，不把它混同为 Foundry 资源成本。

## 阶段 1：引入任务级 billing context

目标：所有资源账本事件都能精确归因到一次任务执行。

### Foundry 侧

- 在 bounty claim / execution 分配时生成稳定的 `invocation_id`，并保留：
  - `requirement_id`
  - `agent_name`
  - `client/user owner`
  - `billing_mode`
  - `gross_reward_usd`
- 把 `invocation_id` 注入到：
  - external agent pull job payload
  - LLM gateway metadata
  - sandbox session metadata
  - deliverable submit request
  - settlement record metadata
- 扩展 sandbox session 数据模型，至少保存：
  - `agent_name`
  - `requirement_id`
  - `invocation_id`
  - `billing_owner`
  - `billing_mode`
- `agent_usage_ledger.metadata` 中统一写入这些字段，便于后续聚合。

### ccfoundry-agent-kit 侧

- Pull runtime 在调用 bounty handler 时传入 `invocation_id` / `requirement_id` / `billing_context`。
- `FoundrySandboxClient.start()` 支持携带或自动附带 billing context。
- deliverable submit helper 或现有 metadata 中必须带回 `invocation_id`。
- 示例 verilog-module-writer 更新为使用这个 context，而不是只靠 agent name 和最近 sandbox session 推断。

### ccfoundry-meta / protocol

- 在 job payload / bounty execution contract 中声明 `billing_context` schema。
- 规定 agent 必须原样回传 `invocation_id`，不得自行生成替代 ID。

## 阶段 2：让 LLM 与 sandbox 成本进入可验证账本

目标：Foundry 能够在结算前查询到本次任务的 verified resource cost。

### Foundry 侧

- LLM gateway 调用时把 `invocation_id` 写入 provider metadata / LiteLLM metadata，并在 `_record_completion_accounting_event` 中落到 `agent_usage_ledger.metadata`。
- 外部 agent 如果通过 Foundry-provided LLM endpoint 调用模型，必须使用 Foundry 注入的 user / invocation metadata。
- 如果 external agent chat response 或 bounty response 自带 usage/cost，只能作为 `agent_reported` fallback 记录，不能覆盖 gateway verified cost。
- Sandbox start 时绑定 billing context；stop/finalize 时记录 `external_sandbox_runtime`，并把 `invocation_id` / `requirement_id` 写入 ledger metadata。
- 在 deliverable 结算前提供一个内部聚合函数，例如：

```text
get_resource_cost_for_invocation(invocation_id) -> {
  model_cost_usd,
  sandbox_cost_usd,
  feature_cost_usd,
  total_resource_cost_usd,
  entries
}
```

- 对仍在运行的 sandbox 提供一种处理策略：
  - 推荐结算前强制 finalize/stop 对应 invocation 的 sandbox。
  - 如果不能 stop，则计算 pending runtime cost 并标记为 `estimated`，但 Stripe 支付前应尽量避免 estimated cost。

### ccfoundry-agent-kit 侧

- SDK 示例和 sandbox skill 在提交 deliverable 前调用 `sandbox.stop()` 或显式 finalize。
- Sandbox client 暴露 stop 返回的 `runtime_cost` / accounting summary，便于 agent logs 和本地调试。
- 如果 SDK 提供 Foundry LLM helper，应自动携带 billing context。

### ccfoundry-meta / protocol

- 规定 verified cost 的最小字段：
  - `category`
  - `entry_type`
  - `quantity`
  - `unit`
  - `cost`
  - `currency`
  - `pricing_source`
  - `invocation_id`

## 阶段 3：改造 public deliverable settlement

目标：Stripe 支付金额不再等于 gross bounty，而是等于扣除 Foundry 资源成本后的 net payout。

### Foundry 侧

- 在 public deliverable submit 验证通过后，执行统一结算流程：

```text
1. verify deliverable
2. finalize/stop task-scoped sandbox if needed
3. aggregate resource cost by invocation_id
4. calculate net_agent_payout
5. build settlement_items
6. create Stripe payment/transfer for net_agent_payout
7. write bounty_submissions
8. write agent_usage_ledger settlement entry
9. return gross/resource/net breakdown to agent
```

- `bounty_submissions` 继续作为 bounty 业务表，但 settlement truth 应以 `agent_usage_ledger` 的 `settlement` entry 为准。
- `AgentAccountingService.record_settlement` 不应只信任 caller amount；public bounty 路径应由 Foundry 自己根据 verified cost 生成 amount/items。
- Settlement metadata 中至少包含：
  - `gross_reward_usd`
  - `resource_cost_usd`
  - `net_payout_usd`
  - `unrecovered_resource_cost_usd`
  - `billing_mode`
  - `invocation_id`
  - `requirement_id`
  - `resource_entry_ids`
- Bootstrap / earnings / notification 逻辑统一从 ledger settlement 查询，避免 public bounty 已付款但 notification 看不到。

### ccfoundry-agent-kit 侧

- Pull runtime 解析 settlement response 中的 cost breakdown，并写入本地 earnings / logs。
- 示例 agent 的 deliverable submission 不再假设返回金额就是 bounty gross amount。
- Agent Dev Board earnings 面板如果展示 settlement，应同时展示 gross、resource cost、net。

### ccfoundry-meta / protocol

- 更新 deliverable submit response schema，增加：
  - `settlement.gross_reward_usd`
  - `settlement.resource_cost_usd`
  - `settlement.net_payout_usd`
  - `settlement.items`
  - `settlement.pricing_source`

## 阶段 4：SDK 与示例体验补齐

目标：agent 开发者不需要手写结算 glue code，也不容易漏掉 sandbox finalize。

### ccfoundry-agent-kit 侧

- 增加 `BillingContext` / `ResourceUsage` / `SettlementBreakdown` models。
- Pull runtime 将 bounty job payload 中的 billing context 传给 handler。
- `FoundrySandboxClient`：
  - 支持 `start(context=...)`。
  - 支持 `stop(finalize=True)` 并返回标准化 cost summary。
  - 在缺少 context 时给出明确 warning。
- 增加 deliverable submit helper：

```python
await submit_deliverable(
    requirement_id=...,
    invocation_id=...,
    files=...,
    metadata=...,
)
```

- 更新 verilog-module-writer example：
  - 使用 Foundry-provided LLM / sandbox。
  - 在提交 deliverable 前 finalize sandbox。
  - 在日志中打印 gross/resource/net breakdown。
- 更新 SDK README / docs，说明 `service_fee` 与 Foundry resource cost 的区别：
  - `service_fee` 是 agent 自己申报的服务收入。
  - `resource_cost` 是 Foundry 可验证资源成本。

### Foundry 侧

- 不保留 paid settlement 的 legacy fallback：缺少 `invocation_id` 的 bounty deliverable 请求直接拒绝。
- 对没有 finalize sandbox 的 agent，在 settlement 前自动 stop 对应 invocation 的 sandbox，或拒绝结算并返回明确错误。

## 阶段 5：测试、迁移与运营视图

目标：让成本扣除流程可回归、可观察、可审计。

### Foundry 侧测试

- Public deliverable submit：
  - 无资源成本时，`net_payout == gross_reward`。
  - 有 LLM 成本时，`net_payout == gross_reward - llm_cost`。
  - 有 sandbox 成本时，结算前会 finalize sandbox，并扣除 runtime cost。
  - LLM + sandbox 同时存在时，items 明细正确。
  - 资源成本超过 gross reward 时，`net_payout == 0`，并记录 unrecovered cost。
- Ledger：
  - model / compute / settlement entries 共享同一个 `invocation_id`。
  - public bounty 付款后可以从 ledger settlement 查询到记录。
- Stripe：
  - Stripe amount 使用 net payout。
  - gross/resource/net breakdown 不受 Stripe rounding 影响。
- Legacy：
  - 缺少 invocation context 的请求不会静默进入错误结算。

### ccfoundry-agent-kit 侧测试

- Pull runtime 正确传递 billing context。
- Sandbox client start/stop 能携带 context 并解析 cost summary。
- Verilog example 在提交 deliverable 前 finalize sandbox。
- Settlement response 能被 SDK 正确解析和展示。

### 运营与调试

- Foundry admin / dev board 应能按 `invocation_id` 查看：
  - task metadata
  - LLM ledger entries
  - sandbox ledger entries
  - settlement entry
  - Stripe payment/transfer id
- 增加异常视图：
  - 已付款但没有 settlement ledger。
  - 有 resource cost 但没有 settlement。
  - sandbox 未 finalize。
  - cost source 为 `agent_reported` 或 `unpriced`。

## 最小可用实现顺序

如果要最快把当前 verilog flow 修到不会漏算，推荐顺序是：

1. 在 bounty execution / deliverable submit 中加入 `invocation_id`。
2. Sandbox session 绑定 `invocation_id`，stop 时 ledger 写入相同 metadata。
3. verilog-module-writer 在 submit 前强制 stop sandbox。
4. public deliverable submit 查询该 `invocation_id` 的 sandbox cost，计算 net payout。
5. public deliverable submit 写入 ledger settlement。
6. 补 LLM gateway invocation metadata，让 Foundry-provided LLM cost 进入同一 invocation ledger。

不建议长期使用“按 agent name 查询最近一次 sandbox runtime”来扣费。这个方法可以临时演示，但多任务并发或重试时会错误归因。

## 需要重点避免的误区

- 不要把 agent 的 `service_fee` 当成 Foundry resource cost。前者是 agent 收入项，后者是平台成本项。
- 不要让 public bounty 路径绕过 `agent_usage_ledger`，否则 earnings、notification、audit 会继续不一致。
- 不要信任 agent 自报成本作为主结算依据。Foundry 自己提供的资源必须由 Foundry 自己计量。
- 不要在 sandbox finalize 之前进行最终 Stripe 支付，除非该任务明确不使用 sandbox。
- 不要只做总额扣款而不记录 items；没有 items 后续无法解释 agent 为什么少收到钱。
