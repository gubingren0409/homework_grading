"""One-shot E2E test: submit physics grading task and monitor progress."""
import httpx, os, time, json

BASE = "http://localhost:8000/api/v1"
UPLOAD_DIR = "data/uploads/bef80614-daed-4d80-92d0-7a58e010f25c"

ref_files = sorted([f for f in os.listdir(UPLOAD_DIR) if f.startswith("reference_")])
stu_files = sorted([f for f in os.listdir(UPLOAD_DIR) if f.startswith("stu_ans_")])
print(f"References: {len(ref_files)}, Students: {len(stu_files)}")

files_list = []
for rf in ref_files:
    path = os.path.join(UPLOAD_DIR, rf)
    files_list.append(("reference_files", (rf, open(path, "rb"), "image/png")))
for sf in stu_files:
    path = os.path.join(UPLOAD_DIR, sf)
    files_list.append(("files", (sf, open(path, "rb"), "image/png")))

print("Submitting task...")
with httpx.Client(timeout=30) as client:
    resp = client.post(
        f"{BASE}/grade/submit-batch-with-reference",
        files=files_list,
        headers={"X-Teacher-Id": "test-teacher-e2e"},
    )
    print(f"HTTP {resp.status_code}")
    if resp.status_code != 202:
        print(resp.text[:500])
        exit(1)
    data = resp.json()
    task_id = data["task_id"]
    print(f"Task ID: {task_id}")
    print(f"Mode: {data.get('mode')}, Submitted: {data.get('submitted_count')}")

for _, (_, fh, _) in files_list:
    fh.close()

# Poll progress
print("\n--- Polling progress ---")
with httpx.Client(timeout=10) as client:
    for i in range(120):  # up to 10 minutes
        time.sleep(5)
        try:
            r = client.get(f"{BASE}/grade/{task_id}")
            d = r.json()
            status = d.get("status", "?")
            progress = d.get("progress", 0)
            graded = d.get("graded_count", 0)
            total = d.get("total_students", "?")
            eta = d.get("eta_seconds", "?")
            print(f"[{i*5:>4}s] {status:12} prog={progress:.0%}  graded={graded}/{total}  eta={eta}s")
            if status in ("COMPLETED", "FAILED"):
                if status == "COMPLETED":
                    print("\n=== SUCCESS ===")
                else:
                    print(f"\n=== FAILED === error: {d.get('error', '?')}")
                break
        except Exception as e:
            print(f"[{i*5:>4}s] poll error: {e}")
    else:
        print("\n=== TIMEOUT after 10 minutes ===")
