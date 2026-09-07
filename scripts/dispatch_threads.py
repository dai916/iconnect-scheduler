#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""投稿時刻ちょうどに iconnect-threads の投稿ワークフローを起動する。

GitHubのスケジュール実行(cron)は発火自体が1〜4時間遅れることがあり、
iconnect-threads 側の実測では AM枠が約1時間、PM枠が約2時間遅れて着地していた。
そこで「Actions実行が無料の公開リポジトリ」で短い間隔のtickerを回し、
狙った時刻まで待ってから workflow_dispatch で叩く。

このスクリプト自体の起動も遅れる前提で作ってある:
  - 投稿時刻まで MAX_SLEEP_MIN 以内なら、その時刻まで待ってから叩く(定刻投稿)
  - すでに投稿時刻を過ぎていても CATCHUP_MIN 以内なら即座に叩く(取り返し)
  - どちらの場合も、その枠が今日すでに実行済みなら何もしない(二重投稿防止)

必要な環境変数:
  DISPATCH_TOKEN  ... 対象リポジトリに Actions:write を持つ fine-grained PAT
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, time as dtime, timedelta, timezone

JST = timezone(timedelta(hours=9))
TARGET_REPO = os.getenv("TARGET_REPO", "dai916/iconnect-threads")
API = "https://api.github.com"

# 枠 → 狙う投稿時刻(JST)。iconnect-threads の weekly_schedule と合わせる。
SLOTS = {
    "am": dtime(8, 15),
    "pm": dtime(22, 0),
}
MAX_SLEEP_MIN = 45   # これ以内に投稿時刻が来るなら待って定刻に叩く
CATCHUP_MIN = 240    # 投稿時刻を過ぎていてもこの範囲なら取り返して叩く
DRY_RUN = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")
# PATの設定確認だけをする自己診断モード(投稿時刻と関係なく実行できる)
CHECK_ONLY = os.getenv("CHECK_ONLY", "").lower() in ("1", "true", "yes")


def api(path: str, method: str = "GET", body: dict | None = None) -> dict:
    token = os.environ["DISPATCH_TOKEN"]
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}", data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "iconnect-threads-scheduler",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


def workflow_file(target_day: datetime, slot: str) -> str:
    """その日の枠に対応するワークフローファイル名 (例: post-monday-am.yml)"""
    return f"post-{target_day.strftime('%A').lower()}-{slot}.yml"


def ran_today(workflow: str, today) -> bool:
    """その枠のワークフローが今日(JST)すでに走っているか。

    定時cronが先に発火していた場合や、前のtickがすでに叩いた場合に
    二重で叩かないための確認。"""
    try:
        data = api(f"/repos/{TARGET_REPO}/actions/workflows/{workflow}/runs?per_page=10")
    except urllib.error.HTTPError as e:
        # 判断できないときは「走っていない」とみなさず、安全側(叩かない)に倒す
        print(f"[警告] 実行履歴を取得できません ({workflow}): {e}")
        return True
    for run in data.get("workflow_runs", []):
        created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if created.astimezone(JST).date() == today:
            print(f"  {workflow} は今日すでに実行済み "
                  f"({created.astimezone(JST):%H:%M} / {run['event']})")
            return True
    return False


def dispatch(workflow: str) -> None:
    if DRY_RUN:
        print(f"  [DRY_RUN] {workflow} を叩いたことにします")
        return
    api(f"/repos/{TARGET_REPO}/actions/workflows/{workflow}/dispatches",
        method="POST", body={"ref": "main"})
    print(f"  {workflow} を起動しました")


def main() -> None:
    if not os.getenv("DISPATCH_TOKEN"):
        # PAT未設定でも失敗にはしない。iconnect-threads 側の定時cronが
        # (遅れながらも)投稿するので、ここで赤くしても直る種類の話ではない。
        print("DISPATCH_TOKEN が未設定です。README の手順でPATを登録してください。"
              "登録するまで定刻投稿は動かず、遅延したcron任せになります。")
        return

    now = datetime.now(JST)
    print(f"現在 {now:%Y-%m-%d %H:%M:%S} JST / 対象 {TARGET_REPO}")

    if CHECK_ONLY:
        # PATが対象リポジトリのActionsを読めるか(=叩けるか)だけを確認する。
        # 投稿時刻から遠い時間帯でも設定ミスに気づけるようにするための自己診断。
        data = api(f"/repos/{TARGET_REPO}/actions/workflows?per_page=100")
        names = [w["path"].split("/")[-1] for w in data.get("workflows", [])]
        posts = sorted(n for n in names if n.startswith("post-"))
        print(f"PATで {TARGET_REPO} のワークフロー {len(names)}件を取得できました。")
        print(f"投稿ワークフロー {len(posts)}件: {', '.join(posts)}")
        missing = [
            workflow_file(now.replace(hour=0), slot)
            for slot in SLOTS
            if workflow_file(now.replace(hour=0), slot) not in names
        ]
        print("今日の枠に対応するワークフロー: "
              + ("すべて存在します" if not missing else f"見つかりません → {missing}"))
        return

    for slot, at in sorted(SLOTS.items(), key=lambda kv: kv[1]):
        target = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
        wait_min = (target - now).total_seconds() / 60
        workflow = workflow_file(target, slot)

        if wait_min > MAX_SLEEP_MIN:
            print(f"[{slot}] {at:%H:%M} まで {wait_min:.0f}分 → 今回は何もしない")
            continue
        if wait_min < -CATCHUP_MIN:
            print(f"[{slot}] {at:%H:%M} から {-wait_min:.0f}分経過 → 取り返し範囲外")
            continue

        print(f"[{slot}] {at:%H:%M} / {workflow}")
        if ran_today(workflow, now.date()):
            continue

        if wait_min > 0:
            print(f"  {wait_min:.1f}分待ってから起動します")
            time.sleep(wait_min * 60)
            # 待っている間に定時cronが動いていないか、直前にもう一度確認する
            if ran_today(workflow, datetime.now(JST).date()):
                continue
        else:
            print(f"  投稿時刻を{-wait_min:.0f}分過ぎているため即起動します")

        dispatch(workflow)


if __name__ == "__main__":
    try:
        main()
    except KeyError:
        sys.exit("DISPATCH_TOKEN が設定されていません。")
    except urllib.error.HTTPError as e:
        sys.exit(f"GitHub API エラー: {e.code} {e.read().decode(errors='replace')[:300]}")
