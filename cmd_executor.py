#!/usr/bin/env python
import subprocess
import sys
import os

os.chdir('E:\\ai批改\\homework_grader_system')

# Command 1
print("="*80)
print("COMMAND 1: Check available questions and missing audits")
print("="*80)
result1 = subprocess.run([
    sys.executable, '-c', """
from pathlib import Path
import os

os.chdir('E:\\\\ai批改\\\\homework_grader_system')

# List what we have
questions_in_data = sorted([d.name for d in Path('data/3.20_physics').iterdir() if d.is_dir() and d.name.startswith('question_')])
questions_with_audit = sorted([d.name for d in Path('outputs/audit_reports').iterdir() if d.is_dir() and d.name.startswith('question_')])

print('Questions in data:', questions_in_data)
print('Questions with audits:', questions_with_audit)

missing = set(questions_in_data) - set(questions_with_audit)
print('Missing audits:', sorted(missing))

# Pick candidates
candidates = [q for q in ['question_13', 'question_05', 'question_10'] if q in missing][:2]
print('Selected candidates:', candidates)

for q in candidates:
    sdir = Path(f'data/3.20_physics/{q}/students')
    if sdir.exists():
        count = len([f for f in sdir.iterdir() if f.is_file()])
        print(f'{q}: {count} students')
"""
], capture_output=False, text=True)
print(f"Exit code: {result1.returncode}\n")

# Command 2
print("="*80)
print("COMMAND 2: Batch grade question_13")
print("="*80)
result2 = subprocess.run([
    sys.executable, 'scripts/batch_grade.py',
    '--students_dir', 'data/3.20_physics/question_13/students',
    '--rubric_file', 'data/3.20_physics/reference_rubric.json',
    '--output_dir', 'outputs/batch_results/conn_probe/question_13',
    '--db_path', 'outputs/grading_database.db',
    '--concurrency', '8'
], capture_output=False, text=True)
print(f"Exit code: {result2.returncode}\n")

# Command 4a - Parse question_13 summary.csv
print("="*80)
print("COMMAND 4a: Parse question_13 summary.csv")
print("="*80)
result4a = subprocess.run([
    sys.executable, '-c', """
import csv
from pathlib import Path
import os

os.chdir('E:\\\\ai批改\\\\homework_grader_system')

csv_file = 'outputs/batch_results/conn_probe/question_13/summary.csv'
if Path(csv_file).exists():
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        error_counts = {}
        total = 0
        for row in reader:
            total += 1
            status = row.get('Error_Status', 'UNKNOWN')
            error_counts[status] = error_counts.get(status, 0) + 1
        
        print(f"Total records: {total}")
        print("Error_Status counts:")
        for status, count in sorted(error_counts.items()):
            print(f"  {status}: {count}")
else:
    print(f"File not found: {csv_file}")
"""
], capture_output=False, text=True)
print(f"Exit code: {result4a.returncode}\n")

# Command 3
print("="*80)
print("COMMAND 3: Batch grade question_05")
print("="*80)
result3 = subprocess.run([
    sys.executable, 'scripts/batch_grade.py',
    '--students_dir', 'data/3.20_physics/question_05/students',
    '--rubric_file', 'data/3.20_physics/reference_rubric.json',
    '--output_dir', 'outputs/batch_results/conn_probe/question_05',
    '--db_path', 'outputs/grading_database.db',
    '--concurrency', '8'
], capture_output=False, text=True)
print(f"Exit code: {result3.returncode}\n")

# Command 4b - Parse question_05 summary.csv
print("="*80)
print("COMMAND 4b: Parse question_05 summary.csv")
print("="*80)
result4b = subprocess.run([
    sys.executable, '-c', """
import csv
from pathlib import Path
import os

os.chdir('E:\\\\ai批改\\\\homework_grader_system')

csv_file = 'outputs/batch_results/conn_probe/question_05/summary.csv'
if Path(csv_file).exists():
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        error_counts = {}
        total = 0
        for row in reader:
            total += 1
            status = row.get('Error_Status', 'UNKNOWN')
            error_counts[status] = error_counts.get(status, 0) + 1
        
        print(f"Total records: {total}")
        print("Error_Status counts:")
        for status, count in sorted(error_counts.items()):
            print(f"  {status}: {count}")
else:
    print(f"File not found: {csv_file}")
"""
], capture_output=False, text=True)
print(f"Exit code: {result4b.returncode}\n")

# Command 5 - Search for connection error handling patterns
print("="*80)
print("COMMAND 5: Search for connection error handling in deepseek_engine.py")
print("="*80)
result5 = subprocess.run([
    sys.executable, '-c', """
import os
from pathlib import Path

os.chdir('E:\\\\ai批改\\\\homework_grader_system')

search_file = 'src/deepseek_engine.py'
if Path(search_file).exists():
    with open(search_file, 'r', encoding='utf-8') as f:
        content = f.read()
        lines = content.split('\\n')
        
        # Search for connection-related patterns
        patterns = ['connection', 'timeout', 'error', 'except', 'retry', 'ConnectionError']
        matching_lines = []
        
        for i, line in enumerate(lines, 1):
            for pattern in patterns:
                if pattern.lower() in line.lower():
                    matching_lines.append((i, line))
                    break
        
        print(f"Found {len(matching_lines)} lines with connection error handling patterns:")
        for line_num, line in matching_lines[:50]:
            print(f"{line_num}: {line}")
else:
    print(f"File not found: {search_file}")
"""
], capture_output=False, text=True)
print(f"Exit code: {result5.returncode}\n")

# Command 6 - Search for connection error handling in qwen_engine.py
print("="*80)
print("COMMAND 6: Search for connection error handling in qwen_engine.py")
print("="*80)
result6 = subprocess.run([
    sys.executable, '-c', """
import os
from pathlib import Path

os.chdir('E:\\\\ai批改\\\\homework_grader_system')

search_file = 'src/qwen_engine.py'
if Path(search_file).exists():
    with open(search_file, 'r', encoding='utf-8') as f:
        content = f.read()
        lines = content.split('\\n')
        
        # Search for connection-related patterns
        patterns = ['connection', 'timeout', 'error', 'except', 'retry', 'ConnectionError']
        matching_lines = []
        
        for i, line in enumerate(lines, 1):
            for pattern in patterns:
                if pattern.lower() in line.lower():
                    matching_lines.append((i, line))
                    break
        
        print(f"Found {len(matching_lines)} lines with connection error handling patterns:")
        for line_num, line in matching_lines[:50]:
            print(f"{line_num}: {line}")
else:
    print(f"File not found: {search_file}")
"""
], capture_output=False, text=True)
print(f"Exit code: {result6.returncode}\n")
