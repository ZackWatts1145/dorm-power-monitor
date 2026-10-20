#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宿舍电费监控（通用模板）

按房间号查询剩余电量/金额，低于阈值时推送到微信。只依赖 Python 标准库。

配置见 config.example.json；运行 `python monitor.py --test` 验证通道。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HOME = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("PM_CONFIG") or os.path.join(HOME, "config.json")
STATE_PATH = os.path.join(HOME, "state.json")
HISTORY_PATH = os.path.join(HOME, "history.csv")
LOG_PATH = os.path.join(HOME, "logs", "monitor.log")

CST = timezone(timedelta(hours=8))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

DEFAULT_BASE = "https://jxpt.nxist.com"
PATH_ROOM_LIST = "/api/v1/electricity/WeChat/login/findRoomListPage"
PATH_BALANCE = "/api/v1/electricity/phone/onePassPreChargeSelect"

# ---------------------------------------------------------------- 基础设施


def log(msg: str) -> None:
    line = f"{datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_json(path: str, default: dict) -> dict:
    # utf-8-sig：兼容记事本等编辑器保存时带 BOM 的 config.json
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def http_get(base: str, path: str, params: dict, timeout: float = 20.0) -> dict:
    url = f"{base.rstrip('/')}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read().decode("utf-8", "replace")
    if not raw.strip():
        raise ValueError("接口返回为空")
    return json.loads(raw)


# ---------------------------------------------------------------- 业务查询


def resolve_room(cfg: dict) -> dict:
    """按房间号解析 roomId 等信息。只需要用户填房间号。"""
    code = str((cfg.get("room") or {}).get("code") or "").strip()
    if not code:
        raise LookupError("config.json 里 room.code 为空")
    base = cfg.get("base_url") or DEFAULT_BASE
    data = http_get(base, PATH_ROOM_LIST, {"roomCode": code, "page": 1, "size": 20})
    rows = data.get("list") or []
    hit = next((r for r in rows if str(r.get("roomCode", "")).lower() == code.lower()), None)
    if not hit:
        raise LookupError(f"没找到房间 {code}，请确认房间号与小程序里显示的一致（例如 A101）")
    return {
        "roomId": hit.get("id") or hit.get("roomId"),
        "roomCode": hit.get("roomCode"),
        "equipId": hit.get("equipId"),
        "buildingName": hit.get("buildingName"),
    }


def fetch_balance(cfg: dict, room: dict) -> dict:
    base = cfg.get("base_url") or DEFAULT_BASE
    data = http_get(base, PATH_BALANCE, {
        "roomId": room["roomId"],
        "roomCode": room["roomCode"],
        "type": 0,
    })
    code = data.get("code")
    payload = data.get("data") or {}
    if code not in (200, 2000) or "energy" not in payload:
        msg = data.get("msg") or data.get("message") or "查询失败"
        if code == 5000:
            msg = "设备离线"
        elif code == 2000 and payload.get("reportStatus") == 1:
            msg = "设备查询超时"
        raise ValueError(f"{msg} (code={code})")
    money = payload.get("money")
    return {
        "energy": float(payload.get("energy") or 0),
        "money": float(money) if money is not None else None,
        "meterId": payload.get("meterId"),
        "reportStatus": payload.get("reportStatus"),
        "ts": datetime.now(CST).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------- 趋势


def append_history(reading: dict) -> None:
    is_new = not os.path.exists(HISTORY_PATH)
    os.makedirs(os.path.dirname(HISTORY_PATH) or ".", exist_ok=True)
    with open(HISTORY_PATH, "a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        if is_new:
            writer.writerow(["ts", "energy_kwh", "money_yuan"])
        money = "" if reading["money"] is None else f"{reading['money']:.2f}"
        writer.writerow([reading["ts"], f"{reading['energy']:.3f}", money])


def read_history(days: float = 14.0) -> list[tuple[datetime, float]]:
    if not os.path.exists(HISTORY_PATH):
        return []
    cutoff = datetime.now(CST) - timedelta(days=days)
    out = []
    with open(HISTORY_PATH, "r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                ts = datetime.fromisoformat(row["ts"])
                out.append((ts, float(row["energy_kwh"])))
            except (KeyError, ValueError):
                continue
    return [(t, e) for t, e in out if t >= cutoff]


def estimate_usage(history: list[tuple[datetime, float]]) -> dict:
    """用最近一段单调下降的样本估算日均消耗与可用天数。"""
    if len(history) < 2:
        return {"per_day": None, "days_left": None}
    seg = [history[-1]]
    for prev in reversed(history[:-1]):
        if prev[1] >= seg[-1][1] - 0.05:      # 越早的电量应越高；突然变低说明中间充过值
            seg.append(prev)
        else:
            break
    if len(seg) < 2:
        return {"per_day": None, "days_left": None}
    span_days = (seg[0][0] - seg[-1][0]).total_seconds() / 86400
    used = seg[-1][1] - seg[0][1]
    if span_days <= 0.05 or used <= 0:
        return {"per_day": None, "days_left": None}
    per_day = used / span_days
    return {"per_day": round(per_day, 2),
            "days_left": round(seg[0][1] / per_day, 1) if per_day > 0 else None}


# ---------------------------------------------------------------- 通知

NOTIFY_UA = UA.split(" MicroMessenger")[0]
REQUIRED_FIELDS = {
    "pushplus": "pushplus_token",
    "serverchan": "serverchan_key",
    "wecom": "wecom_webhook",
}


def _pick_channel(cfg: dict) -> str:
    """auto：配了哪个就发哪个，都没配就弹桌面通知。"""
    notify_cfg = cfg.get("notify") or {}
    channel = notify_cfg.get("channel", "auto")
    if channel != "auto":
        return channel
    if notify_cfg.get("pushplus_token"):
        return "pushplus"
    if notify_cfg.get("serverchan_key"):
        return "serverchan"
    if notify_cfg.get("wecom_webhook"):
        return "wecom"
    return "toast"


def _serverchan_url(key: str) -> str:
    """兼容两代 Server酱：sc3 的 key 形如 sctp{uid}t...，Turbo 形如 SCT..."""
    match = re.match(r"^sctp(\d+)t", key or "")
    if match:
        return f"https://{match.group(1)}.push.ft07.com/send/{key}.send"
    return f"https://sctapi.ftqq.com/{key}.send"


def _toast(title: str, body: str) -> bool:
    script = os.path.join(HOME, "toast.ps1")
    if not os.path.exists(script) or os.name != "nt":
        return False
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", script, "-Title", title, "-Body", body],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except Exception as exc:                                  # noqa: BLE001
        log(f"桌面通知启动失败: {exc}")
        return False


def notify(cfg: dict, title: str, body: str) -> bool:
    channel = _pick_channel(cfg)
    notify_cfg = cfg.get("notify") or {}
    field = REQUIRED_FIELDS.get(channel)
    if field and not notify_cfg.get(field):
        log(f"notify 跳过：channel={channel} 但 {field} 未填写")
        _toast("电费提醒（未配置推送凭据）", f"{title}\n{body}")
        return False
    try:
        if channel == "pushplus":
            url = notify_cfg.get("pushplus_url") or "https://www.pushplus.plus/send"
            data = json.dumps({"token": notify_cfg["pushplus_token"], "title": title,
                               "content": body, "template": "txt"}).encode()
            req = urllib.request.Request(url, data=data,
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": NOTIFY_UA})
            with urllib.request.urlopen(req, timeout=20) as resp:
                res = json.loads(resp.read().decode())
            log(f"notify pushplus -> {res.get('code')} {res.get('msg') or ''}".strip())
            return res.get("code") == 200

        if channel == "serverchan":
            key = notify_cfg["serverchan_key"]
            url = notify_cfg.get("serverchan_url") or _serverchan_url(key)
            data = urllib.parse.urlencode({"title": title, "desp": body}).encode()
            req = urllib.request.Request(url, data=data, headers={
                "User-Agent": NOTIFY_UA,
                "Content-Type": "application/x-www-form-urlencoded",
            })
            with urllib.request.urlopen(req, timeout=20) as resp:
                res = json.loads(resp.read().decode())
            detail = res.get("message") or (res.get("data") or {}).get("error") or ""
            log(f"notify serverchan -> {res.get('code')} {detail}".strip())
            return res.get("code") == 0

        if channel == "wecom":
            data = json.dumps({"msgtype": "markdown",
                               "markdown": {"content": f"**{title}**\n{body}"}}).encode()
            req = urllib.request.Request(notify_cfg["wecom_webhook"], data=data,
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": NOTIFY_UA})
            with urllib.request.urlopen(req, timeout=20) as resp:
                res = json.loads(resp.read().decode())
            log(f"notify wecom -> {res.get('errcode')}")
            return res.get("errcode") == 0

        if channel == "toast":
            ok = _toast(title, body)
            log(f"notify toast -> {ok}")
            return ok

        log(f"[console] {title}\n{body}")
        return True
    except Exception as exc:                                  # noqa: BLE001
        log(f"notify FAILED ({channel}): {exc}")
        if channel in REQUIRED_FIELDS and notify_cfg.get("toast_fallback", True):
            _toast("电费提醒（推送失败，本地显示）", f"{title}\n{body}")
        return False


# ---------------------------------------------------------------- 主流程


def build_message(cfg: dict, room: dict, reading: dict, usage: dict, reason: str) -> tuple[str, str]:
    name = room.get("roomCode") or cfg.get("room", {}).get("code")
    money = "?" if reading["money"] is None else f"{reading['money']:.2f}"
    title = f"电费余额不足 · {name}"
    lines = [f"房间: {name}", f"剩余电量: {reading['energy']:.2f} 度", f"剩余金额: ¥{money}"]
    if usage.get("per_day"):
        lines.append(f"日均消耗: {usage['per_day']:.2f} 度/天")
    if usage.get("days_left"):
        lines.append(f"预计可用: {usage['days_left']:.1f} 天")
    lines.append(f"触发原因: {reason}")
    lines.append(f"查询时间: {reading['ts'][:19].replace('T', ' ')}")
    lines.append("")
    lines.append("请及时充值，避免断电。")
    return title, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="宿舍电费监控")
    parser.add_argument("--test", action="store_true", help="强制发一条测试消息（不受阈值限制）")
    parser.add_argument("--json", action="store_true", help="打印读数 JSON")
    parser.add_argument("--no-notify", action="store_true", help="只查不推")
    parser.add_argument("--resolve", action="store_true", help="重新按房间号解析 roomId")
    args = parser.parse_args()

    cfg = load_json(CONFIG_PATH, {})
    if not (cfg.get("room") or {}).get("code"):
        log("config.json 缺少 room.code")
        return 2
    state = load_json(STATE_PATH, {"failures": 0})

    try:
        room = state.get("room") or {}
        if args.resolve or not room.get("roomId") or room.get("roomCode") != cfg["room"]["code"]:
            room = resolve_room(cfg)
            state["room"] = room
            log(f"解析房间: {room.get('buildingName') or ''} {room['roomCode']} "
                f"(roomId={room['roomId']})")
        reading = fetch_balance(cfg, room)
    except Exception as exc:                                  # noqa: BLE001
        state["failures"] = int(state.get("failures", 0)) + 1
        state["last_error"] = f"{datetime.now(CST).isoformat(timespec='seconds')} {exc}"
        save_json(STATE_PATH, state)
        log(f"查询失败 ({state['failures']}): {exc}")
        limit = int(cfg.get("alert", {}).get("heartbeat_failures", 3))
        if state["failures"] == limit and not args.no_notify:
            notify(cfg, "电费监控掉线", f"连续 {limit} 次查询失败，最后一次错误:\n{exc}")
        return 2

    state["failures"] = 0
    state["last_success"] = reading["ts"]
    prev = state.get("last_energy")
    if prev is not None and reading["energy"] > float(prev) + 1.0 and state.get("last_alert"):
        log(f"检测到充值（{float(prev):.2f} -> {reading['energy']:.2f} 度），重置告警冷却")
        state["last_alert"] = None
    state["last_energy"] = reading["energy"]
    append_history(reading)
    usage = estimate_usage(read_history())

    if args.json or args.test:
        print(json.dumps({"room": room, "reading": reading, "usage": usage},
                         ensure_ascii=False, indent=2))

    threshold = cfg.get("threshold", {})
    th_energy = float(threshold.get("energy_kwh") or 0)
    th_money = float(threshold.get("money_yuan") or 0)

    hits = []
    if th_energy and reading["energy"] <= th_energy:
        hits.append(f"电量 {reading['energy']:.2f} ≤ {th_energy:g} 度")
    if th_money and reading["money"] is not None and reading["money"] <= th_money:
        hits.append(f"金额 ¥{reading['money']:.2f} ≤ ¥{th_money:g}")

    if args.test:
        if hits:
            reason = "；".join(hits)
            title, body = build_message(cfg, room, reading, usage, reason)
        else:
            reason = f"当前 {reading['energy']:.2f} 度，高于阈值 {th_energy:g} 度（手动测试）"
            title, body = build_message(cfg, room, reading, usage, reason)
            title = f"电费监控测试 · {room.get('roomCode')}"
        ok = True
        if not args.no_notify:
            ok = notify(cfg, "【测试】" + title, body)
        save_json(STATE_PATH, state)
        return 0 if ok else 2

    if not hits:
        save_json(STATE_PATH, state)
        log(f"正常: {reading['energy']:.2f} 度 (阈值 {th_energy:g} 度)")
        return 0

    reason = "；".join(hits)
    title, body = build_message(cfg, room, reading, usage, reason)

    cooldown = float(cfg.get("alert", {}).get("cooldown_hours", 12))
    last = state.get("last_alert")
    if last:
        try:
            elapsed = (datetime.now(CST) - datetime.fromisoformat(last)).total_seconds() / 3600
            if elapsed < cooldown:
                save_json(STATE_PATH, state)
                log(f"告警冷却中（{elapsed:.1f}h < {cooldown:g}h）: {reason}")
                return 1
        except ValueError:
            pass

    log(f"告警: {reason}")
    if not args.no_notify and notify(cfg, title, body):
        state["last_alert"] = datetime.now(CST).isoformat(timespec="seconds")
    save_json(STATE_PATH, state)
    return 1


if __name__ == "__main__":
    sys.exit(main())
