from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

def _parse_metrics(text: str):
    data = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) == 2:
            data[parts[0]] = float(parts[1])
    return data


def test_metrics_counts_increment():
    # capture baseline
    r1 = client.get("/metrics")
    assert r1.status_code == 200
    before = _parse_metrics(r1.text).get("odin_requests_total", 0)

    # perform a request that should be counted
    env = {
        "payload": {"a": "b"},
        "payload_type": "foo.bar.v1",
        "target_type": "foo.bar.v1",
    }
    r_env = client.post("/v1/odin/envelope", json=env)
    assert r_env.status_code == 200

    r2 = client.get("/metrics")
    assert r2.status_code == 200
    after = _parse_metrics(r2.text).get("odin_requests_total", 0)
    assert after >= before + 1
    # histogram count consistency
    metrics_lines = [
        line
        for line in r2.text.splitlines()
        if line.startswith("odin_request_latency_seconds")
    ]
    assert any("_bucket" in line for line in metrics_lines)
    assert any(line.startswith("odin_request_latency_seconds_sum") for line in metrics_lines)
    assert any(line.startswith("odin_request_latency_seconds_count") for line in metrics_lines)
