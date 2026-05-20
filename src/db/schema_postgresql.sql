-- PostgreSQL Schema for Homework Grader System
-- Migrated from SQLite with optimizations for concurrent writes

-- Core tasks table
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    grading_status TEXT CHECK (grading_status IN ('SCORED', 'REJECTED_UNREADABLE') OR grading_status IS NULL),
    celery_task_id TEXT,
    rubric_id TEXT,
    error_message TEXT,
    review_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED' CHECK (review_status IN ('NOT_REQUIRED', 'PENDING_REVIEW', 'REVIEWED')),
    fallback_reason TEXT,
    submitted_count INTEGER NOT NULL DEFAULT 0 CHECK (submitted_count >= 0),
    progress REAL NOT NULL DEFAULT 0 CHECK (progress >= 0.0 AND progress <= 1.0),
    eta_seconds INTEGER CHECK (eta_seconds IS NULL OR eta_seconds >= 0),
    last_heartbeat_at TIMESTAMP,
    teacher_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Hygiene interception log
CREATE TABLE IF NOT EXISTS hygiene_interception_log (
    id SERIAL PRIMARY KEY,
    trace_id TEXT NOT NULL,
    task_id TEXT,
    interception_node TEXT NOT NULL CHECK (interception_node IN ('blank', 'short_circuit', 'unreadable')),
    raw_image_path TEXT,
    action TEXT NOT NULL CHECK (action IN ('discard', 'manual_review')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Golden annotation assets
CREATE TABLE IF NOT EXISTS golden_annotation_assets (
    id SERIAL PRIMARY KEY,
    trace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    region_id TEXT NOT NULL,
    region_type TEXT NOT NULL CHECK (region_type IN ('question_region', 'answer_region')),
    image_width INTEGER NOT NULL CHECK (image_width > 0),
    image_height INTEGER NOT NULL CHECK (image_height > 0),
    bbox_coordinates TEXT NOT NULL,
    perception_ir_snapshot TEXT NOT NULL,
    cognitive_ir_snapshot TEXT NOT NULL,
    teacher_text_feedback TEXT NOT NULL,
    expected_score REAL NOT NULL,
    is_integrated_to_dataset INTEGER NOT NULL DEFAULT 0 CHECK (is_integrated_to_dataset IN (0, 1)),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Teacher review decisions
CREATE TABLE IF NOT EXISTS teacher_review_decisions (
    task_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    student_id TEXT,
    decision TEXT NOT NULL CHECK (decision IN ('CONFIRM_MACHINE', 'ADJUST_SCORE', 'MARK_UNREADABLE', 'ESCALATE')),
    final_score REAL,
    teacher_comment TEXT NOT NULL,
    include_in_dataset INTEGER NOT NULL DEFAULT 0 CHECK (include_in_dataset IN (0, 1)),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (task_id, sample_id),
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

-- Grading results
CREATE TABLE IF NOT EXISTS grading_results (
    id SERIAL PRIMARY KEY,
    task_id TEXT,
    student_id TEXT,
    question_id TEXT,
    total_deduction REAL,
    is_pass BOOLEAN,
    report_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

-- Runtime telemetry
CREATE TABLE IF NOT EXISTS task_runtime_telemetry (
    task_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    requested_model TEXT NOT NULL,
    model_used TEXT NOT NULL,
    route_reason TEXT NOT NULL,
    fallback_used INTEGER NOT NULL CHECK (fallback_used IN (0, 1)),
    fallback_reason TEXT,
    prompt_key TEXT NOT NULL,
    prompt_asset_version TEXT NOT NULL,
    prompt_variant_id TEXT NOT NULL,
    prompt_cache_level TEXT NOT NULL CHECK (prompt_cache_level IN ('L1', 'L2', 'SOURCE', 'LKG')),
    prompt_token_estimate INTEGER NOT NULL CHECK (prompt_token_estimate >= 0),
    succeeded INTEGER NOT NULL CHECK (succeeded IN (0, 1)),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

-- Prompt control state
CREATE TABLE IF NOT EXISTS prompt_control_state (
    prompt_key TEXT PRIMARY KEY,
    forced_variant_id TEXT,
    lkg_mode INTEGER NOT NULL DEFAULT 0 CHECK (lkg_mode IN (0, 1)),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Prompt AB configs
CREATE TABLE IF NOT EXISTS prompt_ab_configs (
    prompt_key TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    rollout_percentage INTEGER NOT NULL CHECK (rollout_percentage >= 0 AND rollout_percentage <= 100),
    variant_weights_json TEXT NOT NULL,
    segment_prefixes_json TEXT NOT NULL,
    sticky_salt TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Prompt ops audit log
CREATE TABLE IF NOT EXISTS prompt_ops_audit_log (
    id SERIAL PRIMARY KEY,
    trace_id TEXT NOT NULL,
    operator_id TEXT,
    action TEXT NOT NULL,
    prompt_key TEXT,
    payload_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Skill validation records
CREATE TABLE IF NOT EXISTS skill_validation_records (
    id SERIAL PRIMARY KEY,
    task_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    question_id TEXT,
    checker TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'mismatch', 'error')),
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    details_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

-- Paper tasks
CREATE TABLE IF NOT EXISTS paper_tasks (
    task_id TEXT PRIMARY KEY,
    student_id TEXT,
    bundle_id TEXT,
    paper_id TEXT NOT NULL,
    total_questions INTEGER NOT NULL CHECK (total_questions >= 0),
    answered_questions INTEGER NOT NULL CHECK (answered_questions >= 0),
    total_score_deduction REAL NOT NULL CHECK (total_score_deduction >= 0.0),
    requires_human_review INTEGER NOT NULL CHECK (requires_human_review IN (0, 1)),
    report_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

-- Paper question results
CREATE TABLE IF NOT EXISTS paper_question_results (
    id SERIAL PRIMARY KEY,
    task_id TEXT NOT NULL,
    student_id TEXT,
    question_id TEXT NOT NULL,
    page_indexes_json TEXT,
    total_deduction REAL NOT NULL CHECK (total_deduction >= 0.0),
    status TEXT NOT NULL,
    requires_human_review INTEGER NOT NULL CHECK (requires_human_review IN (0, 1)),
    report_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

-- Rubrics
CREATE TABLE IF NOT EXISTS rubrics (
    rubric_id TEXT PRIMARY KEY,
    question_id TEXT,
    rubric_json TEXT NOT NULL,
    source_fingerprint TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Rubric generate audit
CREATE TABLE IF NOT EXISTS rubric_generate_audit (
    id SERIAL PRIMARY KEY,
    trace_id TEXT NOT NULL,
    rubric_id TEXT,
    source_fingerprint TEXT NOT NULL,
    reused_from_cache INTEGER NOT NULL CHECK (reused_from_cache IN (0, 1)),
    force_regenerate INTEGER NOT NULL CHECK (force_regenerate IN (0, 1)),
    source_file_count INTEGER NOT NULL CHECK (source_file_count >= 1),
    client_ip TEXT,
    user_agent TEXT,
    referer TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance optimization
-- Tasks table indexes
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_review_status ON tasks(review_status);
CREATE INDEX IF NOT EXISTS idx_tasks_grading_status ON tasks(grading_status);
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_teacher_id ON tasks(teacher_id);
CREATE INDEX IF NOT EXISTS idx_tasks_celery_task_id ON tasks(celery_task_id);

-- Grading results indexes
CREATE INDEX IF NOT EXISTS idx_grading_results_task_id ON grading_results(task_id);
CREATE INDEX IF NOT EXISTS idx_grading_results_student_id ON grading_results(student_id);
CREATE INDEX IF NOT EXISTS idx_grading_results_question_id ON grading_results(question_id);

-- Runtime telemetry indexes
CREATE INDEX IF NOT EXISTS idx_runtime_telemetry_model_used ON task_runtime_telemetry(model_used);
CREATE INDEX IF NOT EXISTS idx_runtime_telemetry_created_at ON task_runtime_telemetry(created_at);
CREATE INDEX IF NOT EXISTS idx_runtime_telemetry_cache_level ON task_runtime_telemetry(prompt_cache_level);

-- Prompt ops audit indexes
CREATE INDEX IF NOT EXISTS idx_prompt_ops_action ON prompt_ops_audit_log(action);
CREATE INDEX IF NOT EXISTS idx_prompt_ops_created_at ON prompt_ops_audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_prompt_ops_prompt_key ON prompt_ops_audit_log(prompt_key);

-- Skill validation indexes
CREATE INDEX IF NOT EXISTS idx_skill_validation_task_id ON skill_validation_records(task_id);
CREATE INDEX IF NOT EXISTS idx_skill_validation_checker ON skill_validation_records(checker);

-- Paper tasks indexes
CREATE INDEX IF NOT EXISTS idx_paper_tasks_student_id ON paper_tasks(student_id);
CREATE INDEX IF NOT EXISTS idx_paper_tasks_bundle_id ON paper_tasks(bundle_id);
CREATE INDEX IF NOT EXISTS idx_paper_tasks_paper_id ON paper_tasks(paper_id);

-- Paper question results indexes
CREATE INDEX IF NOT EXISTS idx_paper_question_results_task_id ON paper_question_results(task_id);
CREATE INDEX IF NOT EXISTS idx_paper_question_results_question_id ON paper_question_results(question_id);

-- Rubrics indexes
CREATE INDEX IF NOT EXISTS idx_rubrics_created_at ON rubrics(created_at);
CREATE INDEX IF NOT EXISTS idx_rubrics_source_fingerprint ON rubrics(source_fingerprint);
CREATE INDEX IF NOT EXISTS idx_rubrics_question_id ON rubrics(question_id);

-- Rubric generate audit indexes
CREATE INDEX IF NOT EXISTS idx_rubric_generate_audit_created_at ON rubric_generate_audit(created_at);
CREATE INDEX IF NOT EXISTS idx_rubric_generate_audit_fingerprint ON rubric_generate_audit(source_fingerprint);

-- Hygiene log indexes
CREATE INDEX IF NOT EXISTS idx_hygiene_trace_id ON hygiene_interception_log(trace_id);
CREATE INDEX IF NOT EXISTS idx_hygiene_created_at ON hygiene_interception_log(created_at);
CREATE INDEX IF NOT EXISTS idx_hygiene_task_id ON hygiene_interception_log(task_id);

-- Golden annotation indexes
CREATE INDEX IF NOT EXISTS idx_golden_trace_id ON golden_annotation_assets(trace_id);
CREATE INDEX IF NOT EXISTS idx_golden_task_id ON golden_annotation_assets(task_id);
CREATE INDEX IF NOT EXISTS idx_golden_region_id ON golden_annotation_assets(region_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_golden_trace_region ON golden_annotation_assets(trace_id, region_id);

-- PostgreSQL-specific optimizations
-- Enable auto-vacuum for high-write tables
ALTER TABLE tasks SET (autovacuum_vacuum_scale_factor = 0.1);
ALTER TABLE grading_results SET (autovacuum_vacuum_scale_factor = 0.1);
ALTER TABLE paper_question_results SET (autovacuum_vacuum_scale_factor = 0.1);

-- Comments for documentation
COMMENT ON TABLE tasks IS 'Core task tracking table with concurrent write support';
COMMENT ON COLUMN tasks.last_heartbeat_at IS 'Worker heartbeat timestamp for long-running tasks';
COMMENT ON COLUMN tasks.progress IS 'Task progress from 0.0 to 1.0';
COMMENT ON COLUMN tasks.eta_seconds IS 'Estimated time to completion in seconds';
