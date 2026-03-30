-- Core DDL for Homework Grader System
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    status TEXT NOT NULL, -- PENDING, PROCESSING, COMPLETED, FAILED, REJECTED
    celery_task_id TEXT, -- Phase 28: Track Celery async task ID for revocation
    error_message TEXT,
    review_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED'
        CHECK (review_status IN ('NOT_REQUIRED', 'PENDING_REVIEW', 'REVIEWED')),
    human_feedback_json TEXT, -- Structured teacher correction payload (JSON string)
    is_regression_sample INTEGER NOT NULL DEFAULT 0 CHECK (is_regression_sample IN (0, 1)),
    fallback_reason TEXT, -- Why machine path was rejected/degraded to human
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

CREATE INDEX IF NOT EXISTS idx_task_id ON grading_results(task_id);
CREATE INDEX IF NOT EXISTS idx_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_review_status ON tasks(review_status);
