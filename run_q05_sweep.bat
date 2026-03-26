@echo off
cd /d E:\ai批改\homework_grader_system
echo [Phase 26.3] Starting q05 final sweep...
python scripts\batch_grade.py --students_dir data\3.20_physics\question_05\students --rubric_file outputs\q5_rubric.json --output_dir outputs\batch_results\q05_sweep --db_path outputs\grading_database.db --concurrency 6

echo.
echo [Stats] Parsing summary.csv...
python -c "import pandas as pd; df=pd.read_csv('outputs/batch_results/q05_sweep/summary.csv'); print(f'SUMMARY_TOTAL={len(df)}'); print(f'SUMMARY_NONE={len(df[df.Error_Status==\"NONE\"])}')"

echo.
echo [Stats] Querying SQLite for question_05...
python -c "import sqlite3; conn=sqlite3.connect('outputs/grading_database.db'); cur=conn.cursor(); cur.execute(\"SELECT COUNT(*) FROM grading_results WHERE question_id='question_05'\"); print(f'DB_Q05_COUNT={cur.fetchone()[0]}'); conn.close()"

echo.
echo [Phase 26.3] Sweep complete. Review stats above.
pause
