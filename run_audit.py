#!/usr/bin/env python3
"""
Audit audit script to run batch grading for missing questions.
"""
import subprocess
import sys
import os
from pathlib import Path
import json

os.chdir("E:\\ai批改\\homework_grader_system")

# Step 1: Audit status
print("="*80)
print("STEP 1: AUDIT STATUS")
print("="*80)

questions_in_data = sorted([d.name for d in Path("data/3.20_physics").iterdir() if d.is_dir() and d.name.startswith("question_")])
questions_with_audit = sorted([d.name for d in Path("outputs/audit_reports").iterdir() if d.is_dir() and d.name.startswith("question_")])

print(f"Questions in data/3.20_physics: {questions_in_data}")
print(f"Questions with audit reports: {questions_with_audit}")

missing_audits = set(questions_in_data) - set(questions_with_audit)
print(f"Missing audit questions: {sorted(missing_audits)}")

# Candidate selection
preference_order = ["question_13", "question_05", "question_10"]
candidates = [q for q in preference_order if q in missing_audits][:2]

print(f"\nSelected candidates for processing: {candidates}")

# Step 2: Count students
print("\n" + "="*80)
print("STEP 2: STUDENT COUNTS")
print("="*80)

for qid in candidates:
    students_dir = Path(f"data/3.20_physics/{qid}/students")
    if students_dir.exists():
        count = len([f for f in students_dir.iterdir() if f.is_file()])
        print(f"{qid}: {count} student files")

# Step 3: Run batch grading
print("\n" + "="*80)
print("STEP 3: BATCH GRADING EXECUTION")
print("="*80)

for qid in candidates:
    print(f"\n--- Processing {qid} ---")
    students_dir = f"data/3.20_physics/{qid}/students"
    rubric_file = f"data/3.20_physics/reference_rubric.json"
    output_dir = f"outputs/batch_results/conn_probe/{qid}"
    db_path = "outputs/grading_database.db"
    
    cmd = [
        sys.executable,
        "scripts/batch_grade.py",
        "--students_dir", students_dir,
        "--rubric_file", rubric_file,
        "--output_dir", output_dir,
        "--db_path", db_path,
        "--concurrency", "8"
    ]
    
    print(f"Command: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, capture_output=False, text=True)
    print(f"\nReturn code: {result.returncode}")
    
    # Step 4: Parse summary CSV
    summary_file = Path(output_dir) / "summary.csv"
    if summary_file.exists():
        print(f"\n--- Summary for {qid} ---")
        with open(summary_file) as f:
            lines = f.readlines()
            print("".join(lines[:5]))  # Print header + first few rows
            
            # Count errors
            import csv
            f.seek(0)
            reader = csv.DictReader(f)
            error_counts = {}
            for row in reader:
                status = row.get("Error_Status", "NONE")
                if status not in error_counts:
                    error_counts[status] = 0
                error_counts[status] += 1
            
            print("\nError Status Summary:")
            for status, count in sorted(error_counts.items(), key=lambda x: -x[1]):
                print(f"  {status}: {count}")

print("\n" + "="*80)
print("STEP 5: CODE ANALYSIS - Connection Error Handling")
print("="*80)

# Grep for connection error patterns
import subprocess
patterns = [
    "connection_error_count",
    "MAX_CONNECTION_ERRORS",
    "fallback",
    "APIConnectionError",
    "APITimeoutError"
]

for pattern in patterns:
    print(f"\n--- Searching for '{pattern}' ---")
    result = subprocess.run(
        ["findstr", "/r", pattern, "src\\cognitive\\engines\\deepseek_engine.py", "src\\perception\\engines\\qwen_engine.py"],
        capture_output=True,
        text=True
    )
    if result.stdout:
        print(result.stdout)
