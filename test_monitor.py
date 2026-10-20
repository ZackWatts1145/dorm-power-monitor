#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""monitor.py 的自测，不需要真实 key。

    python test_monitor.py

推送通道用本地 mock 服务器验证请求体；业务逻辑用临时目录隔离。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import monitor  # noqa: E402

monitor.LOG_PATH = os.path.join(tempfile.mkdtemp(prefix="pm_log_"), "monitor.log")
_ORIG_RESOLVE_ROOM = monitor.resolve_room

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((PASS if cond else FAIL, name, detail))


# --------------------------------------------------------------- mock server

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        size = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(size)
        self.server.seen.append({
            "path": self.path,
            "body": body,
            "ctype": self.headers.get("Content-Type", ""),
            "ua": self.headers.get("User-Agent", ""),
        })
        if self.path.startswith("/pushplus"):
            resp = {"code": 200, "msg": "ok"}
        elif self.path.startswith("/wecom"):
            resp = {"errcode": 0, "errmsg": "ok"}
        else:
            resp = {"code": 0, "message": "", "data": {"error": "SUCCESS"}}
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.seen = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


# --------------------------------------------------------------- 通道

def test_channels() -> None:
    srv = start_server()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    title, body = "电费余额不足 · N0000", "剩余电量: 5.40 度\n剩余金额: ¥2.70"

    cfg = {"notify": {"channel": "pushplus", "pushplus_token": "tok_abc",
                      "pushplus_url": f"{base}/pushplus"}}
    ok = monitor.notify(cfg, title, body)
    payload = json.loads(srv.seen[-1]["body"])
    check("pushplus 成功", ok is True)
    check("pushplus token", payload.get("token") == "tok_abc")
    check("pushplus 标题正文", payload.get("title") == title and "5.40" in payload.get("content", ""))
    check("pushplus 浏览器 UA", "AppleWebKit" in srv.seen[-1]["ua"])

    cfg = {"notify": {"channel": "serverchan", "serverchan_key": "SCT123",
                      "serverchan_url": f"{base}/SCT123.send"}}
    ok = monitor.notify(cfg, title, body)
    form = parse_qs(srv.seen[-1]["body"].decode())
    check("serverchan 成功", ok is True)
    check("serverchan 路径", srv.seen[-1]["path"] == "/SCT123.send")
    check("serverchan 参数", form.get("title", [""])[0] == title and "5.40" in form.get("desp", [""])[0])
    check("serverchan 浏览器 UA", "AppleWebKit" in srv.seen[-1]["ua"])

    cfg = {"notify": {"channel": "wecom", "wecom_webhook": f"{base}/wecom"}}
    ok = monitor.notify(cfg, title, body)
    payload = json.loads(srv.seen[-1]["body"])
    check("wecom 成功", ok is True)
    check("wecom markdown", payload.get("msgtype") == "markdown"
          and title in payload.get("markdown", {}).get("content", ""))

    toasts: list[str] = []
    monitor._toast = lambda t, b: (toasts.append(t), True)[1]           # type: ignore

    cfg = {"notify": {"channel": "wecom", "wecom_webhook": f"http://127.0.0.1:{closed_port()}/x"}}
    ok = monitor.notify(cfg, title, body)
    check("连接失败返回 False", ok is False)
    check("推送失败回落桌面通知", len(toasts) == 1)

    cfg = {"notify": {"channel": "serverchan", "serverchan_key": ""}}
    ok = monitor.notify(cfg, title, body)
    check("缺 key 不请求且回落", ok is False and len(toasts) == 2)

    check("auto 无凭据 -> toast", monitor._pick_channel({"notify": {"channel": "auto"}}) == "toast")
    check("auto 有 sendkey -> serverchan",
          monitor._pick_channel({"notify": {"channel": "auto", "serverchan_key": "x"}}) == "serverchan")

    check("Turbo 地址", monitor._serverchan_url("SCT123abc") == "https://sctapi.ftqq.com/SCT123abc.send")
    check("Server酱³ 地址",
          monitor._serverchan_url("sctp8890tABC") == "https://8890.push.ft07.com/send/sctp8890tABC.send")

    srv.shutdown()


# --------------------------------------------------------------- 业务

def test_logic() -> None:
    tmp = tempfile.mkdtemp(prefix="pm_")
    monitor.CONFIG_PATH = os.path.join(tmp, "config.json")
    monitor.STATE_PATH = os.path.join(tmp, "state.json")
    monitor.HISTORY_PATH = os.path.join(tmp, "history.csv")
    monitor.LOG_PATH = os.path.join(tmp, "monitor.log")

    cfg = {"base_url": "https://example.invalid",
           "room": {"code": "N0000"},
           "threshold": {"energy_kwh": 20, "money_yuan": 0},
           "notify": {"channel": "console"},
           "alert": {"cooldown_hours": 12, "heartbeat_failures": 3}}
    with open(monitor.CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)

    calls: list[tuple[str, str]] = []
    monitor.notify = lambda c, t, b: (calls.append((t, b)), True)[1]    # type: ignore
    room = {"roomId": 1, "roomCode": "N0000", "equipId": 2, "buildingName": "测试楼"}
    monitor.resolve_room = lambda c: room                              # type: ignore

    def reading(energy, money=None):
        return {"energy": energy, "money": money, "meterId": 2, "reportStatus": 0,
                "ts": datetime.now(monitor.CST).isoformat(timespec="seconds")}

    # 1. 高于阈值：静默
    monitor.fetch_balance = lambda c, r: reading(80.0, 40.0)           # type: ignore
    rc = monitor.main()
    check("高于阈值返回 0", rc == 0, f"rc={rc}")
    check("高于阈值不通知", not calls)

    # 2. --test 不受阈值限制
    calls.clear()
    argv = sys.argv
    sys.argv = ["monitor.py", "--test"]
    try:
        rc = monitor.main()
    finally:
        sys.argv = argv
    check("--test 高于阈值也发送", rc == 0 and len(calls) == 1, f"rc={rc} calls={len(calls)}")
    check("--test 标题带测试前缀", calls[0][0].startswith("【测试】"), calls[0][0])
    calls.clear()

    # 3. 低于阈值：通知一次
    monitor.fetch_balance = lambda c, r: reading(5.0, 2.5)             # type: ignore
    rc = monitor.main()
    check("低于阈值返回 1", rc == 1)
    check("低于阈值发一条", len(calls) == 1, f"calls={len(calls)}")
    check("通知含房间与读数", "N0000" in calls[0][0] and "5.00" in calls[0][1])

    # 4. 冷却
    monitor.main()
    check("冷却期内不重复", len(calls) == 1, f"calls={len(calls)}")

    # 5. 充值后重置冷却
    monitor.fetch_balance = lambda c, r: reading(100.0, 50.0)          # type: ignore
    monitor.main()
    state = json.load(open(monitor.STATE_PATH, encoding="utf-8"))
    check("充值后清空冷却", state.get("last_alert") is None, str(state.get("last_alert")))
    monitor.fetch_balance = lambda c, r: reading(5.0, 2.5)             # type: ignore
    monitor.main()
    check("充值后再低电量立即告警", len(calls) == 2, f"calls={len(calls)}")

    # 6. 掉线告警：连续 3 次失败只发 1 条
    calls.clear()
    state = json.load(open(monitor.STATE_PATH, encoding="utf-8"))
    state["last_alert"] = (datetime.now(monitor.CST) - timedelta(hours=13)).isoformat(timespec="seconds")
    json.dump(state, open(monitor.STATE_PATH, "w", encoding="utf-8"))

    def boom(_c, _r):
        raise ValueError("connection refused")

    monitor.fetch_balance = boom                                       # type: ignore
    codes = [monitor.main() for _ in range(3)]
    check("查询失败返回 2", codes == [2, 2, 2], str(codes))
    check("第 3 次失败才告警", len(calls) == 1, f"calls={len(calls)}")
    check("掉线通知标题", calls and "掉线" in calls[0][0], calls[0][0] if calls else "")

    # 7. 趋势
    now = datetime.now(monitor.CST)
    hist = sorted([(now - timedelta(days=d), 15 - 2 * (5 - d)) for d in range(6)])
    est = monitor.estimate_usage(hist)
    check("日均消耗", est["per_day"] == 2.0, str(est))
    check("可用天数", est["days_left"] == 2.5, str(est))


def test_resolve() -> None:
    monitor.resolve_room = _ORIG_RESOLVE_ROOM                          # 还原被 test_logic 替换的实现
    monitor.http_get = lambda base, path, params, timeout=20: {        # type: ignore
        "list": [
            {"id": 99, "roomCode": "A101", "equipId": 7, "buildingName": "一号楼"},
            {"id": 100, "roomCode": "n0000", "equipId": 8, "buildingName": "测试楼"},
        ]
    }
    room = monitor.resolve_room({"base_url": "https://x", "room": {"code": "N0000"}})
    check("房间号大小写不敏感", room["roomId"] == 100, str(room))
    check("带回楼栋与电表", room["buildingName"] == "测试楼" and room["equipId"] == 8)

    monitor.http_get = lambda base, path, params, timeout=20: {"list": []}   # type: ignore
    try:
        monitor.resolve_room({"base_url": "https://x", "room": {"code": "ZZZ"}})
        check("找不到房间应报错", False)
    except LookupError as exc:
        check("找不到房间应报错", "ZZZ" in str(exc), str(exc))


def main() -> int:
    test_channels()
    test_logic()
    test_resolve()
    width = max(len(n) for _, n, _ in results)
    for status, name, detail in results:
        line = f"[{status}] {name.ljust(width)}"
        if detail and status == FAIL:
            line += f"  <- {detail}"
        print(line)
    failed = sum(1 for s, _, _ in results if s == FAIL)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
