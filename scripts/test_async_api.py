import time
import requests
import sys
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000/api/v1"

def test_async_workflow():
    # 1. Prepare test file
    test_file_path = Path("data/3.20_physics/question_02/students/stu_ans_01/stu_ans_01.png")
    if not test_file_path.exists():
        # Try fallback if exact path is different
        test_file_path = next(Path("data/3.20_physics/question_02/students").rglob("*.png"))
    
    print(f"[探针] 正在测试文件: {test_file_path}")

    # 2. Submit Task
    with open(test_file_path, "rb") as f:
        files = [("files", (test_file_path.name, f, "image/png"))]
        response = requests.post(f"{BASE_URL}/grade/submit", files=files)
    
    if response.status_code != 202:
        print(f"[错误] 提交失败: {response.status_code} - {response.text}")
        sys.exit(1)
    
    data = response.json()
    task_id = data["task_id"]
    print(f"[探针] 任务已提交: task_id={task_id}, status={data['status']}")

    # 3. Poll Status
    max_retries = 120  # 10 minutes max
    attempt = 0
    while attempt < max_retries * 5: # check every 5s for 10 min
        poll_resp = requests.get(f"{BASE_URL}/grade/{task_id}")
        if poll_resp.status_code not in [200, 206]:
            print(f"[错误] 轮询异常: {poll_resp.status_code} - {poll_resp.text}")
            sys.exit(1)
        
        status_data = poll_resp.json()
        current_status = status_data["status"]
        print(f"[探针] 轮询中 ({attempt}s): {current_status}")

        if current_status == "COMPLETED":
            print("[探针] 任务成功完成！")
            results = status_data.get("results", [])
            print(f"[探针] 结果摘要: 样本数={len(results)}, 状态码=200")
            if results:
                report = results[0].get("report_json", {})
                print(f"[数据] 最终得分扣除: {report.get('evaluation_report', {}).get('total_score_deduction', 'N/A')}")
            return True
        elif current_status == "FAILED":
            print(f"[错误] 任务失败: {status_data.get('error_message')}")
            sys.exit(1)
        
        time.sleep(5)
        attempt += 5
    
    print("[错误] 任务超时！")
    sys.exit(1)

if __name__ == "__main__":
    try:
        test_async_workflow()
    except Exception as e:
        print(f"[异常] 脚本运行出错: {e}")
        sys.exit(1)
