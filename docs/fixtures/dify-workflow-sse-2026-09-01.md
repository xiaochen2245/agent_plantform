# Dify 真实 SSE 事件样本（2026-09-01 从 192.168.20.226 实抓）

## workflows/run（工作流模式应用，key: app-ciL1...444 已验证有效）

POST /v1/workflows/run  {"inputs":{},"response_mode":"streaming","user":"probe-1"}

```
event: ping

data: {"event":"workflow_started","task_id":"9709f85f-8b2f-43a2-a2df-b36a315cc7b7","workflow_run_id":"9709f85f-8b2f-43a2-a2df-b36a315cc7b7","data":{"id":"9709f85f-8b2f-43a2-a2df-b36a315cc7b7","workflow_id":"ad1ded10-39a8-4847-a45a-c28d83a3b1c4","inputs":{},"created_at":1788231753,"reason":"initial"}}

data: {"event":"workflow_finished","task_id":"9709f85f-8b2f-43a2-a2df-b36a315cc7b7","workflow_run_id":"9709f85f-8b2f-43a2-a2df-b36a315cc7b7","data":{"id":"9709f85f-8b2f-43a2-a2df-b36a315cc7b7","workflow_id":"ad1ded10-39a8-4847-a45a-c28d83a3b1c4","status":"failed","outputs":null,"error":"business_card is required in input form","elapsed_time":0.0,"total_tokens":0,"total_steps":0,"created_by":{},"created_at":1788231753,"finished_at":1788231753,"exceptions_count":1,"files":[]}}
```

## chat-messages（同 key → 模式不符，证明 key 有效但应用非 chat 模式）

```
{"code":"not_chat_app","message":"Please check if your app mode matches the right API route.","status":400}
```

## 工程结论
1. 工作流模式事件词汇表：ping / workflow_started / node_started* / text_chunk* / node_finished* / workflow_finished（带 status/error/total_tokens）
2. 该工作流必填输入 `business_card` → 印证契约缺口：chat/send 需扩展 inputs 透传，AppOut 需补 inputs_schema
3. chat-messages 仅适用 chat/agent-chat 模式应用

---

## chat-messages（聊天模式，key: app-Rykp...jLXua，模型 gpt-5.6-luna，已跑通完整链路）

POST /v1/chat-messages（经我们 FastAPI 代理转发的实测）

```
data: {"event":"message","conversation_id":"60518f34-...","message_id":"bdeff8f9-...","task_id":"6f735421-...","answer":"你好",...}   ← 逐 token 增量
...
data: {"event":"message_end",...,"metadata":{"usage":{"prompt_tokens":4394,"completion_tokens":30,"total_tokens":4424,"total_price":"0.0009148","currency":"USD","latency":7.08},...}}
event: agent_done          ← 我们后端追加的自有事件
data: {}
```

要点：answer 逐字增量；usage 在 message_end.metadata；conversation_id 为 Dify 侧 UUID（我方内部 UUID 另存，经 /api/conversations 查询）。
