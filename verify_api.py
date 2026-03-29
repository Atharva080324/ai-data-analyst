import requests
import json
import time

BASE_URL = "http://127.0.0.1:8000"

def test_health():
    response = requests.get(f"{BASE_URL}/health")
    print(f"Health Check: {response.json()}")

def test_upload():
    # Note: This requires a valid token which we don't have easily in a script
    # but we can try to hit the endpoint to see the 401 response at least.
    response = requests.post(f"{BASE_URL}/datasets/upload")
    print(f"Upload Check (Expect 401/422): {response.status_code}")

if __name__ == "__main__":
    test_health()
    test_upload()
