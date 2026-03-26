import asyncio
import httpx
import time
import sys
from pathlib import Path
from typing import List, Dict, Any

BASE_URL = "http://127.0.0.1:8000/api/v1"
STUDENTS_DIR = Path("data/3.20_physics/question_02/students")

class AsyncLoadTester:
    def __init__(self):
        self.task_ids = []
        self.stats = {
            "total_submitted": 0,
            "trigger_429_count": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
            "start_time": 0.0,
            "end_time": 0.0
        }

    async def submit_with_retry(self, client: httpx.AsyncClient, student_id: str, files: list):
        """Submits a single student with backoff on 429."""
        attempt = 0
        while True:
            try:
                # Re-create the files for each retry because httpx consumes the streams
                # Note: For production load tests we should read into memory once.
                # Since these are small images, we reread from disk or use bytes.
                
                resp = await client.post(f"{BASE_URL}/grade/submit", files=files)
                
                if resp.status_code == 202:
                    data = resp.json()
                    tid = data["task_id"]
                    self.task_ids.append(tid)
                    self.stats["total_submitted"] += 1
                    return tid
                
                if resp.status_code == 429:
                    self.stats["trigger_429_count"] += 1
                    # Hard backoff as requested: 10 seconds fixed or exponential
                    await asyncio.sleep(12) 
                    continue
                
                print(f"[ERROR] Student {student_id} submission failed: {resp.status_code}")
                return None

            except Exception as e:
                print(f"[EXCEPTION] {student_id}: {e}")
                await asyncio.sleep(2)
                continue

    async def poll_task(self, client: httpx.AsyncClient, task_id: str):
        """Polls a single task until final state."""
        while True:
            try:
                resp = await client.get(f"{BASE_URL}/grade/{task_id}")
                
                if resp.status_code == 429:
                    self.stats["trigger_429_count"] += 1
                    await asyncio.sleep(5)
                    continue
                
                if resp.status_code == 200:
                    data = resp.json()
                    status = data["status"]
                    if status == "COMPLETED":
                        self.stats["completed_tasks"] += 1
                        return True
                    if status == "FAILED":
                        self.stats["failed_tasks"] += 1
                        return False
                
                await asyncio.sleep(10) # Wait 10s between polls
            except Exception:
                await asyncio.sleep(5)

    async def run(self):
        self.stats["start_time"] = time.time()
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Parallel Submission
            print(f"[LOAD TEST] Starting submission for all students in {STUDENTS_DIR}...")
            submission_tasks = []
            
            # Support both directories and direct files
            extensions = {".png", ".jpg", ".jpeg"}
            for student_path in STUDENTS_DIR.iterdir():
                if student_path.is_dir():
                    student_id = student_path.name
                    img_files = [f for f in student_path.iterdir() if f.suffix.lower() in extensions]
                    if not img_files: continue
                    
                    payload = []
                    for img in img_files:
                        payload.append(("files", (img.name, img.read_bytes(), "image/png")))
                    
                    submission_tasks.append(self.submit_with_retry(client, student_id, payload))
                elif student_path.is_file() and student_path.suffix.lower() in extensions:
                    student_id = student_path.stem
                    payload = [("files", (student_path.name, student_path.read_bytes(), "image/png"))]
                    submission_tasks.append(self.submit_with_retry(client, student_id, payload))

            if not submission_tasks:
                print(f"[WARNING] No students found in {STUDENTS_DIR}")
                self.stats["end_time"] = time.time()
                self.report()
                return

            await asyncio.gather(*submission_tasks)
            print(f"[LOAD TEST] All {len(self.task_ids)} tasks submitted.")

            # 2. Parallel Polling
            print("[LOAD TEST] Starting polling for results...")
            polling_tasks = [self.poll_task(client, tid) for tid in self.task_ids]
            await asyncio.gather(*polling_tasks)
            
        self.stats["end_time"] = time.time()
        self.report()

    def report(self):
        total_time = self.stats["end_time"] - self.stats["start_time"]
        print("\n" + "="*40)
        print("ASYNC LOAD TEST FINAL REPORT")
        print("="*40)
        print(f"Total Time: {total_time:.2f} seconds")
        print(f"429 Trigger Count: {self.stats['trigger_429_count']}")
        print(f"Completed Tasks: {self.stats['completed_tasks']}")
        print(f"Failed Tasks: {self.stats['failed_tasks']}")
        print(f"Total Submitted: {self.stats['total_submitted']}")
        if self.stats['total_submitted'] > 0:
            print(f"Success Rate: {(self.stats['completed_tasks']/self.stats['total_submitted'])*100:.1f}%")
        else:
            print("Success Rate: 0.0% (No tasks submitted)")
        print("="*40)

if __name__ == "__main__":
    tester = AsyncLoadTester()
    asyncio.run(tester.run())
