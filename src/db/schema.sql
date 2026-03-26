-- Core DDL for Homework Grader System
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    status TEXT NOT NULL, -- PENDING, PROCESSING, COMPLETED, FAILED
    error_message TEXT,
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
