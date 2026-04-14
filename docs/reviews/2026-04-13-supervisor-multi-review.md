# Minimax

  ---                                                                                                                                                                             
  thin-supervisor 代码审查报告                                                                                                                                                    
                                                                                                                                                                                  
  概览                                                                                                                                                                            
                                                                                                                                                                                  
  thin-supervisor 是一个基于 tmux 的 sidecar 监督器，用于管理 AI coding agent（Claude Code、Codex 等）的长时运行工作流。核心架构：                                                
  - Daemon 模式：Unix socket 通信，多 run 并发管理      
  - 状态机：TopState 驱动的 10 状态机                                                                                                                                             
  - 三路 Gate：ContinueGate / BranchGate / FinishGate                                                                       
  - Checkpoint 协议：agent 以 YAML block 形式报告进度                                                                                                                             
                                                                                                                                                                                  
  ---                                                                                                                                                                             
  1. 逻辑推演与漏洞分析                                                                                                                                                           
                                                                                                                                                                                  
  1.1 端到端状态流转                                                                                                        
                                                        
  READY → RUNNING → GATING → (VERIFYING | RUNNING | PAUSED_FOR_HUMAN)
                            ↓                                                                                                                                                     
                        VERIFYING → RUNNING / PAUSED_FOR_HUMAN / COMPLETED                                                                                                        
                                                                                                                                                                                  
  1.2 漏洞列表（按严重程度）                                                                                                                                                      
                                                                                                                            
  P0 - 状态被覆盖（top_state 覆盖bug）                                                                                                                                            
                                                                                                                            
  文件: loop.py:67-80                                                                                                                                                             
                                                                                                                            
  def handle_event(self, state, event):                 
      state.last_event = event                                                                                                                                                    
      if event["type"] == "agent_output":
          cp = event.get("payload", {}).get("checkpoint")                                                                                                                         
          if cp:                                                                                                            
              ...                                       
              state.top_state = TopState.GATING    # ← 任何 checkpoint 都直接覆盖
      elif event["type"] == "agent_ask":                                                                                                                                          
          state.top_state = TopState.GATING
                                                                                                                                                                                  
  问题：当状态机处于 VERIFYING 时，如果 agent 发来新的 checkpoint（比如在验证进行中 agent 又输出了内容），top_state 会被直接覆盖为 GATING，丢失正在进行的验证结果。               
                                                        
  推演：                                                                                                                                                                          
  VERIFYING（正在运行 verifier）                                                                                            
    → agent 又输出了一行日志（含 checkpoint）           
    → handle_event 把 top_state 改为 GATING  
    → 验证结果被丢弃                                                                                                                                                              
    → 进入 gate 逻辑，可能重新触发验证或注入指令
                                                                                                                                                                                  
  同样，agent_ask 也会无条件覆盖状态，如果正在 VERIFYING 时 agent 提问，验证会被中止。                                                                                            
                                                                                                                                                                                  
  修复建议：在 handle_event 中保护 VERIFYING 状态：                                                                                                                               
  if state.top_state not in (TopState.VERIFYING, TopState.GATING):                                                                                                                
      state.top_state = TopState.GATING                                                                                     
                                                                                                                                                                                  
  P0 - workflow_done 走 VERIFY_STEP 而非直接 Finish
                                                                                                                                                                                  
  文件: loop.py:110-118                                                                                                     
                                                                                                                                                                                  
  if cp_status == "workflow_done":                                                                                          
      return SupervisorDecision.make(                   
          decision=DecisionType.VERIFY_STEP.value,   # ← 应该直接 FINISH
          ...                                                                                                                                                                     
      )
                                                                                                                                                                                  
  当 agent 报告 workflow_done 时，系统返回 VERIFY_STEP，然后 apply_verification 会再次验证当前 node，再调用 finish_gate。这对于"已经声称全部完成"的 agent 是多余且危险的——如果    
  verifier 偶然失败（比如 git dirty），workflow_done 反而会被拒绝。
                                                                                                                                                                                  
  对比 finish_gate.py:35-37 的逻辑：                                                                                        
  if spec.kind == "conditional_workflow":               
      required = set(state.done_node_ids)
      required.add(state.current_node_id)                                                                                                                                         
                                         
  如果 workflow_done 是 agent 提前发出的（违反 contract），这里会检查 current_node_id 是否在 done list 里。如果 verifier 因为某些原因还没通过，状态不一致会导致 finish gate       
  拒绝完成。                                                                                                                                                                      
                                                        
  修复建议：workflow_done 应该直接触发 finish_gate.evaluate() 而不是走 VERIFY_STEP。                                                                                              
                                                                                                                            
  P0 - 指令送达无确认（Observation-Only 黑洞）                                                                                                                                    
                                                                                                                            
  文件: loop.py:680-714                                                                                                                                                           
                                                                                                                            
  def _inject_or_pause(self, state, terminal, instruction) -> bool:
      if getattr(terminal, "is_observation_only", False):
          try:                                                                                                                                                                    
              terminal.inject(instruction.content)  # 写文件，不确认送达
          except Exception as exc:                                                                                                                                                
              logger.warning("observation-only inject failed: %s", exc)                                                     
          return True  # ← 永远返回 True，不会 pause    
                                                                                                                                                                                  
  对于 JSONL 观察模式，注入指令只是写文件，没有任何机制确认 agent 是否收到并执行。JsonlObserver 自己的注释也承认了这一点：                                                        
                                                                                                                                                                                  
  ▎ 文件: jsonl_observer.py:105-116                                                                                                                                               
  ▎ "JSONL mode is observation-only; instruction written to file but no hook is wired to deliver it to the agent"           
                                                                                                                                                                                  
  这意味着如果 supervisor 认为 agent 被阻塞了（status: blocked），注入了"继续工作"的指令，但 agent 根本没收到——系统会继续轮询，永远不会知道指令没有被执行。                       
                                                                                                                                                                                  
  P1 - apply_verification 在 FINISH decision 后调用 finish_gate                                                                                                                   
                                                                                                                            
  文件: loop.py:218                                                                                                                                                               
                                                                                                                            
  next_id = spec.next_node_id(state.current_node_id)    
  if next_id is None:
      finish = self.finish_gate.evaluate(spec, state, cwd=cwd)
      if finish["ok"]:                                                                                                                                                            
          state.top_state = TopState.COMPLETED
                                                                                                                                                                                  
  这里的问题是：如果 next_id is None（当前是最后一步）且 finish["ok"] 为 True，则进入 COMPLETED。但如果 finish["ok"] 为 False，会调用 _pause_for_human。然而，apply_verification  
  本身没有检查 state.top_state 是否已经是 FINAL_STATE——如果 apply_decision 已经设置了 COMPLETED/FAILED/ABORTED，apply_verification 仍然会执行并可能覆盖状态。
                                                                                                                                                                                  
  P1 - resume 逻辑中 top_state 匹配但实际不可恢复                                                                                                                                 
                                                        
  文件: daemon/server.py:336                                                                                                                                                      
                                                                                                                            
  if (state_data.get("top_state") in ("PAUSED_FOR_HUMAN", "RUNNING", "READY", "GATING", "VERIFYING")):                                                                            
                                                                                                                                                                                  
  GATING 和 VERIFYING 被列入可恢复状态，但实际上这两个状态意味着"正在等待 agent 响应"。Resume 后重新进入 run_sidecar，如果 terminal/pane 状态不正确，可能会重复注入或丢失         
  in-flight 的 checkpoint。                                                                                                                                                       
                                                                                                                                                                                  
  P1 - _do_ack_review spec_hash 验证在加载 spec 之后                                                                                                                              
                                                        
  文件: daemon/server.py:441-454                                                                                                                                                  
                                                                                                                            
  spec = load_spec(spec_path)        # ← 先加载 spec    
  current_spec_hash = StateStore._hash_spec(spec_path)
  saved_spec_hash = state_data.get("spec_hash", "")                                                                                                                               
  if current_spec_hash and saved_spec_hash and current_spec_hash != saved_spec_hash:
      return {"ok": False, "error": "spec was modified since the run was created..."}                                                                                             
                                                                                                                                                                                  
  先 load_spec() 再检查 hash。如果 spec 有语法错误会在 hash 检查前抛出异常。更严重的是：review ack 后会改变 state.completed_reviews，但如果此时 spec 被篡改过，虽然有 hash        
  检查，但审查者的 ack 实际上是基于新 spec 作出的判断。                                                                                                                           
                                                                                                                                                                                  
  P1 - node_mismatch_count 不跨 auto-intervention 持久化                                                                    
                                                        
  文件: loop.py:354, 519-539                                                                                                                                                      
   
  node_mismatch_count = 0      # 每次 run_sidecar 调用都重置                                                                                                                      
  ...                                                                                                                       
  node_mismatch_count += 1                              
  if node_mismatch_count >= max_node_mismatch:
      pause_payload = self._pause_for_human(...)                                                                                                                                  
      if self._attempt_auto_intervention(...):
          restart_loop = True                                                                                                                                                     
          node_mismatch_count = 0  # 重置了！                                                                               
                                                                                                                                                                                  
  当 auto-intervention 触发后 restart_loop = True，整个内部循环重新开始，node_mismatch_count 回到 0。如果中断后 checkpoint 还是不匹配，需要再积累 5 次才能再次                    
  pause。Auto-intervention 可能注入"重定向回正确 node"的指令，但如果 agent 没有正确响应，这个 mismatch 会持续存在但 counter 被不断重置。                                          
                                                                                                                                                                                  
  P2 - content 字段直接嵌入 state，无任何 sanitization                                                                                                                            
                                                        
  Checkpoint 中的 evidence、candidate_next_actions、question_for_supervisor 等字段直接放入 state.last_agent_checkpoint，这些内容最终会被注入到 prompt 中供 LLM judge 使用。虽然   
  TranscriptAdapter._build_checkpoint 会做 YAML 解析，但没有对内容进行长度限制或格式清理——恶意 agent 可以注入超长字符串干扰 judge prompt。
                                                                                                                                                                                  
  P2 - _run_sidecar_inner run_id 重启后 stale 覆盖                                                                          
                                                        
  文件: loop.py:420-428                                                                                                                                                           
   
  cp = adapter.parse_checkpoint(pending_text, run_id=state.run_id, surface_id=surface_id)                                                                                         
  if cp and cp.run_id and cp.run_id != state.run_id:                                                                        
      cp = None  # stale checkpoint from previous run   
  if cp:                                                                                                                                                                          
      state.last_injected_node_id = state.current_node_id
      state.last_injected_attempt = state.current_attempt                                                                                                                         
                                                                                                                            
  第一次 READY→RUNNING 转换时，如果 pane 中已经有来自当前 run 的 checkpoint，只会跳过第一条指令的注入。但如果 pane 里的 checkpoint 是来自同 run_id 的更早 node（比如 agent 在     
  supervisor 注入指令前已经开始了下一步），run_id 相同所以不会被跳过，只有 current_node 不匹配才会触发 node_mismatch 处理。
                                                                                                                                                                                  
  P2 - is_final(state) 和 PAUSED_FOR_HUMAN 的矛盾                                                                                                                                 
                                                        
  文件: loop.py:332-333, 446                                                                                                                                                      
                                                                                                                            
  def is_final(self, state) -> bool:                                                                                                                                              
      return state.top_state in FINAL_STATES
  # FINAL_STATES = {COMPLETED, FAILED, ABORTED}                                                                                                                                   
  ...                                                                                                                                                                             
  while not self.is_final(state) and state.top_state != TopState.PAUSED_FOR_HUMAN:
                                                                                                                                                                                  
  is_final 不包含 PAUSED_FOR_HUMAN，所以主循环会在 PAUSED_FOR_HUMAN 时继续运行。但 PAUSED_FOR_HUMAN 本身没有退出机制——必须等待外部 resume 命令或人类干预。这在语义上是对的（pause 
  确实应该阻止循环），但 is_final() 的命名有误导性。    
                                                                                                                                                                                  
  ---                                                                                                                       
  2. 状态机实现 Review                                  
                      
  2.1 状态定义
                                                                                                                                                                                  
  TopState:
    INIT → READY → RUNNING → AWAITING_AGENT_EVENT → GATING                                                                                                                        
                              → VERIFYING                                                                                   
                              → PAUSED_FOR_HUMAN                                                                                                                                  
                              → COMPLETED / FAILED / ABORTED
                                                                                                                                                                                  
  问题：                                                                                                                    
  - AWAITING_AGENT_EVENT 状态存在但从未被设置（grep 所有源码只有定义处引用）
  - GATING 是 checkpoint 驱动自动进入的，而不是外部触发的                                                                                                                         
  - FINAL_STATES 不包含 PAUSED_FOR_HUMAN，语义正确但需文档说明
                                                                                                                                                                                  
  2.2 转换完整性                                                                                                                                                                  
                                                                                                                                                                                  
  ┌─────────────────────┬────────────────────────────────────────────────────────┬─────────────────────┐                                                                          
  │        转换         │                        触发条件                        │        问题         │                                                                          
  ├─────────────────────┼────────────────────────────────────────────────────────┼─────────────────────┤                    
  │ READY→RUNNING       │ _run_sidecar_inner 初始注入后                          │ ✓                   │
  ├─────────────────────┼────────────────────────────────────────────────────────┼─────────────────────┤
  │ RUNNING→GATING      │ handle_event 收到 checkpoint                           │ ✓                   │                                                                          
  ├─────────────────────┼────────────────────────────────────────────────────────┼─────────────────────┤
  │ GATING→VERIFYING    │ apply_decision(VERIFY_STEP)                            │ ✓                   │                                                                          
  ├─────────────────────┼────────────────────────────────────────────────────────┼─────────────────────┤                                                                          
  │ GATING→RUNNING      │ apply_decision(CONTINUE)                               │ ✓                   │
  ├─────────────────────┼────────────────────────────────────────────────────────┼─────────────────────┤                                                                          
  │ GATING→PAUSED       │ apply_decision(ESCALATE_TO_HUMAN)                      │ ✓                   │                    
  ├─────────────────────┼────────────────────────────────────────────────────────┼─────────────────────┤                                                                          
  │ VERIFYING→RUNNING   │ apply_verification(ok=True, has next)                  │ ✓                   │
  ├─────────────────────┼────────────────────────────────────────────────────────┼─────────────────────┤                                                                          
  │ VERIFYING→COMPLETED │ apply_verification(ok=True, no next, finish_gate=true) │ ✓                   │                    
  ├─────────────────────┼────────────────────────────────────────────────────────┼─────────────────────┤                                                                          
  │ VERIFYING→PAUSED    │ apply_verification(ok=False, retry exhausted)          │ ✓                   │
  ├─────────────────────┼────────────────────────────────────────────────────────┼─────────────────────┤                                                                          
  │ 任何→ABORTED        │ apply_decision(ABORT)                                  │ ⚠️  没有检查前置状态 │                    
  ├─────────────────────┼────────────────────────────────────────────────────────┼─────────────────────┤                                                                          
  │ 任何→FAILED         │ 异常时设置                                             │ ⚠️  没有明确转换路径 │
  └─────────────────────┴────────────────────────────────────────────────────────┴─────────────────────┘                                                                          
                                                                                                                            
  2.3 鲁棒性问题                                                                                                                                                                  
   
  问题 1：FINAL_STATES 之外的 top_state 值如果被持久化，然后 resume 加载，无法被正确处理。                                                                                        
                                                                                                                            
  问题 2：状态转换没有原子性保证——store.save() 在多个地方被调用，但状态变更和持久化之间没有事务语义。                                                                             
                                                                                                                            
  问题 3：AWAITING_AGENT_EVENT 是死代码，应该删除或实现。                                                                                                                         
                                                                                                                            
  ---                                                                                                                                                                             
  3. Prompt 流程分析                                                                                                        
                                                        
  3.1 架构图

  ┌─────────────────────────────────────────────────────────────┐
  │  Coding Agent (Codex / Claude Code)                        │                                                                                                                  
  │  ┌─────────────────────────────────────────────────────┐   │
  │  │ SKILL.md: 4-stage workflow                         │   │                                                                                                                   
  │  │   Clarify → Plan → Approve → Execute               │   │                                                                                                                   
  │  │                                                     │   │
  │  │ checkpoint protocol:                                │   │                                                                                                                  
  │  │   <checkpoint> status: working|step_done|blocked   │   │                                                                                                                   
  │  │     current_node: <id> ... </checkpoint>          │   │
  │  └─────────────────────────────────────────────────────┘   │                                                                                                                  
  └───────────────────────────┬─────────────────────────────────┘                                                                                                                 
                              │ tmux pane / JSONL transcript
                              ▼                                                                                                                                                   
  ┌─────────────────────────────────────────────────────────────┐                                                           
  │  Supervisor (thin-supervisor)                               │                                                                                                                 
  │                                                             │                                                           
  │  1. TranscriptAdapter.parse_checkpoint() → Checkpoint       │
  │  2. ContinueGate.decide() 或 BranchGate.decide()           │                                                                                                                  
  │     └──→ LLM Judge (continue_or_escalate prompt)           │
  │  3. SupervisorDecision → apply_decision()                  │                                                                                                                  
  │  4. InstructionComposer.build() → HandoffInstruction        │                                                                                                                 
  │  5. terminal.inject(instruction.content)                  │                                                                                                                   
  │                                                             │                                                                                                                 
  │  Prompts involved:                                         │                                                            
  │  - supervisor/llm/prompts/continue_or_escalate.txt (Judge)  │                                                                                                                 
  │  - supervisor/gates/rules.py (classify_text/classify_checkpoint) │
  │  - skills/thin-supervisor/SKILL.md (Agent side)             │                                                                                                                 
  │  - skills/thin-supervisor/references/*.md (Frozen contracts)│                                                           
  └─────────────────────────────────────────────────────────────┘                                                                                                                 
                                                                                                                            
  3.2 Judge Prompt (continue_or_escalate.txt) 分析                                                                                                                                
                                                                                                                            
  收到的上下文:                                                                                                                                                                   
  {                                                                                                                         
    "spec_id": "...", "current_node_id": "...",         
    "last_agent_checkpoint": { status, summary, evidence, needs },
    "done_node_ids": [...],                                                                                                                                                       
    "retry_budget": { per_node, global_limit, used_global }
  }                                                                                                                                                                               
                                                                                                                                                                                  
  问题：                                                
                                                                                                                                                                                  
  1. confidence 校准不清晰：prompt 说 0.9+ 需要"clear signal from checkpoint"，但 ContinueGate 在处理 step_done/workflow_done 时不调用 judge，直接设置                            
  confidence=1.0。这意味着checkpoint-based 决策绕过了 judge 的置信度校准。
  2. ESCALATE_TO_HUMAN 返回的 decision 字符串大小写问题：                                                                                                                         
  # continue_gate.py:74                                                                                                                                                           
  decision=raw.get("decision", "continue").upper(),  # 上层转换为大写
  # 但 raw 来自 judge，可能返回 "escalate_to_human" (snake_case)                                                                                                                  
  2. 如果 judge 返回 "decision": "escalate_to_human"（snake_case），.upper() 变成 "ESCALATE_TO_HUMAN"，这是正确的。但如果 judge 返回 "ESCALATE_TO_HUMAN" (已大写)，.upper()       
  还是大写，最终在 apply_decision 中比较时会加上 .value 转换——但 DecisionType.ESCALATE_TO_HUMAN.value 是 "ESCALATE_TO_HUMAN"，所以这里有隐式耦合。                                
  3. Judge prompt 没有告知当前是第几次 attempt：retry_budget 信息给了，但没有明确"这是第 2 次尝试"这样的语义。                                                                    
                                                                                                                                                                                  
  3.3 Agent SKILL.md 分析                                                                                                   
                                                                                                                                                                                  
  协议复杂度：SKILL.md 定义了完整的 4-stage workflow，但 checkpoint 协议是"软性"的——它依赖于 agent 的自觉遵守。Supervisor 无法验证：                                              
  - Agent 是否真的在 current_node 上工作                
  - Agent 是否遵循了"不要跳跃 node"的规则                                                                                                                                         
  - workflow_done 是否真的意味着所有步骤都完成了                                                                            
                                                                                                                                                                                  
  证据问题：
  # SKILL.md checkpoint 格式                                                                                                                                                      
  evidence:                                                                                                                 
    - modified: <file path>                                                                                                                                                       
    - ran: <command>                                                                                                        
    - result: <short result>                            
                            
  但 FinishGate 检查 required_evidence 时只是简单的字符串包含检查：                                                                                                               
  # finish_gate.py:95-97                                                                                                                                                          
  for req in contract.required_evidence:                                                                                                                                          
      if req.lower() not in evidence_text:                                                                                                                                        
          failures.append(f"missing required evidence: {req}")                                                              
                                                              
  Agent 可以注入假的 evidence 来绕过 finish gate。例如 spec 要求 evidence 包含 pytest -q，agent 可以输出：                                                                        
  evidence:                                                                                                                                                                       
    - ran: pytest -q                                                                                                                                                              
      result: passed                                                                                                                                                              
  但实际上从未运行。如果 verifier 没有单独验证这一点，finish gate 会错误通过。                                              
                                                                                                                                                                                  
  3.4 Prompt 不一致问题                                                                                                                                                           
                                                                                                                                                                                  
  不一致 1：SKILL.md 说 step_done 是"当前步骤完成，准备验证"，但 continue_or_escalate.txt 的 policy 说：                                                                          
  ▎ "VERIFY_STEP — agent claims step is done, run verification"                                                             
                                                                                                                                                                                  
  然而 ContinueGate 对 step_done 直接返回 VERIFY_STEP，完全跳过了 judge。这意味着 judge prompt 中的 policy 描述不适用于实际路径。这是好的设计（减少 LLM
  调用），但文档和实现不一致会造成维护困难。                                                                                                                                      
                                                                                                                            
  不一致 2：references/escalation-rules.md 说：                                                                                                                                   
  ▎ "If the agent emits status: blocked, escalate immediately — don't wait for retry."                                      
                                                                                      
  但 interventions.py:32-44 的 auto-intervention 会在收到 status: blocked 时尝试自动恢复（resume_with_instruction），这与 escalation-rules.md 的指导矛盾。
                                                                                                                                                                                  
  3.5 Prompt 注入风险                                                                                                                                                             
                                                                                                                                                                                  
  最高风险：InstructionComposer.build() 第 36-40 行：                                                                                                                             
  next_inst = state.last_decision.get("next_instruction")                                                                   
  if next_inst and next_inst != node.objective:          
      generic = ["Continue with the highest-priority", "Do not ask the user"]                                                                                                     
      if trigger_type == "continue" or not any(p in next_inst for p in generic):
          parts.append(next_inst)                                                                                                                                                 
                                                                                                                                                                                  
  state.last_decision 来自 state.last_decision（也就是上一个 gate decision），其 next_instruction 字段来自 LLM judge 的输出。虽然 judge 是受控的，但如果 judge prompt
  被污染或返回异常格式，可能注入非预期指令。                                                                                                                                      
                                                                                                                            
  ---                                                                                                                                                                             
  4. 总结建议                                                                                                               
                                                        
  高优先级修复

  1. 修复 handle_event 状态覆盖 bug（P0）：保护 VERIFYING 和 GATING 状态不被新 checkpoint 覆盖                                                                                    
  2. workflow_done 走专用 Finish 路径（P0）：不要经过 VERIFY_STEP
  3. 增强 JSONL 模式的送达确认（P0）：否则 observation-only 模式下指令送不到 agent                                                                                                
  4. 移除或实现 AWAITING_AGENT_EVENT（P2）：死代码需要清理                                                                                                                        
                                                                                                                                                                                  
  中优先级改进                                                                                                                                                                    
                                                                                                                                                                                  
  5. FINAL_STATES 包含 PAUSED_FOR_HUMAN 或重命名 is_final()                                                                                                                       
  6. ack_review 先验证 hash 再加载 spec                 
  7. resume 不恢复 GATING/VERIFYING 状态（或明确警告）                                                                                                                            
  8. node_mismatch_count 在 auto-intervention 后不丢失积累值                                                                                                                      
                                                                                                                                                                                  
  低优先级（文档/清理）                                                                                                                                                           
                                                                                                                            
  9. references/escalation-rules.md 与 interventions.py 的 auto-recovery 策略冲突需要文档化                                                                                       
  10. Judge prompt 与实际 ContinueGate 路径的不一致需要同步文档                                                             
                                                                 
  # Amp

Thin-Supervisor 深度 Review 报告

一、逻辑推演与漏洞分析

🔴 CRITICAL-1: FINISH 决策绕过 FinishGate

位置: loop.py L205-207

apply_decision 中 FINISH 分支直接设 state.top_state = TopState.COMPLETED，完全不经过 FinishGate.evaluate()。这意味着如果 LLM Judge 返回 FINISH（无论是误判还是被恶意诱导），所有验收合约（must_review_by, require_clean_or_committed_repo, forbidden_states）都会被跳过。

相比之下，正常路径（VERIFY_STEP → apply_verification → finish_gate.evaluate）是安全的。但 LLM Judge 的 prompt 里明确列出了 FINISH 作为可选决策，形成了绕过口。

🔴 HIGH-2: Checkpoint Sequence Reset 逻辑有缺陷

位置: loop.py L474-478

if state.checkpoint_seq - checkpoint.checkpoint_seq < 100:
    continue  # 拒绝 checkpoint

当 agent 崩溃重启后从 seq=1 重新计数，如果 state.checkpoint_seq 是 50，差值是 49 < 100，那么 所有新 checkpoint 都会被永久丢弃，直到 agent 盲目发出 50+ 个 checkpoint。正确做法应该是检查 checkpoint_seq == 1 时无条件接受。

🔴 HIGH-3: Node Mismatch 计数 + consume_checkpoint 竞争

位置: loop.py L518-540 + L655-659

当 checkpoint 因为 node mismatch 被 continue 跳过时，循环末尾的 terminal.consume_checkpoint() 仍会执行，把跳过的 checkpoint 文本从缓冲区清除。下次读取时 checkpoint 已不存在，node_mismatch_count 回到 1，永远达不到 max_node_mismatch=5 的阈值，造成无限静默 hang。

🟡 MEDIUM-4: Auto-Intervention 不重置 retry budget

位置: interventions.py L62-74

"retry budget exhausted" 触发 auto-recovery 后，state.top_state 被设回 RUNNING，但 current_attempt 和 used_global 没有重置。agent 下次失败立刻又超预算，形成 pause → auto-intervene → fail → pause → auto-intervene → fail → 最终达到 max_auto_interventions 停止。虽然最终能停下来，但中间浪费了 agent 执行次数。

🟡 MEDIUM-5: resume 时不重置 auto_intervention_coun
位置: server.py L373-380

人类 resume 后 auto_intervention_count 保留原值。如果之前已经用完 2 次自动恢复配额，即使人类手动 resume 后再次出问题，也无法自动恢复。

---

二、状态机实现 Review

🟡 MEDIUM-6: 死状态 — AWAITING_AGENT_EVENT, INIT, NodeStatus 均未使用

位置: enums.py

TopState.INIT 和 TopState.AWAITING_AGENT_EVENT 在整个 loop 中从未作为转换目标。NodeStatus 枚举虽然作为 SupervisorState 字段存在，但 loop 中从未读取或修改它。这是过度抽象——增加了 schema 复杂性但没有功能价值。

🔴 HIGH-7: _do_ack_review 的 TOCTOU 竞争

位置: server.py L394-473

获取锁检查线程是否活跃 → 释放锁
在无锁状态下读取 state.json → 修改 → 写回

在步骤 1 和 2 之间，另一个 IPC 调用（如 resume）可以启动 worker 线程，开始并发修改 state.json。两者的写入会互相覆盖。

🟡 MEDIUM-8: resume 匹配逻辑过于宽松

位置: server.py L336

resume 匹配条件包含 RUNNING, GATING, VERIFYING 状态。这些通常表示 worker 线程意外退出（crash），但扫描到这些状态的 state.json 时，daemon 不能确认 worker 是否还在另一个 daemon 实例中运行。虽然有 pane lock 保护，但如果 lock 文件意外残留，可能导致 resume 被拒绝。

---

三、Prompt 流程分析

🔴 HIGH-9: InstructionComposer 的 checkpoint 模板与 parser 不兼
位置: composer.py L66-74 vs transcript_adapter.py L58-84

_checkpoint_protocol_suffix 生成单行格式：
<checkpoint> status: working | blocked | step_done | workflow_done current_node: step_1 summary: ... </checkpoint>

但 TranscriptAdapter._parse_lines 按 换行分割，每行检查 line.startswith("status:")。如果 agent 精确复制了注入的单行格式，parser 会把整个 status: working | blocked ... current_node: step_1 ... 解析为 status 字段值，导致 current_node 缺失，返回 None。

好在大多数 LLM 都会将模板重新格式化为多行输出（因为 SKILL.md 给了多行示例），但这是一个脆弱的隐含依赖。

🔴 HIGH-10: classify_checkpoint 遗漏 summary 和 evidence 字段

位置: rules.py L53-59

规则引擎只拼接 status, needs, question_for_supervisor 三个字段做文本分类。如果 agent 在 summary 中写了 "waiting for user credentials"，或在 evidence 中提到 "delete production database"，规则引擎完全看不到，不会触发 BLOCKED 或 DANGEROUS_ACTION 升级。

🟡 MEDIUM-11: LLM Judge prompt 允许 FINISH 但 gate() 从不路由到它

位置: continue_or_escalate.txt L19 vs loop.py L82-119

gate() 方法在非 step_done/workflow_done/blocked 状态时才调用 continue_gate.decide()，而 continue_gate 将 LLM 返回的任何 decision 原样传回。如果 LLM 返回 FINISH，它会通过 apply_decision 直接完成 run（见 CRITICAL-1）。这与 prompt 中 "rare; usually driven by verifier" 的描述矛盾——它本不应出现在 continue gate 的选项中。

🟡 MEDIUM-12: SKILL.md 的 checkpoint 模板含 run_id 和 checkpoint_seq，但 checkpoint_protocol.txt 说不需要

SKILL.md 模板包含 run_id: 和 checkpoint_seq: 行，但 checkpoint_protocol.txt 说 "You do NOT need to include run_id or checkpoint_seq"。这是一致的设计（agent 可选），但实际上如果 agent 不提供 checkpoint_seq，则 seq-based dedup 完全不生效，全靠 content-based dedup，这会在 agent 反复发相同 summary 时造成丢弃问题。

🟡 MEDIUM-13: BLOCKED pattern 假阳性风险

位置: rules.py L28-34

r"waiting for" 模式会匹配合法上下文如 "waiting for test to finish" 或 "waiting for build to complete"，导致 agent 正常工作时被误判为 BLOCKED 并升级到人类。

---
总结优先级

#
严重度
问题
推荐修复
1
🔴 Critical
FINISH 绕过 FinishGate
apply_decision(FINISH) 改为调用 finish_gate.evaluate()
2
🔴 High
Seq reset 丢弃 checkpoint
checkpoint_seq == 1 时无条件接受
3
🔴 High
consume_checkpoint 清除 mismatch 数据
仅在 checkpoint 被完整处理后才 consume
7
🔴 High
ack_review TOCTOU 竞争
整个 read-modify-write 加锁
9
🔴 High
Composer 模板与 parser 格式不兼容
用多行 \n 模板替换单行字符串
10
🔴 High
classify_checkpoint 遗漏字段
拼接时包含 summary 和 evidence
4-5
🟡 Medium
Auto-intervention 不重置计数器
resume 时重置 auto_intervention_count
11
🟡 Medium
LLM prompt 暴露 FINISH 路径
从 continue prompt 中移除 FINISH 选项

# Glm-5.1
项目全面 Review                                                                                 
                                                                                                                                                                                 
 一、逻辑推演与漏洞分析
                                                                                                                                                                                 
 1.1 Checkpoint 注入模板不可解析（严重）                                                           
                                                       
 composer.py:66-74 的 _checkpoint_protocol_suffix 生成的模板：

 <checkpoint> status: working | blocked | step_done | workflow_done current_node: step_1 summary: <one-line description> evidence: ... candidate_next_actions: ... needs: - none
 question_for_supervisor: - none </checkpoint>

 这是一个单行格式，但 TranscriptAdapter._build_checkpoint 先尝试 yaml.safe_load()，失败后走 _parse_lines()。_parse_lines 依赖 splitlines() + startswith("status:")
 做逐行解析。单行内容没有换行，所有字段都无法解析，checkpoint 会被静默丢弃（返回 None）。

 后果：如果 Agent（特别是 Codex）遵循 supervisor 注入的模板字面量输出 checkpoint，supervisor 将永远看不到进度，陷入无限轮询。

 SKILL.md 中的模板是多行 YAML 格式，可以正常解析。但 supervisor 注入的指令模板和 SKILL.md 的格式不一致，Agent 可能遵循任何一个。

 1.2 deferred_continue_instruction 跨 Checkpoint 竞态（中等）

 loop.py:594-621，当一个 poll 周期内读到多个 checkpoint 时：

 Checkpoint 1 (status=working) → CONTINUE with guidance → deferred_continue_instruction = instruction → continue
 Checkpoint 2 (status=step_done) → VERIFY_STEP → verify → pass → state.current_node_id = next_id

 for 循环结束后，loop.py:638-653 检查 deferred instruction 并注入。但此时 state.current_node_id 已经前进到了下一个 node。注入的 instruction 内容引用的是旧 node（"Stay on
 current_node: step_1"），但 agent 已经在 step_2 上了。

 后果：Agent 收到矛盾的指令——supervisor 说 stay on step_1，但实际已在 step_2。可能导致 node mismatch 连锁反应。

 1.3 Agent 无活动超时缺失（中等）

 loop.py:446-464 的 while 循环中，如果没有 checkpoint 解析出来，只是 time.sleep(poll_interval) 然后 continue。没有机制检测 "agent 已经沉默了 N 分钟"。

 虽然 handle_event 处理 "timeout" 事件类型，但 sidecar loop 自身从不产生 timeout 事件。如果 agent 进程卡死或崩溃但 tmux session 仍存在，supervisor 会无限轮询下去。

 1.4 Resume 的 Spec Hash 检查有缺口

 daemon/server.py:338-347：
 if current_spec_hash and saved_spec_hash and current_spec_hash != saved_spec_hash:

 如果 spec_hash 为空（旧版本创建的 state，或 spec 文件被移动），检查被跳过。这意味着 resume 可能运行在已修改的 spec 上，导致 node id 不匹配或其他不一致。

 1.5 Observation-Only Surface 的 Node Rebinding 风险

 loop.py:493-517，当 observation-only surface 的 checkpoint 报告的 node 和 state 不一致时：

 checkpoint.current_node = state.current_node_id  # 强制覆盖

 这是无条件覆盖。如果 agent 确实已经完成了当前 node 并在下一个 node 上工作（supervisor 只是没看到 step_done），强制覆盖会丢失 agent 的真实进度。

 1.6 Injection Confirmation 的误判

 adapter.py:190-213 的 _confirm_injection 机制：

 1. 按 Enter 提交
 2. 轮询 10 次看 tail 是否还在

 如果 agent 的输出中恰好包含注入文本的片段（比如 agent 在日志中引用了被注入的指令），会被误判为 "stuck"。特别是 _tail_looks_stuck 检查 markers[0] in
 joined_tail——如果注入文本的前 12 个词出现在 agent 输出中，就会误判。

 1.7 Seq-based 去重的边界条件

 loop.py:474-477：
 if checkpoint.checkpoint_seq <= state.checkpoint_seq:
     if state.checkpoint_seq - checkpoint.checkpoint_seq < 100:
         continue

 gap 恰好等于 100 时不会被跳过（< 100），gap 为 99 时被跳过。这意味着 seq 从 106 跳到 5（gap=101）会被放行，但 seq 从 106 跳到
 7（gap=99）会被丢弃。这是一个硬编码的魔数，没有配置化，且语义不清晰。

 ---
 二、状态机实现 Review

 2.1 没有状态转移校验（严重）

 apply_decision() 中直接赋值 state.top_state = TopState.RUNNING，没有任何前置状态检查。理论上：

 - COMPLETED → RUNNING（finish 后又 continue）——不会被 while 条件阻止
 - FAILED → VERIFYING（失败后突然进入验证）——同上
 - 任何非法转移都可能发生

 当前代码之所以能工作，是因为决策逻辑本身不会产生非法转移。但这是一个隐式约束，不是显式保障。任何未来的 bug 都可能导致非法转移。

 建议：添加状态转移表，在 apply_decision 开头校验合法性。

 2.2 AWAITING_AGENT_EVENT 和 INIT 是死状态

 TopState 枚举定义了 INIT 和 AWAITING_AGENT_EVENT，但：
 - INIT 从未被赋值（load_or_init 直接创建 READY）
 - AWAITING_AGENT_EVENT 从未被任何代码设置
 - while 循环的条件不检查这两个状态

 这些是未实现的预留状态，增加了理解成本但无实际作用。

 2.3 NodeStatus 完全未使用

 SupervisorState.node_status 字段（类型 NodeStatus）在 load_or_init 中初始化为 CURRENT_STEP_PENDING，但在整个 loop.py 中从未被更新。

 NodeStatus 定义了 7 个值（CURRENT_STEP_PENDING, CURRENT_STEP_RUNNING, CURRENT_STEP_BLOCKED, CURRENT_STEP_DONE, BRANCH_DECISION_PENDING, RETRY_PENDING,
 ROLLUP_VERIFY_PENDING），但只有第一个被使用过。

 2.4 PAUSED_FOR_HUMAN 的退出路径不统一

 进入 PAUSED_FOR_HUMAN 的路径有多条（通过 _pause_for_human），但退出路径只有：
 1. Daemon _do_resume() 设置 RUNNING
 2. _attempt_auto_intervention() 设置 RUNNING

 但 _attempt_auto_intervention 在某些路径上被调用两次（比如 loop.py:529-539 先调 auto_intervention，再检查结果），在某些路径上只调用一次。PAUSED_FOR_HUMAN → auto_intervention →
 RUNNING 的恢复路径，没有重置 human_escalations 列表，可能导致 escalation 历史膨胀。

 2.5 State Store 的 _session_seq 非线程安全

 state_store.py:102-103：
 def append_session_event(self, run_id, event_type, payload):
     self._session_seq += 1

 _session_seq 是普通 int，没有锁保护。如果 daemon 中的线程同时写 event（虽然当前每个 run 是单线程），seq 可能重复。

 2.6 状态持久化窗口

 状态保存在以下时机：
 - Init inject 后
 - 每次注入前（loop.py:625-626）
 - while 循环底部（loop.py:654）
 - 异常时（loop.py:391）

 但在 apply_decision() 和 apply_verification() 之间没有 save。如果进程在 apply_decision(VERIFY_STEP) 后、verify_current_node() 前崩溃，恢复时会重新进入 GATING（因为上一次 save
 是 while 循环底部的），checkpoint 会被重新解析，gate 重新触发。这实际上是安全的（幂等），但依赖隐式的幂等性。

 ---
 三、Prompt 流程分析

 3.1 两端 Checkpoint 协议不一致（严重）

 ┌────────────────┬───────────────────────┬────────────────────────┐
 │      维度      │ SKILL.md（Agent 端）  │ composer.py 注入的模板 │
 ├────────────────┼───────────────────────┼────────────────────────┤
 │ 格式           │ 多行 YAML             │ 单行文本               │
 ├────────────────┼───────────────────────┼────────────────────────┤
 │ run_id         │ 要求填入              │ 说不需要               │
 ├────────────────┼───────────────────────┼────────────────────────┤
 │ checkpoint_seq │ 要求递增              │ 说不需要               │
 ├────────────────┼───────────────────────┼────────────────────────┤
 │ 可解析性       │ YAML safe_load 可解析 │ 无法解析               │
 └────────────────┴───────────────────────┴────────────────────────┘

 checkpoint_protocol.txt（独立文件）说："You do NOT need to include run_id or checkpoint_seq — the supervisor fills these automatically."

 但 SKILL.md 的模板要求两者都填。如果 Agent 遵循 checkpoint_protocol.txt 而非 SKILL.md，seq-based 去重失效（seq 永远为 0），依赖 content-based 去重。

 核心问题：三个来源（SKILL.md、checkpoint_protocol.txt、composer 注入模板）给出了三种不同的 checkpoint 格式指引。

 3.2 Continue Gate 的 SOFT_CONFIRMATION 处理

 rules.py 的中文 pattern 列表：
 r"要不要我继续", r"如果你同意", r"接下来我可以", r"是否继续"

 这些 pattern 匹配的是 Agent 向 supervisor 发出的"软确认"请求。ContinueGate 会返回 CONTINUE + next_instruction "Continue with the highest-priority remaining action. Do not ask
 the user for confirmation..."

 但在 composer.py:38-40：
 generic = ["Continue with the highest-priority", "Do not ask the user"]
 if trigger_type == "continue" or not any(p in next_inst for p in generic):
     parts.append(next_inst)

 当 trigger_type != "continue" 时，这些 generic 指导会被过滤掉。如果 SOFT_CONFIRMATION 发生在 retry 或 node_advance 场景下，Agent 的"要不要我继续"可能得不到明确回复。

 3.3 LLM Judge 的 JSON 解析鲁棒性

 continue_or_escalate.txt 要求 "Return JSON only"，但 ContinueGate.decide() 的 fallback：
 if not isinstance(raw, dict):
     raw = {"decision": "continue", ...}

 如果 Judge 返回的 JSON 被包裹在 markdown code block（LLM 常见行为）中，json.loads() 会失败。需要检查 JudgeClient 是否有 strip 逻辑。

 3.4 Agent 收到指令后的行为约束不足

 SKILL.md 的 contract 说 "Do NOT ask 'should I continue?'"，但这只是 soft 约束。如果 Agent 忽略这个规则：
 1. Agent 发出 "should I continue?" → ContinueGate 触发 SOFT_CONFIRMATION → CONTINUE
 2. Supervisor 注入 "Do not ask the user for confirmation"
 3. Agent 再次发出同样的问题 → 循环

 没有硬机制防止这个循环。Content-based 去重会过滤相同的 checkpoint，但如果 Agent 稍微修改了措辞，就会绕过。

 3.5 Codex SKILL 与 Claude Code SKILL 的差异

 thin-supervisor-codex/SKILL.md 是简化版本，没有：
 - Clarify loop 的 "ONE question per round" 规则
 - "contract confirmation pass" 步骤
 - Spec 的 verification types 表格

 如果 Codex 使用简化版 SKILL，生成的 spec 可能缺少 verify 配置，导致 verification 总是 pass（VerifierSuite.run 遍历空列表 → all([]) → True）。

 3.6 Finish Gate 的 Evidence 匹配过于简单

 finish_gate.py:84-97：
 evidence_text = " ".join(evidence_strings).lower()
 for req in contract.required_evidence:
     if req.lower() not in evidence_text:

 这是 substring 匹配。"test" 会匹配 "integration test" 和 "test_failing"。对于需要精确匹配的场景（如要求 "all tests pass" 而不是 "test"），这会导致误判。

 ---
 汇总：按严重度排序

 ┌────────┬─────────────────────────────────────────────────────────┐
 │ 严重度 │                          问题                           │
 ├────────┼─────────────────────────────────────────────────────────┤
 │ 严重   │ Checkpoint 注入模板不可解析（1.1）                      │
 ├────────┼─────────────────────────────────────────────────────────┤
 │ 严重   │ 状态转移无校验（2.1）                                   │
 ├────────┼─────────────────────────────────────────────────────────┤
 │ 严重   │ 三端 Checkpoint 协议不一致（3.1）                       │
 ├────────┼─────────────────────────────────────────────────────────┤
 │ 中等   │ deferred instruction 跨 checkpoint 竞态（1.2）          │
 ├────────┼─────────────────────────────────────────────────────────┤
 │ 中等   │ Agent 无活动超时缺失（1.3）                             │
 ├────────┼─────────────────────────────────────────────────────────┤
 │ 中等   │ Observation-only node rebinding 风险（1.5）             │
 ├────────┼─────────────────────────────────────────────────────────┤
 │ 中等   │ Injection confirmation 误判（1.6）                      │
 ├────────┼─────────────────────────────────────────────────────────┤
 │ 低     │ Resume spec hash 检查缺口（1.4）                        │
 ├────────┼─────────────────────────────────────────────────────────┤
 │ 低     │ Seq-based 去重魔数（1.7）                               │
 ├────────┼─────────────────────────────────────────────────────────┤
 │ 低     │ NodeStatus/INIT/AWAITING_AGENT_EVENT 未使用（2.2, 2.3） │
 ├────────┼─────────────────────────────────────────────────────────┤
 │ 低     │ _session_seq 非线程安全（2.5）                          │
 ├────────┼─────────────────────────────────────────────────────────┤
 │ 低     │ SOFT_CONFIRMATION generic 过滤（3.2）                   │
 ├────────┼─────────────────────────────────────────────────────────┤
 │ 低     │ Codex SKILL 缺少 verify 配置指引（3.5）                 │
 ├────────┼─────────────────────────────────────────────────────────┤
 │ 低     │ Finish gate evidence substring 匹配（3.6）              │
 └────────┴─────────────────────────────────────────────────────────┘
