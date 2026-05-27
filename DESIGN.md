# DESIGN

## Goal

构建一个 **Hermes 原生 Python 插件**，把最关键的一组运行时工作流护栏收敛成清晰、可测试、可发布的实现，优先保证：

1. hook 对齐准确
2. 状态机简单清晰
3. 每条规则可测试
4. 不引入跨语言桥接屎山

## Design Principles

### 1. Runtime > prompt

所有关键约束优先做在 Hermes hook：

- `pre_tool_call`
- `post_tool_call`
- `transform_tool_result`
- `pre_llm_call`
- `on_session_start`
- `on_session_end`
- `on_session_finalize`

prompt 文案只做轻量提醒，不承担真实执行约束。

### 2. Shared policy, host-native wiring

策略逻辑保持可复用、可测试，但宿主接线必须 Hermes-native：

- 命令检测
- 低信号判定
- 阶段状态迁移
- 结果摘要

这些是共享 policy。

- `register(ctx)`
- Hermes hook 返回格式
- 路径/工具名适配

这些是 Hermes adapter。

### 3. No cross-language bridge in v1

v1 不做 TS runtime + Python shim，不做 node 子进程桥。

原因：

- Hermes 插件入口天然是 Python
- 状态与错误传播在 Python 内最自然
- 开源后用户安装和排障成本最低

### 4. TDD first

每条规则先有失败测试，再写实现。

当前测试覆盖：

- existing file mutation blocked without evidence
- mutation requires verification before next mutation
- repeated low-signal probe gets blocked
- tool result summarization
- stage-aware pre_llm_call context
- session end/finalize cleanup

## Runtime Model

### Session phases

只保留三态：

- `observe`
- `execute`
- `review`

解释：

- `observe`：还没拿到足够证据
- `execute`：已有证据，可做最小改动
- `review`：刚完成改动，必须先验证

### State fields

每个 session 保存：

- `phase`
- `evidence_count`
- `last_evidence_label`
- `pending_verification`
- `last_mutation_label`
- `consecutive_low_signal`
- `last_low_signal_signature`
- `last_low_signal_intent`
- `last_updated_at`

### State lifecycle

- `on_session_start`：确保 session state 初始化
- `post_tool_call`：推进状态机
- `on_session_end` / `on_session_finalize`：清理 session state
- store 达到 TTL 或容量上限时自动 prune

## Hook Contracts

### pre_tool_call

返回：

```python
{"action": "block", "message": "..."}
```

负责：

- 危险命令拦截/审批前判定
- 无证据修改已有文件拦截
- 未验证前继续变更拦截
- 同一低信号 intent 重复探测拦截

### post_tool_call

负责：

- 识别 observation / mutation / validation
- 更新 evidence count
- 设置 `pending_verification`
- 维护 low-signal 连击状态

### transform_tool_result

负责：

- 对超大工具输出做摘要
- 减少上下文污染

### pre_llm_call

负责：

- 注入阶段说明和运行时提醒
- 不直接改 system prompt，只追加上下文文本

## Current v0.01 autonomous harness layer

v0.01 已经完成：

1. 默认 `dangerous_command_action=warn`，高风险命令不进入手动审批流，但会写入审计并注入自验证提醒。
2. 新增 JSONL audit trail，记录 session、tool preflight、dangerous command、tool result、large-output summarization 等事件。
3. 新增 validation suggestion layer，根据触碰文件和命令形态建议最窄验证命令。
4. session state 记录 mutation / validation / dangerous 计数、触碰文件、验证建议、最近验证和最近高风险标签。
5. `pre_llm_call` 注入触碰文件、建议验证、高风险审计提醒和最终证据汇报要求。
6. 当前测试覆盖 hook 注册、危险命令、evidence-before-mutation、verify-after-mutation、低信号阻断、摘要、任务账本、审计日志和 review 状态清理。

## Planned Next Steps

1. 增加 durable task ledger，用于记录任务目标、验收条件、证据、变更、验证和最终状态。
2. 将 `explain_state()` 暴露为正式 Hermes debug tool。
3. 增加 diff / mutation review 摘要，把修改范围纳入最终复核。
4. 增加 compaction 相关快照和恢复锚点。
5. 增加 clean-install / wheel / plugin-dir 安装 smoke test。
6. 在真实 Hermes 实例里做灰度启用和真实任务验证。
