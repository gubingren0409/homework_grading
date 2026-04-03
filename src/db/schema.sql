-- Core DDL for Homework Grader System
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    status TEXT NOT NULL, -- Pipeline status: PENDING, PROCESSING, COMPLETED, FAILED
    grading_status TEXT
        CHECK (grading_status IN ('SCORED', 'REJECTED_UNREADABLE') OR grading_status IS NULL),
    celery_task_id TEXT, -- Phase 28: Track Celery async task ID for revocation
    rubric_id TEXT, -- Optional reference rubric used for grading
    error_message TEXT,
    review_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED'
        CHECK (review_status IN ('NOT_REQUIRED', 'PENDING_REVIEW', 'REVIEWED')),
    fallback_reason TEXT, -- Why machine path was rejected/degraded to human
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Phase 38: Data Hygiene Pipeline (physical isolation)
CREATE TABLE IF NOT EXISTS hygiene_interception_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    task_id TEXT,
    interception_node TEXT NOT NULL
        CHECK (interception_node IN ('blank', 'short_circuit', 'unreadable')),
    raw_image_path TEXT,
    action TEXT NOT NULL
        CHECK (action IN ('discard', 'manual_review')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Phase 38: Annotation Asset Pipeline (golden dataset source of truth)
CREATE TABLE IF NOT EXISTS golden_annotation_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    region_id TEXT NOT NULL,
    region_type TEXT NOT NULL
        CHECK (region_type IN ('question_region', 'answer_region')),
    image_width INTEGER NOT NULL CHECK (image_width > 0),
    image_height INTEGER NOT NULL CHECK (image_height > 0),
    bbox_coordinates TEXT NOT NULL, -- JSON array: [x1, y1, x2, y2] in absolute pixels
    perception_ir_snapshot TEXT NOT NULL, -- JSON deep copy at submission time
    cognitive_ir_snapshot TEXT NOT NULL, -- JSON deep copy at submission time
    teacher_text_feedback TEXT NOT NULL,
    expected_score REAL NOT NULL,
    is_integrated_to_dataset INTEGER NOT NULL DEFAULT 0 CHECK (is_integrated_to_dataset IN (0, 1)),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS grading_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    student_id TEXT,
    question_id TEXT,
    total_deduction REAL,
    is_pass BOOLEAN,
    report_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

-- Phase P0: Runtime telemetry persistence for dashboard-grade observability
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
    prompt_cache_level TEXT NOT NULL
        CHECK (prompt_cache_level IN ('L1', 'L2', 'SOURCE', 'LKG')),
    prompt_token_estimate INTEGER NOT NULL CHECK (prompt_token_estimate >= 0),
    succeeded INTEGER NOT NULL CHECK (succeeded IN (0, 1)),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

-- Phase P1: Prompt control plane state and audit
CREATE TABLE IF NOT EXISTS prompt_control_state (
    prompt_key TEXT PRIMARY KEY,
    forced_variant_id TEXT,
    lkg_mode INTEGER NOT NULL DEFAULT 0 CHECK (lkg_mode IN (0, 1)),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS prompt_ab_configs (
    prompt_key TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    rollout_percentage INTEGER NOT NULL CHECK (rollout_percentage >= 0 AND rollout_percentage <= 100),
    variant_weights_json TEXT NOT NULL,
    segment_prefixes_json TEXT NOT NULL,
    sticky_salt TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS prompt_ops_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    operator_id TEXT,
    action TEXT NOT NULL,
    prompt_key TEXT,
    payload_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Phase 43: External skill validation records (objective checker trace)
CREATE TABLE IF NOT EXISTS skill_validation_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    question_id TEXT,
    checker TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('ok', 'mismatch', 'error')),
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    details_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS rubrics (
    rubric_id TEXT PRIMARY KEY,
    question_id TEXT,
    rubric_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_task_id ON grading_results(task_id);
CREATE INDEX IF NOT EXISTS idx_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_runtime_telemetry_model_used ON task_runtime_telemetry(model_used);
CREATE INDEX IF NOT EXISTS idx_runtime_telemetry_created_at ON task_runtime_telemetry(created_at);
CREATE INDEX IF NOT EXISTS idx_runtime_telemetry_cache_level ON task_runtime_telemetry(prompt_cache_level);
CREATE INDEX IF NOT EXISTS idx_prompt_ops_action ON prompt_ops_audit_log(action);
CREATE INDEX IF NOT EXISTS idx_prompt_ops_created_at ON prompt_ops_audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_prompt_ops_prompt_key ON prompt_ops_audit_log(prompt_key);
CREATE INDEX IF NOT EXISTS idx_skill_validation_task_id ON skill_validation_records(task_id);
CREATE INDEX IF NOT EXISTS idx_skill_validation_checker ON skill_validation_records(checker);
CREATE INDEX IF NOT EXISTS idx_rubrics_created_at ON rubrics(created_at);
CREATE INDEX IF NOT EXISTS idx_hygiene_trace_id ON hygiene_interception_log(trace_id);
CREATE INDEX IF NOT EXISTS idx_hygiene_created_at ON hygiene_interception_log(created_at);
CREATE INDEX IF NOT EXISTS idx_golden_trace_id ON golden_annotation_assets(trace_id);
CREATE INDEX IF NOT EXISTS idx_golden_task_id ON golden_annotation_assets(task_id);
CREATE INDEX IF NOT EXISTS idx_golden_region_id ON golden_annotation_assets(region_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_golden_trace_region ON golden_annotation_assets(trace_id, region_id);
