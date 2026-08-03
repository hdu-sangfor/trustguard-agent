-- TrustGuard Agent: 误报（False Positive）追踪表
-- 记录从扫描器发现到人工确认的完整 FP 判定生命周期
-- 创建于 2026-07-17

CREATE TABLE IF NOT EXISTS tg_fp_findings (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    fp_id VARCHAR(64) NOT NULL COMMENT 'FP 记录唯一标识 fp-{uuid12}',
    task_id VARCHAR(64) NOT NULL COMMENT '关联任务 ID',
    finding_signature VARCHAR(64) NOT NULL COMMENT 'SHA256(template_id|url) 去重签名',
    template_id VARCHAR(255) COMMENT '扫描器模板 ID，如 nuclei/struts/s2-057',
    url VARCHAR(2048) COMMENT '漏洞关联 URL',
    title VARCHAR(512) COMMENT '漏洞名称/标题',
    severity VARCHAR(32) COMMENT '严重级别: critical/high/medium/low/info',
    source_skill_id VARCHAR(128) COMMENT '来源技能 ID，如 nuclei',
    source_phase VARCHAR(32) COMMENT '发现阶段: VULN_SCAN/EXPLOIT',
    current_verdict VARCHAR(32) NOT NULL DEFAULT 'UNVERIFIED' COMMENT '当前判定: UNVERIFIED/FALSE_POSITIVE/TRUE_POSITIVE/INCONCLUSIVE',
    verification_source VARCHAR(32) COMMENT '判定来源: LLM/HEURISTIC/HUMAN',
    verification_reasoning TEXT COMMENT '判定理由/LLM 推理',
    detected_at DATETIME NOT NULL COMMENT '发现时间',
    verified_at DATETIME COMMENT '首次判定时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_fp_id (fp_id),
    INDEX idx_task_id (task_id),
    INDEX idx_verdict (current_verdict),
    INDEX idx_template_id (template_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='误报追踪记录表';
