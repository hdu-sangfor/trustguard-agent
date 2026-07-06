# Tests 结构说明

本项目是多服务 monorepo，多个 Python 服务都使用顶层包名 `app`。测试目录按“测试类型 + 服务/领域”组织，公共导入隔离逻辑集中放在 `tests/support/`。

## 运行方式

```bash
pytest -c tests/pytest.ini -q -m "not integration and not smoke"
```

需要外部服务的测试通过 marker 区分：

- `integration`：依赖本地 Gateway/MySQL 等服务。
- `smoke`：依赖本机 Orchestrator/Executor 可达。
- `e2e`：依赖靶场目标和 `E2E_TARGET_URL` 等环境变量。

## 目录约定

- `tests/support/`：公共测试基础设施，不放业务断言。
- `tests/contracts/`：跨服务契约和配置契约，防止服务边界漂移。
- `tests/unit/<service-or-domain>/`：单服务或单领域的快速单元测试。
- `tests/integration/`、`tests/smoke/`、`tests/e2e/`：依赖外部运行环境的测试。
- `tests/snapshots/`：契约测试快照数据。

## 文件清单

- `tests/__init__.py`：使 tests 目录可作为 Python 包导入。
- `tests/conftest.py`：pytest 全局 fixture 和 app 包导入路由。
- `tests/contracts/test_execution_context_allowlist_sync_unit.py`：契约测试：execution context allowlist sync。
- `tests/contracts/test_execution_kind_contract_unit.py`：契约测试：execution kind contract。
- `tests/contracts/test_kb_feature_policy_unit.py`：契约测试：kb feature policy。
- `tests/contracts/test_r4f_e_dispatch_payload_contract_unit.py`：契约测试：r4f e dispatch payload contract。
- `tests/contracts/test_tools_registry_contract_unit.py`：契约测试：tools registry contract。
- `tests/contracts/test_tools_registry_phase_unit.py`：契约测试：tools registry phase。
- `tests/contracts/test_v1_mq_lane_execution_plane_key_parity_unit.py`：契约测试：v1 mq lane execution plane key parity。
- `tests/e2e/test_e2e_target_verification.py`：E2E 测试：e2e target verification。
- `tests/executor_test_env.py`：兼容入口，转发到 tests/support/executor_app.py。
- `tests/integration/test_integration_api.py`：集成测试：integration api。
- `tests/orchestrator_test_env.py`：兼容入口，转发到 tests/support/orchestrator_app.py。
- `tests/paths.py`：兼容入口，转发到 tests/support/paths.py。
- `tests/pytest.ini`：pytest 收集范围和 marker 定义。
- `tests/smoke/context_governance_smoketest.py`：冒烟测试：context governance smoketest。
- `tests/smoke/test_smoke_orchestrator_executor.py`：冒烟测试：smoke orchestrator executor。
- `tests/snapshots/r4f_e_executor_payload_v1.json`：契约测试使用的固定快照数据。
- `tests/support/__init__.py`：测试辅助包标记文件。
- `tests/support/executor_app.py`：executor 顶层 app 包导入隔离工具。
- `tests/support/orchestrator_app.py`：orchestrator 顶层 app 包导入隔离工具。
- `tests/support/paths.py`：仓库根目录和各服务根目录常量。
- `tests/unit/dev_mq/test_mq_agent_daemon_root_cli_unit.py`：单元测试（dev_mq）：mq agent daemon root cli。
- `tests/unit/dev_mq/test_mq_worker_root_cli_unit.py`：单元测试（dev_mq）：mq worker root cli。
- `tests/unit/evidence/test_evidence_store_unit.py`：单元测试（evidence）：evidence store。
- `tests/unit/executor/api/test_execute_impl_sniff_pool_unit.py`：单元测试（executor/api）：execute impl sniff pool。
- `tests/unit/executor/api/test_execution_kind_phase_e_unit.py`：单元测试（executor/api）：execution kind phase e。
- `tests/unit/executor/api/test_executor_health_v1_execution_plane_unit.py`：单元测试（executor/api）：executor health v1 execution plane。
- `tests/unit/executor/api/test_executor_katana_workspace_recovery_unit.py`：单元测试（executor/api）：executor katana workspace recovery。
- `tests/unit/executor/core/test_execution_store_finish_v1_unit.py`：单元测试（executor/core）：execution store finish v1。
- `tests/unit/executor/core/test_execution_store_terminal_unit.py`：单元测试（executor/core）：execution store terminal。
- `tests/unit/executor/core/test_workspace_store_wsref_unit.py`：单元测试（executor/core）：workspace store wsref。
- `tests/unit/executor/micro_executor/test_micro_executor_outputs_unit.py`：单元测试（executor/micro_executor）：micro executor outputs。
- `tests/unit/executor/micro_executor/test_micro_executor_protocol_unit.py`：单元测试（executor/micro_executor）：micro executor protocol。
- `tests/unit/executor/micro_executor/test_micro_executor_target_scope_unit.py`：单元测试（executor/micro_executor）：micro executor target scope。
- `tests/unit/executor/mq/test_mq_agent_daemon_argv_unit.py`：单元测试（executor/mq）：mq agent daemon argv。
- `tests/unit/executor/mq/test_mq_agent_daemon_unit.py`：单元测试（executor/mq）：mq agent daemon。
- `tests/unit/executor/mq/test_r4g_c_executor_mq_worker_e2e_lite_unit.py`：单元测试（executor/mq）：r4g c executor mq worker e2e lite。
- `tests/unit/executor/skills/test_dynamic_loader_runpy_priority_unit.py`：单元测试（executor/skills）：dynamic loader runpy priority。
- `tests/unit/executor/skills/test_external_script_skill_runpy_container_unit.py`：单元测试（executor/skills）：external script skill runpy container。
- `tests/unit/executor/skills/test_skill_chain_refactor_unit.py`：单元测试（executor/skills）：skill chain refactor。
- `tests/unit/executor/worker_daemon/test_worker_daemon_managed_session_unit.py`：单元测试（executor/worker_daemon）：worker daemon managed session。
- `tests/unit/executor/worker_daemon/test_worker_daemon_proc_group_unit.py`：单元测试（executor/worker_daemon）：worker daemon proc group。
- `tests/unit/executor/worker_daemon/test_worker_daemon_sniff_pool_unit.py`：单元测试（executor/worker_daemon）：worker daemon sniff pool。
- `tests/unit/orchestrator/api/test_orchestrator_sli_unit.py`：单元测试（orchestrator/api）：orchestrator sli。
- `tests/unit/orchestrator/api/test_orchestrator_trace_r7a_unit.py`：单元测试（orchestrator/api）：orchestrator trace r7a。
- `tests/unit/orchestrator/api/test_orchestrator_trace_r7b_unit.py`：单元测试（orchestrator/api）：orchestrator trace r7b。
- `tests/unit/orchestrator/api/test_orchestrator_trace_stream_unit.py`：单元测试（orchestrator/api）：orchestrator trace stream。
- `tests/unit/orchestrator/api/test_v1_agent_registry_unit.py`：单元测试（orchestrator/api）：v1 agent registry。
- `tests/unit/orchestrator/api/test_v1_health_overview_api_unit.py`：单元测试（orchestrator/api）：v1 health overview api。
- `tests/unit/orchestrator/api/test_v1_health_registry_unit.py`：单元测试（orchestrator/api）：v1 health registry。
- `tests/unit/orchestrator/api/test_v1_kb_federation_observe_api_unit.py`：单元测试（orchestrator/api）：v1 kb federation observe api。
- `tests/unit/orchestrator/api/test_v1_kb_federation_store_api_unit.py`：单元测试（orchestrator/api）：v1 kb federation store api。
- `tests/unit/orchestrator/api/test_v1_kb_federation_sync_unit.py`：单元测试（orchestrator/api）：v1 kb federation sync。
- `tests/unit/orchestrator/api/test_v1_kb_observe_api_unit.py`：单元测试（orchestrator/api）：v1 kb observe api。
- `tests/unit/orchestrator/api/test_v1_overview_api_unit.py`：单元测试（orchestrator/api）：v1 overview api。
- `tests/unit/orchestrator/api/test_v1_overview_kb_consistency_unit.py`：单元测试（orchestrator/api）：v1 overview kb consistency。
- `tests/unit/orchestrator/api/test_v1_scheduler_policy_unit.py`：单元测试（orchestrator/api）：v1 scheduler policy。
- `tests/unit/orchestrator/api/test_v1_scheduling_observe_api_unit.py`：单元测试（orchestrator/api）：v1 scheduling observe api。
- `tests/unit/orchestrator/clients/test_checkpoint_client_unit.py`：单元测试（orchestrator/clients）：checkpoint client。
- `tests/unit/orchestrator/clients/test_checkpoint_finops_persist_unit.py`：单元测试（orchestrator/clients）：checkpoint finops persist。
- `tests/unit/orchestrator/clients/test_llm_provider_switch_unit.py`：单元测试（orchestrator/clients）：llm provider switch。
- `tests/unit/orchestrator/clients/test_trace_service_error_envelope_unit.py`：单元测试（orchestrator/clients）：trace service error envelope。
- `tests/unit/orchestrator/core/test_agent_base_skill_bias_unit.py`：单元测试（orchestrator/core）：agent base skill bias。
- `tests/unit/orchestrator/core/test_agent_tools_callflow_alignment_unit.py`：单元测试（orchestrator/core）：agent tools callflow alignment。
- `tests/unit/orchestrator/core/test_agent_tools_incremental_unit.py`：单元测试（orchestrator/core）：agent tools incremental。
- `tests/unit/orchestrator/core/test_asset_path_profile_unit.py`：单元测试（orchestrator/core）：asset path profile。
- `tests/unit/orchestrator/core/test_capability_kit_assembler_unit.py`：单元测试（orchestrator/core）：capability kit assembler。
- `tests/unit/orchestrator/core/test_capability_kits_unit.py`：单元测试（orchestrator/core）：capability kits。
- `tests/unit/orchestrator/core/test_chunk_quota_soft_warn_unit.py`：单元测试（orchestrator/core）：chunk quota soft warn。
- `tests/unit/orchestrator/core/test_chunk_store_unit.py`：单元测试（orchestrator/core）：chunk store。
- `tests/unit/orchestrator/core/test_context_pipeline_unit.py`：单元测试（orchestrator/core）：context pipeline。
- `tests/unit/orchestrator/core/test_correlation_ids_unit.py`：单元测试（orchestrator/core）：correlation ids。
- `tests/unit/orchestrator/core/test_decision_context_exploit_fatigue_unit.py`：单元测试（orchestrator/core）：decision context exploit fatigue。
- `tests/unit/orchestrator/core/test_decision_context_unit.py`：单元测试（orchestrator/core）：decision context。
- `tests/unit/orchestrator/core/test_decision_context_vuln_scan_fatigue_unit.py`：单元测试（orchestrator/core）：decision context vuln scan fatigue。
- `tests/unit/orchestrator/core/test_dispatch_rate_limit_unit.py`：单元测试（orchestrator/core）：dispatch rate limit。
- `tests/unit/orchestrator/core/test_execution_dispatcher_agent_lane_unit.py`：单元测试（orchestrator/core）：execution dispatcher agent lane。
- `tests/unit/orchestrator/core/test_execution_dispatcher_timeout_unit.py`：单元测试（orchestrator/core）：execution dispatcher timeout。
- `tests/unit/orchestrator/core/test_framework_detect_unit.py`：单元测试（orchestrator/core）：framework detect。
- `tests/unit/orchestrator/core/test_framework_target_upgrade_unit.py`：单元测试（orchestrator/core）：framework target upgrade。
- `tests/unit/orchestrator/core/test_governance_cost_unit.py`：单元测试（orchestrator/core）：governance cost。
- `tests/unit/orchestrator/core/test_http_enum_seeds_unit.py`：单元测试（orchestrator/core）：http enum seeds。
- `tests/unit/orchestrator/core/test_information_maturity_unit.py`：单元测试（orchestrator/core）：information maturity。
- `tests/unit/orchestrator/core/test_llm_decision_response_unit.py`：单元测试（orchestrator/core）：llm decision response。
- `tests/unit/orchestrator/core/test_manager_agent_signals_unit.py`：单元测试（orchestrator/core）：manager agent signals。
- `tests/unit/orchestrator/core/test_memory_store_unit.py`：单元测试（orchestrator/core）：memory store。
- `tests/unit/orchestrator/core/test_orchestrator_task_store_v1_unit.py`：单元测试（orchestrator/core）：orchestrator task store v1。
- `tests/unit/orchestrator/core/test_orchestrator_web_vuln_context_unit.py`：单元测试（orchestrator/core）：orchestrator web vuln context。
- `tests/unit/orchestrator/core/test_phase_capability_policy_unit.py`：单元测试（orchestrator/core）：phase capability policy。
- `tests/unit/orchestrator/core/test_phase_clock_restore_unit.py`：单元测试（orchestrator/core）：phase clock restore。
- `tests/unit/orchestrator/core/test_phase_gate_blocked_dedupe_unit.py`：单元测试（orchestrator/core）：phase gate blocked dedupe。
- `tests/unit/orchestrator/core/test_phase_transition_guard_unit.py`：单元测试（orchestrator/core）：phase transition guard。
- `tests/unit/orchestrator/core/test_preflight_nuclei_unknown_framework_unit.py`：单元测试（orchestrator/core）：preflight nuclei unknown framework。
- `tests/unit/orchestrator/core/test_prompt_injection_guard_unit.py`：单元测试（orchestrator/core）：prompt injection guard。
- `tests/unit/orchestrator/core/test_r6c_plan_dispatch_inflight_unit.py`：单元测试（orchestrator/core）：r6c plan dispatch inflight。
- `tests/unit/orchestrator/core/test_read_target_list_rel_path_unit.py`：单元测试（orchestrator/core）：read target list rel path。
- `tests/unit/orchestrator/core/test_recon_dirsearch_seeds_unit.py`：单元测试（orchestrator/core）：recon dirsearch seeds。
- `tests/unit/orchestrator/core/test_recon_exit_service_maturity_unit.py`：单元测试（orchestrator/core）：recon exit service maturity。
- `tests/unit/orchestrator/core/test_skill_pack_loader_unit.py`：单元测试（orchestrator/core）：skill pack loader。
- `tests/unit/orchestrator/core/test_skill_params_normalize_unit.py`：单元测试（orchestrator/core）：skill params normalize。
- `tests/unit/orchestrator/core/test_skill_policies_loader_unit.py`：单元测试（orchestrator/core）：skill policies loader。
- `tests/unit/orchestrator/core/test_state_machine_exploit_loop_break_force_report_unit.py`：单元测试（orchestrator/core）：state machine exploit loop break force report。
- `tests/unit/orchestrator/core/test_state_machine_loop_guard_integration_unit.py`：单元测试（orchestrator/core）：state machine loop guard integration。
- `tests/unit/orchestrator/core/test_state_machine_plan_list_branch_unit.py`：单元测试（orchestrator/core）：state machine plan list branch。
- `tests/unit/orchestrator/core/test_state_machine_skill_alias_unit.py`：单元测试（orchestrator/core）：state machine skill alias。
- `tests/unit/orchestrator/core/test_state_machine_skill_bias_unit.py`：单元测试（orchestrator/core）：state machine skill bias。
- `tests/unit/orchestrator/core/test_task_inflight_unit.py`：单元测试（orchestrator/core）：task inflight。
- `tests/unit/orchestrator/core/test_web_recon_kit_membership_unit.py`：单元测试（orchestrator/core）：web recon kit membership。
- `tests/unit/orchestrator/kb/test_kb_embed_quota_unit.py`：单元测试（orchestrator/kb）：kb embed quota。
- `tests/unit/orchestrator/kb/test_kb_experience_payload_unit.py`：单元测试（orchestrator/kb）：kb experience payload。
- `tests/unit/orchestrator/kb/test_kb_experience_promotion_unit.py`：单元测试（orchestrator/kb）：kb experience promotion。
- `tests/unit/orchestrator/kb/test_kb_hierarchical_rag_unit.py`：单元测试（orchestrator/kb）：kb hierarchical rag。
- `tests/unit/orchestrator/kb/test_kb_legacy_retrieval_unit.py`：单元测试（orchestrator/kb）：kb legacy retrieval。
- `tests/unit/orchestrator/kb/test_kb_manual_admin_unit.py`：单元测试（orchestrator/kb）：kb manual admin。
- `tests/unit/orchestrator/kb/test_kb_pipeline_unit.py`：单元测试（orchestrator/kb）：kb pipeline。
- `tests/unit/orchestrator/kb/test_kb_query_purify_unit.py`：单元测试（orchestrator/kb）：kb query purify。
- `tests/unit/orchestrator/kb/test_kb_retrieval_scoring_unit.py`：单元测试（orchestrator/kb）：kb retrieval scoring。
- `tests/unit/orchestrator/kb/test_kb_static_tiers_unit.py`：单元测试（orchestrator/kb）：kb static tiers。
- `tests/unit/orchestrator/planning/test_instruction_compiler_r4d_unit.py`：单元测试（orchestrator/planning）：instruction compiler r4d。
- `tests/unit/orchestrator/planning/test_instruction_compiler_skeleton_unit.py`：单元测试（orchestrator/planning）：instruction compiler skeleton。
- `tests/unit/orchestrator/planning/test_instruction_compiler_skip_invalid_chunk_unit.py`：单元测试（orchestrator/planning）：instruction compiler skip invalid chunk。
- `tests/unit/orchestrator/planning/test_plan_business_invalid_chunk_ref_unit.py`：单元测试（orchestrator/planning）：plan business invalid chunk ref。
- `tests/unit/orchestrator/planning/test_plan_business_validate_unit.py`：单元测试（orchestrator/planning）：plan business validate。
- `tests/unit/orchestrator/planning/test_plan_feature_flags_unit.py`：单元测试（orchestrator/planning）：plan feature flags。
- `tests/unit/orchestrator/planning/test_plan_kit_anchor_unit.py`：单元测试（orchestrator/planning）：plan kit anchor。
- `tests/unit/orchestrator/planning/test_plan_list_decision_unit.py`：单元测试（orchestrator/planning）：plan list decision。
- `tests/unit/orchestrator/planning/test_plan_schema_unit.py`：单元测试（orchestrator/planning）：plan schema。
- `tests/unit/orchestrator/planning/test_r4f_d_plan_compile_fail_dispatch_unit.py`：单元测试（orchestrator/planning）：r4f d plan compile fail dispatch。
- `tests/unit/orchestrator/planning/test_rag_plan_chunk_refs_unit.py`：单元测试（orchestrator/planning）：rag plan chunk refs。
- `tests/unit/orchestrator/planning/test_structured_error_envelope_unit.py`：单元测试（orchestrator/planning）：structured error envelope。
- `tests/unit/orchestrator/runtime/test_langgraph_runtime.py`：单元测试（orchestrator/runtime）：langgraph runtime。
- `tests/unit/scripts/test_orch_compile_profile_unit.py`：单元测试（scripts）：orch compile profile。
- `tests/unit/scripts/test_orch_readiness_checks_unit.py`：单元测试（scripts）：orch readiness checks。
- `tests/unit/scripts/test_v1_final_form_readiness_unit.py`：单元测试（scripts）：v1 final form readiness。
- `tests/unit/skills/test_kb_extract_features_unit.py`：单元测试（skills）：kb extract features。
- `tests/unit/skills/test_nmap_url_strip_unit.py`：单元测试（skills）：nmap url strip。
- `tests/unit/skills/test_nuclei_direct_fallback_unit.py`：单元测试（skills）：nuclei direct fallback。
- `tests/unit/skills/test_v1_micro_sample_run_unit.py`：单元测试（skills）：v1 micro sample run。
- `tests/unit/skills/test_web_vuln_common_etl.py`：单元测试（skills）：web vuln common etl。
- `tests/unit/skills/test_web_vuln_pipeline_etl.py`：单元测试（skills）：web vuln pipeline etl。
- `tests/unit/skills/test_web_vuln_pipeline_execute_unit.py`：单元测试（skills）：web vuln pipeline execute。
