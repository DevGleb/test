#!/usr/bin/env python3
"""End-to-end smoke test against a running docker compose stack."""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import uuid

WALLET = os.environ.get("WALLET_URL", "http://localhost:8000")
PROVIDER = os.environ.get("PROVIDER_URL", "http://localhost:8001")
PROVIDER_API_KEY = os.environ.get("PROVIDER_API_KEY", "dev-provider-api-key")


def call(method: str, url: str, body: dict | None = None, headers: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read() or b"null"), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null"), exc.headers


def create_account(name: str) -> dict:
    out = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "wallet",
            "python",
            "-m",
            "wallet.cli",
            "create-account",
            name,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)


def expect(label: str, actual, expected) -> None:
    status = "ok " if actual == expected else "FAIL"
    print(f"[{status}] {label}: {actual!r}" + ("" if actual == expected else f" != {expected!r}"))
    if actual != expected:
        sys.exit(1)


def balance(account: dict) -> str:
    _, body, _ = call("GET", f"{WALLET}/v1/accounts/me", headers={"X-API-Key": account["api_key"]})
    return body["balance_usd"]


def main() -> None:
    alice = create_account("smoke-alice")
    bob = create_account("smoke-bob")
    alice_h = {"X-API-Key": alice["api_key"]}
    provider_h = {"Authorization": f"Bearer {PROVIDER_API_KEY}"}

    status, deposit, _ = call("POST", f"{WALLET}/v1/deposits", {"amount": "10.00"}, alice_h)
    expect("create deposit", status, 201)
    status, sim, _ = call(
        "POST",
        f"{PROVIDER}/invoices/{deposit['invoice_id']}/simulate",
        {"status": "paid", "deliver_webhook": True, "duplicate": True},
        provider_h,
    )
    expect("simulate paid (duplicate)", status, 200)
    expect("webhook deliveries", sim["webhook"]["deliveries"], [{"status_code": 200}] * 2)
    expect("balance after deposit (10.00 - 1.5% fee)", balance(alice), "9.85")

    status, lost, _ = call("POST", f"{WALLET}/v1/deposits", {"amount": "5.00"}, alice_h)
    call(
        "POST",
        f"{PROVIDER}/invoices/{lost['invoice_id']}/simulate",
        {"status": "paid", "deliver_webhook": False},
        provider_h,
    )
    _, lost_now, _ = call("GET", f"{WALLET}/v1/deposits/{lost['deposit_id']}", headers=alice_h)
    expect("deposit with lost webhook", lost_now["status"], "pending")

    key = uuid.uuid4().hex
    transfer = {"to_account_id": bob["account_id"], "amount": "2.00"}
    h = {**alice_h, "Idempotency-Key": key}
    s1, t1, _ = call("POST", f"{WALLET}/v1/transfers", transfer, h)
    s2, t2, h2 = call("POST", f"{WALLET}/v1/transfers", transfer, h)
    expect("transfer", s1, 201)
    expect("replay returns same transfer", (s2, t2["transfer_id"]), (201, t1["transfer_id"]))
    expect("replay header", h2.get("Idempotent-Replayed"), "true")
    s3, err, _ = call("POST", f"{WALLET}/v1/transfers", {**transfer, "amount": "3.00"}, h)
    expect("same key, different body", (s3, err["error"]["code"]), (409, "idempotency_key_reused"))
    expect("alice after transfer", balance(alice), "7.85")
    expect("bob after transfer", balance(bob), "2.00")

    s4, err, _ = call(
        "POST",
        f"{WALLET}/v1/transfers",
        {"to_account_id": bob["account_id"], "amount": "100.00"},
        {**alice_h, "Idempotency-Key": uuid.uuid4().hex},
    )
    expect("insufficient funds", (s4, err["error"]["code"]), (409, "insufficient_funds"))

    s5, err, _ = call("POST", f"{WALLET}/v1/purchases", {"credits": 507}, alice_h)
    expect(
        "purchase 507 credits with 7.85", (s5, err["error"]["code"]), (409, "insufficient_funds")
    )
    s6, purchase, _ = call("POST", f"{WALLET}/v1/purchases", {"credits": 50}, alice_h)
    expect("purchase 50 credits", (s6, purchase["price_usd"]), (201, "5.00"))
    _, me, _ = call("GET", f"{WALLET}/v1/accounts/me", headers=alice_h)
    expect("balances after purchase", (me["balance_usd"], me["credits"]), ("2.85", 50))

    _, page, _ = call("GET", f"{WALLET}/v1/transactions?limit=2", headers=alice_h)
    expect("history page", [i["type"] for i in page["items"]], ["purchase", "transfer_out"])
    _, rest, _ = call(
        "GET", f"{WALLET}/v1/transactions?limit=2&cursor={page['next_cursor']}", headers=alice_h
    )
    expect("history next page", [i["type"] for i in rest["items"]], ["deposit"])

    print("smoke test passed")


if __name__ == "__main__":
    main()
