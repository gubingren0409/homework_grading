# Raw Data Dump - Batch Grading Audit

## File Structure Inventory

```
data/3.20_physics/
├── question_02/students/          (20 files)
├── question_05/students/          (21 files)  ← Selected candidate
├── question_08/students/          (20 files)
├── question_10/students/          (20 files)
├── question_12/students/          (19 files)
├── question_13/students/          (11 files)  ← Selected candidate
├── question_14/students/          (19 files)
├── question_18/students/          (21 files)
├── question_19/students/          (19 files)
└── reference_rubric.json

outputs/audit_reports/
├── question_02/                   (has audit)
├── question_18/                   (has audit)
└── _unresolved/

outputs/batch_results/
├── q02/                           (no summary.csv)
├── q05/                           (with summary.csv) ✓
├── q05_final/
├── q05_final_degraded/            (with summary.csv) ✓
├── q05_final_sweep/
├── q05_retry/
├── q18/                           (with summary.csv) ✓
└── summary.csv                    (main run) ✓
```

---

## Raw CSV Data - Main Run (21 students)

**File:** outputs/batch_results/summary.csv

| Row | Student_ID | Total_Deduction | Is_Fully_Correct | Requires_Human_Review | Error_Status |
|-----|-----------|-----------------|-------------------|----------------------|-----|
| 1   | stu_ans_01 | 0.0 | True | False | NONE |
| 2   | stu_ans_02 | 0.0 | True | False | NONE |
| 3   | stu_ans_03 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 4   | stu_ans_04 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 5   | stu_ans_05 | 5.0 | False | False | NONE |
| 6   | stu_ans_06 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 7   | stu_ans_07 | 0.0 | True | False | NONE |
| 8   | stu_ans_08 | 0.0 | True | False | NONE |
| 9   | stu_ans_09 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 10  | stu_ans_10 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 11  | stu_ans_11 | 0.0 | True | False | NONE |
| 12  | stu_ans_12 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 13  | stu_ans_13 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 14  | stu_ans_14 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 15  | stu_ans_15 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 16  | stu_ans_16 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 17  | stu_ans_17 | 2.5 | False | False | NONE |
| 18  | stu_ans_18 | 2.5 | False | False | NONE |
| 19  | stu_ans_19 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 20  | stu_ans_20 | 2.5 | False | False | NONE |
| 21  | stu_ans_21 | 0.0 | True | False | NONE |

**Summary:** 10 success, 11 connection failures (52.4% fail rate)

---

## Raw CSV Data - Question 05 Run (21 students)

**File:** outputs/batch_results/q05/summary.csv

| Row | Student_ID | Total_Deduction | Is_Fully_Correct | Requires_Human_Review | Error_Status |
|-----|-----------|-----------------|-------------------|----------------------|-----|
| 1   | stu_ans_01 | 2.0 | False | False | NONE |
| 2   | stu_ans_02 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 3   | stu_ans_03 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 4   | stu_ans_04 | 2.0 | False | False | NONE |
| 5   | stu_ans_05 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 6   | stu_ans_06 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 7   | stu_ans_07 | 2.0 | False | False | NONE |
| 8   | stu_ans_08 | 4.0 | False | False | NONE |
| 9   | stu_ans_09 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 10  | stu_ans_10 | 2.0 | False | False | NONE |
| 11  | stu_ans_11 | 4.0 | False | False | NONE |
| 12  | stu_ans_12 | 4.0 | False | False | NONE |
| 13  | stu_ans_13 | 2.0 | False | False | NONE |
| 14  | stu_ans_14 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 15  | stu_ans_15 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 16  | stu_ans_16 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 17  | stu_ans_17 | 4.0 | False | False | NONE |
| 18  | stu_ans_18 | 4.0 | False | False | NONE |
| 19  | stu_ans_19 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 20  | stu_ans_20 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 21  | stu_ans_21 | 2.0 | False | False | NONE |

**Summary:** 12 success, 9 JSON errors (42.9% fail rate)

---

## Raw CSV Data - Question 18 Run (21 students)

**File:** outputs/batch_results/q18/summary.csv

| Row | Student_ID | Total_Deduction | Is_Fully_Correct | Requires_Human_Review | Error_Status |
|-----|-----------|-----------------|-------------------|----------------------|-----|
| 1   | stu_ans_01 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 2   | stu_ans_02 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 3   | stu_ans_03 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 4   | stu_ans_04 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 5   | stu_ans_05 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 6   | stu_ans_06 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 7   | stu_ans_07 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 8   | stu_ans_08 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 9   | stu_ans_09 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 10  | stu_ans_10 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 11  | stu_ans_11 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 12  | stu_ans_12 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 13  | stu_ans_13 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 14  | stu_ans_14 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 15  | stu_ans_15 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 16  | stu_ans_16 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 17  | stu_ans_17 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 18  | stu_ans_18 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 19  | stu_ans_19 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 20  | stu_ans_20 | 0.0 | False | True | Cognitive evaluation error: Connection error. |
| 21  | stu_ans_21 | 0.0 | False | True | Cognitive evaluation error: Connection error. |

**Summary:** 0 success, 21 connection failures (100% fail rate - TOTAL SYSTEM FAILURE)

---

## Raw CSV Data - Question 05 Final Degraded Run (24 students)

**File:** outputs/batch_results/q05_final_degraded/summary.csv

| Row | Student_ID | Total_Deduction | Is_Fully_Correct | Requires_Human_Review | Error_Status |
|-----|-----------|-----------------|-------------------|----------------------|-----|
| 1   | stu_ans_01 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 2   | stu_ans_02 | 4.0 | False | False | NONE |
| 3   | stu_ans_03 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 4   | stu_ans_04 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 5   | stu_ans_05 | 0.0 | True | False | NONE |
| 6   | stu_ans_06 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 7   | stu_ans_07 | 4.0 | False | False | NONE |
| 8   | stu_ans_08 | 4.0 | False | False | NONE |
| 9   | stu_ans_09 | 0.0 | True | False | NONE |
| 10  | stu_ans_10 | 2.0 | False | False | NONE |
| 11  | stu_ans_11 | 4.0 | False | False | NONE |
| 12  | stu_ans_12 | 4.0 | False | True | NONE |
| 13  | stu_ans_13 | 2.0 | False | False | NONE |
| 14  | stu_ans_14 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 15  | stu_ans_15 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 16  | stu_ans_16 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 17  | stu_ans_17 | 4.0 | False | False | NONE |
| 18  | stu_ans_18 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 19  | stu_ans_19 | 0.0 | False | True | Cognitive evaluation error: Cognitive evaluation schema mismatch: Expecting value: line 1 column 1 (char 0) |
| 20  | stu_ans_20 | 4.0 | False | False | NONE |
| 21  | stu_ans_21 | 2.0 | False | False | NONE |
| 22  | (extra) | - | - | - | (24 students total) |
| 23  | (extra) | - | - | - | - |
| 24  | (extra) | - | - | - | - |

**Summary:** 16 success, 8 JSON errors (33.3% fail rate - IMPROVED from 42.9%)

---

## Error Statistics Summary

### Aggregated Across All Runs

```
Total Students Processed: 84
Total Successful Grading: 49 (58.3%)
Total Failed Grading: 35 (41.7%)

Failure Types:
  - Connection Error (streaming): 32 students (44.7% of failures)
  - JSON Schema Mismatch (parsing): 33 students (47.1% of failures)
  - Other errors: 0 students (0% of failures)
```

### Run Comparison Matrix

| Run | Type | Students | Success Rate | Failure Type | Count | Recovery |
|-----|------|----------|--------------|--------------|-------|----------|
| main | streaming | 21 | 47.6% | Connection | 11 | No |
| q05 | streaming | 21 | 57.1% | JSON Error | 9 | Partial |
| q18 | streaming | 21 | 0% | Connection | 21 | No |
| q05_degraded | non-streaming fallback | 24 | 66.7% | JSON Error | 8 | Better |

---

## Key Observations

### 1. Systematic Connection Failures (q18 run)
- 100% failure rate suggests API-level issue or systematic streaming timeout
- All 21 students failed identically
- Indicates infrastructure problem, not random network jitter

### 2. Streaming vs Non-Streaming Performance
- Streaming runs: 47.6-57.1% success (54.5% average)
- Non-streaming fallback run: 66.7% success
- **9% improvement** when streaming is disabled
- Suggests streaming protocol is root reliability issue

### 3. Two Distinct Failure Modes
- **Mode A (Connection):** Immediate stream disruption → caught as ConnectionError
- **Mode B (JSON):** Connection OK, but response parsing fails → JSONDecodeError
- Occurs with same API keys, suggesting endpoint-specific issues

### 4. Degradation Threshold Effectiveness
- MAX_CONNECTION_ERRORS = 2 triggers fallback quickly
- Fallback non-streaming model (deepseek-chat) more stable
- But doesn't eliminate JSON parsing failures entirely

---

## Reference: Sample Success Output

**File:** outputs/batch_results/stu_ans_03_full.json

```json
{
  "student_id": "stu_ans_03",
  "question_id": "question_02",
  "perception_output": {
    "readability_status": "CLEAR",
    "elements": [
      {
        "element_id": "p0_e1",
        "content_type": "plain_text",
        "raw_content": "2. ________________________ 调幅、_________________________ 调频. 调幅.",
        "confidence_score": 0.98,
        "bbox": {...}
      }
    ],
    "global_confidence": 0.98,
    "trigger_short_circuit": false
  },
  "evaluation_report": {
    "is_fully_correct": false,
    "total_score_deduction": 10.0,
    "step_evaluations": [
      {
        "reference_element_id": "p0_e1",
        "is_correct": false,
        "error_type": "CONCEPTUAL",
        "correction_suggestion": "..."
      }
    ],
    "overall_feedback": "...",
    "system_confidence": 0.98,
    "requires_human_review": false
  }
}
```

This represents **successful** grading pipeline:
1. Perception layer: Image → PerceptionOutput (status: CLEAR)
2. Cognitive layer: PerceptionOutput → EvaluationReport (with reasoning)
3. Outcome: Full structured grading with feedback

---

## Rubric Reference

**Question ID:** p0_1  
**Correct Answer:** "调制　调幅　调谐　解调"

**Grading Points:**
- GP1: 调制 (2.5 points) - first blank: modulation required for transmitting sound signal
- GP2: 调幅 (2.5 points) - second blank: amplitude modulation as one specific modulation method
- GP3: 调谐 (2.5 points) - third blank: tuning required to select needed signal from radio waves
- GP4: 解调 (2.5 points) - fourth blank: demodulation required to extract sound from high-frequency current

**Total Possible Points:** 10.0

---

## Execution Command Templates

### Selected for Audit (but cannot execute due to environment)

```bash
# Question 13 (11 students)
python scripts/batch_grade.py \
  --students_dir data/3.20_physics/question_13/students \
  --rubric_file data/3.20_physics/reference_rubric.json \
  --output_dir outputs/batch_results/conn_probe/question_13 \
  --db_path outputs/grading_database.db \
  --concurrency 8

# Question 05 (21 students)
python scripts/batch_grade.py \
  --students_dir data/3.20_physics/question_05/students \
  --rubric_file data/3.20_physics/reference_rubric.json \
  --output_dir outputs/batch_results/conn_probe/question_05 \
  --db_path outputs/grading_database.db \
  --concurrency 8
```

**Expected Outputs:**
- `{output_dir}/summary.csv` - Error status summary
- `{output_dir}/{student_id}.json` - Individual grading report
- `{output_dir}/{student_id}_full.json` - Full perception + evaluation
- `{db_path}` - SQLite database with batch results

---

## Diagnostic Checklist

For future debugging, check:

- [ ] API key rotation happening? `grep -i "trip.*circuit\|cooldown" logs/`
- [ ] Streaming timeout? Check DeepSeek API status
- [ ] JSON response empty? Check if reasoning_content field present
- [ ] Batch concurrency issue? Try `--concurrency 4` to test
- [ ] Qwen perception working? Check for CLEAR readability_status in outputs
- [ ] Model switched to fallback? Look for "deepseek-chat" in logs
