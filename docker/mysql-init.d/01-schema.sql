-- TrustGuard Agent schema
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS tg_task (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL,
    name VARCHAR(255),
    description VARCHAR(512),
    business_background TEXT COMMENT '业务背景（注入决策上下文前需安全校验）',
    extra_user_requirements TEXT COMMENT '用户额外需求（注入前需安全校验）',
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
    INDEX idx_task_ts (task_id, ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Evidence：任务上下文（编排器更新，供后续读取）
CREATE TABLE IF NOT EXISTS tg_task_context (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL,
    context_json JSON,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_task_id (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

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
