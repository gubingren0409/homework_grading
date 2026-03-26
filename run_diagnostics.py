#!/usr/bin/env python3
"""
Diagnostic executor for homework grader system batch operations
Executes commands 1-6 with full raw output capture
"""
import subprocess
import sys
import os
from pathlib import Path
import csv

# Set working directory
os.chdir('E:\\ai批改\\homework_grader_system')

print("=" * 100)
print("COMMAND 1: Check available questions and missing audits")
print("=" * 100)
try:
    result = subprocess.run([
        sys.executable, '-c', r"""
from pathlib import Path
import os

os.chdir(r'E:\ai批改\homework_grader_system')

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
    else:
        print(f'{q}: students directory not found')
"""
    ], capture_output=True, text=True, cwd='E:\\ai批改\\homework_grader_system')
    print("STDOUT:")
    print(result.stdout)
    if result.stderr:
        print("STDERR:")
        print(result.stderr)
    print(f"Exit code: {result.returncode}\n")
except Exception as e:
    print(f"Error executing command 1: {e}\n")

print("=" * 100)
print("COMMAND 2: Batch grade question_13")
print("=" * 100)
try:
    result = subprocess.run([
        sys.executable, 'scripts/batch_grade.py',
        '--students_dir', 'data/3.20_physics/question_13/students',
        '--rubric_file', 'data/3.20_physics/reference_rubric.json',
        '--output_dir', 'outputs/batch_results/conn_probe/question_13',
        '--db_path', 'outputs/grading_database.db',
        '--concurrency', '8'
    ], capture_output=True, text=True, cwd='E:\\ai批改\\homework_grader_system', timeout=300)
    print("STDOUT:")
    print(result.stdout)
    if result.stderr:
        print("STDERR:")
        print(result.stderr)
    print(f"Exit code: {result.returncode}\n")
except subprocess.TimeoutExpired:
    print("TIMEOUT: Command exceeded 300 seconds\n")
except Exception as e:
    print(f"Error executing command 2: {e}\n")

print("=" * 100)
print("COMMAND 4a: Parse question_13 summary.csv and count Error_Status")
print("=" * 100)
try:
    csv_file = 'E:\\ai批改\\homework_grader_system\\outputs\\batch_results\\conn_probe\\question_13\\summary.csv'
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
            print("Error_Status value counts:")
            for status in sorted(error_counts.keys()):
                count = error_counts[status]
                print(f"  {status}: {count}")
    else:
        print(f"File not found: {csv_file}")
except Exception as e:
    print(f"Error parsing question_13 summary.csv: {e}")
print()

print("=" * 100)
print("COMMAND 3: Batch grade question_05")
print("=" * 100)
try:
    result = subprocess.run([
        sys.executable, 'scripts/batch_grade.py',
        '--students_dir', 'data/3.20_physics/question_05/students',
        '--rubric_file', 'data/3.20_physics/reference_rubric.json',
        '--output_dir', 'outputs/batch_results/conn_probe/question_05',
        '--db_path', 'outputs/grading_database.db',
        '--concurrency', '8'
    ], capture_output=True, text=True, cwd='E:\\ai批改\\homework_grader_system', timeout=300)
    print("STDOUT:")
    print(result.stdout)
    if result.stderr:
        print("STDERR:")
        print(result.stderr)
    print(f"Exit code: {result.returncode}\n")
except subprocess.TimeoutExpired:
    print("TIMEOUT: Command exceeded 300 seconds\n")
except Exception as e:
    print(f"Error executing command 3: {e}\n")

print("=" * 100)
print("COMMAND 4b: Parse question_05 summary.csv and count Error_Status")
print("=" * 100)
try:
    csv_file = 'E:\\ai批改\\homework_grader_system\\outputs\\batch_results\\conn_probe\\question_05\\summary.csv'
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
            print("Error_Status value counts:")
            for status in sorted(error_counts.keys()):
                count = error_counts[status]
                print(f"  {status}: {count}")
    else:
        print(f"File not found: {csv_file}")
except Exception as e:
    print(f"Error parsing question_05 summary.csv: {e}")
print()

print("=" * 100)
print("COMMAND 5: Search connection error handling in deepseek_engine.py")
print("=" * 100)
try:
    search_file = 'E:\\ai批改\\homework_grader_system\\src\\cognitive\\engines\\deepseek_engine.py'
    if Path(search_file).exists():
        with open(search_file, 'r', encoding='utf-8') as f:
            content = f.read()
            lines = content.split('\n')
            
            # Search for connection-related patterns
            patterns = ['connection', 'timeout', 'error', 'except', 'retry', 'ConnectionError', 'circuit', 'failover']
            matching_lines = []
            
            for i, line in enumerate(lines, 1):
                for pattern in patterns:
                    if pattern.lower() in line.lower():
                        matching_lines.append((i, line))
                        break
            
            print(f"Found {len(matching_lines)} lines with connection error handling patterns:\n")
            for line_num, line in matching_lines:
                print(f"{line_num:3d}: {line}")
    else:
        print(f"File not found: {search_file}")
except Exception as e:
    print(f"Error searching deepseek_engine.py: {e}")
print()

print("=" * 100)
print("COMMAND 6: Search connection error handling in qwen_engine.py")
print("=" * 100)
try:
    search_file = 'E:\\ai批改\\homework_grader_system\\src\\perception\\engines\\qwen_engine.py'
    if Path(search_file).exists():
        with open(search_file, 'r', encoding='utf-8') as f:
            content = f.read()
            lines = content.split('\n')
            
            # Search for connection-related patterns
            patterns = ['connection', 'timeout', 'error', 'except', 'retry', 'ConnectionError', 'circuit', 'failover']
            matching_lines = []
            
            for i, line in enumerate(lines, 1):
                for pattern in patterns:
                    if pattern.lower() in line.lower():
                        matching_lines.append((i, line))
                        break
            
            print(f"Found {len(matching_lines)} lines with connection error handling patterns:\n")
            for line_num, line in matching_lines:
                print(f"{line_num:3d}: {line}")
    else:
        print(f"File not found: {search_file}")
except Exception as e:
    print(f"Error searching qwen_engine.py: {e}")
print()

print("=" * 100)
print("ALL COMMANDS COMPLETE")
print("=" * 100)
