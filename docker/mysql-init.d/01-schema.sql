-- TrustGuard Agent schema
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS tg_task (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL,
    name VARCHAR(255),
    description VARCHAR(512),
    business_background TEXT COMMENT '业务背景（注入决策上下文前需安全校验）',
    extra_user_requirements TEXT COMMENT '用户额外需求（注入前需安全校验）',
    execution_policy JSON COMMENT '结构化执行权限；由 Orchestrator 硬门禁消费',
    target VARCHAR(512) NOT NULL COMMENT '靶机 URL，必填',
    status VARCHAR(32),
    current_phase VARCHAR(32),
    created_at DATETIME,
    updated_at DATETIME,
    UNIQUE KEY uk_task_id (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 用户管理表（平台管理员账号）
CREATE TABLE IF NOT EXISTS tg_user (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL COMMENT '业务唯一标识 user-{uuid}',
    username VARCHAR(64) NOT NULL COMMENT '用户名（登录名）',
    display_name VARCHAR(128) COMMENT '显示名称',
    email VARCHAR(255) COMMENT '邮箱',
    role VARCHAR(32) NOT NULL DEFAULT 'VIEWER' COMMENT 'ADMIN|OPERATOR|VIEWER',
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE|DISABLED',
    password_hash VARCHAR(255) NULL COMMENT 'BCrypt hash',
    last_login_at DATETIME COMMENT '最近登录时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_id (user_id),
    UNIQUE KEY uk_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='平台用户表';

-- Demo accounts: admin/admin123, operator/operator123, viewer/viewer123.
INSERT IGNORE INTO tg_user (user_id, username, display_name, email, role, status, password_hash, created_at, updated_at)
VALUES ('user-000000001', 'admin', '超级管理员', 'admin@trustguard.local', 'ADMIN', 'ACTIVE', '$2b$12$grzdfkxkLEcQzkFl2wcq2.HaOPsL4U4HHHLM2iivq6uXMiVcLUIVe', NOW(), NOW()),
       ('user-000000002', 'operator', '运维人员', 'operator@trustguard.local', 'OPERATOR', 'ACTIVE', '$2b$12$xFNa9GfvJA1ca9CvJXv.u.hyPw5C/0bD62cB5jZZYprbOcVCp6we6', NOW(), NOW()),
       ('user-000000003', 'viewer', '只读用户', 'viewer@trustguard.local', 'VIEWER', 'ACTIVE', '$2b$12$qWYzVxMwqDvtS7fYsRDmA.iMHjIR9CqXxNr.sXLvBylb26RUaD6xC', NOW(), NOW());

-- Evidence：Trace 事件（编排器/执行器上报，Gateway读库展示）
CREATE TABLE IF NOT EXISTS tg_trace_events (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL,
    event_id VARCHAR(64) NOT NULL,
    ts VARCHAR(64) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    source_module VARCHAR(64) NOT NULL,
    payload JSON,
    run_started_at VARCHAR(64) NULL COMMENT '事件实际开始时间（ISO-8601）',
    run_finished_at VARCHAR(64) NULL COMMENT '事件实际结束时间（ISO-8601）',
    run_duration_ms BIGINT NULL COMMENT '事件实际耗时（毫秒）',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_task_id (task_id),
    INDEX idx_task_ts (task_id, ts),
    INDEX idx_trace_created_at (created_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Evidence：结构化 CoT 推理步骤（与 tg_trace_events 并行；step_type 为字符串以便扩展）
CREATE TABLE IF NOT EXISTS tg_reasoning_steps (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL,
    trace_id VARCHAR(64) NOT NULL COMMENT '与 task_id 1:1，取值相等',
    step_id VARCHAR(64) NOT NULL,
    step_type VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    started_at VARCHAR(64) NULL,
    finished_at VARCHAR(64) NULL,
    duration_ms BIGINT NULL,
    summary TEXT NULL,
    payload JSON,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_step_id (step_id),
    INDEX idx_rs_task_id (task_id),
    INDEX idx_rs_trace_started (trace_id, started_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Evidence：任务上下文（编排器更新，供后续读取）
CREATE TABLE IF NOT EXISTS tg_task_context (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL,
    context_json JSON,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_task_id (task_id),
    INDEX idx_task_context_updated_at (updated_at, task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 告警研判人工复核：保留追加式审计记录，不覆盖 Agent 原始结论。
CREATE TABLE IF NOT EXISTS tg_alert_triage_review (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    review_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    reviewer_user_id VARCHAR(64) NOT NULL,
    reviewer_username VARCHAR(128) NOT NULL,
    decision VARCHAR(32) NOT NULL COMMENT 'CONFIRMED|OVERRIDDEN|NEEDS_MORE_EVIDENCE',
    human_verdict VARCHAR(32) NULL COMMENT 'true_positive|false_positive|suspicious|insufficient_evidence',
    notes TEXT NULL,
    selected_actions JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uk_alert_triage_review_id (review_id),
    INDEX idx_alert_triage_review_task (task_id, created_at),
    INDEX idx_alert_triage_review_user (reviewer_user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='告警研判人工复核审计记录';

-- Evidence：任务断点（停止时保存，续跑时恢复）
CREATE TABLE IF NOT EXISTS tg_task_checkpoint (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL,
    current_phase VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    target_context_json JSON,
    history_summary TEXT,
    name VARCHAR(255),
    target VARCHAR(512),
    description VARCHAR(512),
    phase_start_at DATETIME(6) NULL COMMENT 'UTC phase wall-clock start (orchestrator checkpoint)',
    current_phase_duration_limit_sec INT NULL COMMENT 'Phase duration limit seconds',
    llm_input_tokens_total  BIGINT NULL DEFAULT 0 COMMENT 'LLM 累计输入 token（断点续跑还原用）',
    llm_output_tokens_total BIGINT NULL DEFAULT 0 COMMENT 'LLM 累计输出 token（断点续跑还原用）',
    cumulative_cost_usd      DOUBLE NULL DEFAULT 0.0 COMMENT 'LLM 累计成本 USD（断点续跑还原用）',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_task_id (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Supervisor Agent 会话：MySQL 为长期事实源，Redis 仅保留热数据镜像。
CREATE TABLE IF NOT EXISTS tg_agent_conversation (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    conversation_id VARCHAR(128) NOT NULL,
    actor_id VARCHAR(128) NOT NULL,
    task_id VARCHAR(128) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    UNIQUE KEY uk_agent_conversation_actor (actor_id, conversation_id),
    INDEX idx_agent_conversation_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Supervisor Agent 会话';

CREATE TABLE IF NOT EXISTS tg_agent_conversation_message (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    message_id VARCHAR(160) NOT NULL,
    conversation_id VARCHAR(128) NOT NULL,
    actor_id VARCHAR(128) NOT NULL,
    role VARCHAR(16) NOT NULL,
    message_text MEDIUMTEXT NOT NULL,
    activities_json JSON NULL,
    draft_json JSON NULL,
    confirmation_token TEXT NULL,
    task_id VARCHAR(128) NULL,
    task_status VARCHAR(32) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uk_agent_message_actor (actor_id, conversation_id, message_id),
    INDEX idx_agent_message_conversation (actor_id, conversation_id, id),
    INDEX idx_agent_message_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Supervisor Agent 结构化消息';
