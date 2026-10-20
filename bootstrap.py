#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""根据环境变量生成 config.json，供 CI / Docker 使用。

环境变量：
    ROOM_CODE       房间号，必填
    SENDKEY         Server酱 SendKey（Turbo 或 Server酱³ 皆可）
    PUSHPLUS_TOKEN  PushPlus token（与 SENDKEY 二选一）
    THRESHOLD_KWH   电量阈值，默认 5
    THRESHOLD_YUAN  金额阈值，默认 0（关闭）
    BASE_URL        接口地址，默认 https://jxpt.nxist.com
"""
from __future__ import annotations

import json
import os
import sys

room_code = (os.environ.get("ROOM_CODE") or "").strip()
sendkey = (os.environ.get("SENDKEY") or "").strip()
pushplus = (os.environ.get("PUSHPLUS_TOKEN") or "").strip()

if not room_code:
    sys.exit("缺少环境变量 ROOM_CODE")
if not sendkey and not pushplus:
    sys.exit("缺少 SENDKEY 或 PUSHPLUS_TOKEN")

cfg = {
    "base_url": (os.environ.get("BASE_URL") or "https://jxpt.nxist.com").strip(),
    "room": {"code": room_code},
    "threshold": {
        "energy_kwh": float(os.environ.get("THRESHOLD_KWH") or 5),
        "money_yuan": float(os.environ.get("THRESHOLD_YUAN") or 0),
    },
    "notify": {
        "channel": "serverchan" if sendkey else "pushplus",
        "serverchan_key": sendkey,
        "pushplus_token": pushplus,
        "wecom_webhook": "",
        "toast_fallback": False,
    },
    "alert": {"cooldown_hours": 12, "heartbeat_failures": 3},
}

path = os.environ.get("PM_CONFIG") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
with open(path, "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, ensure_ascii=False, indent=2)

print(f"已生成 {path}：房间 {room_code}，阈值 {cfg['threshold']['energy_kwh']:g} 度，通道 {cfg['notify']['channel']}")
