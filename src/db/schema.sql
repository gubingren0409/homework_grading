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
CREATE INDEX IF NOT EXISTS idx_skill_validation_task_id ON skill_validation_records(task_id);
CREATE INDEX IF NOT EXISTS idx_skill_validation_checker ON skill_validation_records(checker);
CREATE INDEX IF NOT EXISTS idx_rubrics_created_at ON rubrics(created_at);
CREATE INDEX IF NOT EXISTS idx_hygiene_trace_id ON hygiene_interception_log(trace_id);
CREATE INDEX IF NOT EXISTS idx_hygiene_created_at ON hygiene_interception_log(created_at);
CREATE INDEX IF NOT EXISTS idx_golden_trace_id ON golden_annotation_assets(trace_id);
CREATE INDEX IF NOT EXISTS idx_golden_task_id ON golden_annotation_assets(task_id);
CREATE INDEX IF NOT EXISTS idx_golden_region_id ON golden_annotation_assets(region_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_golden_trace_region ON golden_annotation_assets(trace_id, region_id);
