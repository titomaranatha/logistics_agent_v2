"""locust -f tests/load/locustfile.py --host http://localhost:8000   (set LOAD_TOKEN to an operator JWT)"""
import os

from locust import HttpUser, between, task


class Operator(HttpUser):
    wait_time = between(0.05, 0.3)

    def on_start(self):
        self.h = {"Authorization": f"Bearer {os.environ['LOAD_TOKEN']}"}

    @task(8)
    def chat_inventory(self):
        self.client.post("/chat", json={"message": "stock for SKU-1001?"}, headers=self.h, name="/chat")

    @task(2)
    def proposals(self):
        self.client.get("/proposals", headers=self.h, name="/proposals")

    @task(1)
    def ready(self):
        self.client.get("/health/ready", name="/health/ready")
