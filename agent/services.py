import time

import httpx


class ExternalServiceError(RuntimeError):
    """Safe-to-show failure of an external dependency."""


class CarrierClient:
    """Carrier API: timeout, retry with backoff, circuit breaker. Simulated when no base_url is set."""
    def __init__(self, base_url="", api_key="", retries=3, sleep=time.sleep):
        self.base_url, self.api_key, self.retries, self.sleep = base_url, api_key, retries, sleep
        self.failures, self.open_until = 0, 0.0

    def track(self, tracking_no):
        if not self.base_url:
            return {"tracking_no": tracking_no, "status": "IN_TRANSIT", "eta": "2026-11-12",
                    "last_scan": "Lagos Hub", "simulated": True}
        if time.time() < self.open_until:
            raise ExternalServiceError("Carrier API temporarily unavailable (circuit open)")
        err = None
        for attempt in range(self.retries):
            try:
                r = httpx.get(f"{self.base_url}/track/{tracking_no}", timeout=8,
                              headers={"Authorization": f"Bearer {self.api_key}"})
                if r.status_code == 404:
                    return {"tracking_no": tracking_no, "status": "NOT_FOUND"}
                r.raise_for_status()
                self.failures = 0
                return r.json()
            except httpx.HTTPError as e:
                err = e
                self.sleep(0.5 * 2 ** attempt)
        self.failures += 1
        if self.failures >= 3:
            self.open_until = time.time() + 30
        raise ExternalServiceError(f"Carrier API failed after {self.retries} attempts ({type(err).__name__})")
