"""Unified CLI for swarm-tminus.

Usage:
    swarm-tminus <module> <action> [args]

Modules:
    predict   predict-and-confirm primitives
    event     countdown + quorum event store
    deadline  hierarchical deadline trees
    rate      token / leaky bucket rate limiters
    tempo     BPM-adaptive tick clocks
    cron      5-field cron parser
    campaign  DAG-ordered campaigns
    matcher   typed event matching
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(2)


def _out(obj: Any) -> None:
    if isinstance(obj, (dict, list)):
        print(json.dumps(obj, indent=2, sort_keys=True, default=str))
    else:
        print(obj)


def _float(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        _err(f"invalid float: {s!r}")


def _int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        _err(f"invalid int: {s!r}")


def _path(s: str) -> Path:
    return Path(s).expanduser().resolve()


def _duration_to_unix(spec: str) -> float:
    """Parse `+5m`, `+1h`, `+30s`, `+2d`, or ISO timestamp."""
    spec = spec.strip()
    if spec.startswith("+"):
        n = float(spec[1:-1])
        unit = spec[-1].lower()
        if unit == "s":
            return time.time() + n
        if unit == "m":
            return time.time() + n * 60
        if unit == "h":
            return time.time() + n * 3600
        if unit == "d":
            return time.time() + n * 86400
        _err(f"unknown duration unit {unit!r}")
    if spec.endswith("Z"):
        from datetime import datetime
        try:
            dt = datetime.strptime(spec, "%Y-%m-%dT%H:%M:%SZ")
            return dt.replace(tzinfo=__import__("datetime").timezone.utc).timestamp()
        except ValueError:
            _err(f"invalid ISO timestamp: {spec!r}")
    # Try as float unix
    try:
        return float(spec)
    except ValueError:
        _err(f"invalid time spec: {spec!r}")


# ---------------------------------------------------------------------------
# predict subcommand
# ---------------------------------------------------------------------------

def cmd_predict(args: argparse.Namespace) -> int:
    from swarm_tminus.predictor import Predictor

    if args.action == "advance":
        p = Predictor(args.bpm, args.key or "")
        if args.ahead > 0:
            # add a prediction `args.ahead` beats ahead, then advance `args.beats` beats
            p.add_prediction(args.event, args.ahead, 0.9)
            triggered = p.advance(args.beats)
            _out({
                "current_beat": p.current_beat,
                "triggered": [
                    {"id": ev.id, "event_type": ev.event_type, "confidence": ev.confidence}
                    for ev in triggered
                ],
            })
        else:
            triggered = p.advance(args.beats)
            _out({
                "current_beat": p.current_beat,
                "triggered": [
                    {"id": ev.id, "event_type": ev.event_type, "confidence": ev.confidence}
                    for ev in triggered
                ],
            })
        return 0

    if args.action == "add":
        p = Predictor(args.bpm, args.key or "")
        pid = p.add_prediction(args.event, args.ahead, args.confidence)
        _out({"prediction_id": pid, "current_beat": p.current_beat})
        return 0

    if args.action == "savings":
        p = Predictor(args.bpm, args.key or "")
        for _ in range(args.predictions):
            p.add_prediction("chord_change", 4.0, 0.9)
        for _ in range(args.confirmations):
            if p.events:
                p.confirm(p.events[0].id)
        s = p.message_savings()
        _out({
            "predictions_made": s.predictions_made,
            "confirmations_sent": s.confirmations_sent,
            "polling_equivalent": s.polling_equivalent,
            "savings_ratio": s.savings_ratio,
        })
        return 0

    _err(f"unknown predict action {args.action!r}")
    return 1


# ---------------------------------------------------------------------------
# event subcommand
# ---------------------------------------------------------------------------

def cmd_event(args: argparse.Namespace) -> int:
    from swarm_tminus.events import (
        CountdownEvent, EventStore, EventStatus, SubscriberStatus,
    )

    if args.action == "list":
        store = EventStore(args.dir)
        names = sorted(e.name for e in store.all_events())
        for n in names:
            e = store.get(n)
            print(f"{e.name}\t{e.status.value}\tq={e.quorum_required}\tfire_at={e.fire_at_unix}")
        return 0

    if args.action == "add":
        store = EventStore(args.dir)
        fire_at = _duration_to_unix(args.in_seconds)
        e = CountdownEvent(
            name=args.name,
            fire_at_unix=fire_at,
            quorum_required=args.quorum,
        )
        store.add_event(e)
        store.save()
        _out({"name": e.name, "fire_at_unix": e.fire_at_unix, "quorum_required": e.quorum_required})
        return 0

    if args.action == "confirm":
        store = EventStore(args.dir)
        e = store.get(args.name)
        if e is None:
            _err(f"event {args.name!r} not found in {args.dir}")
        e.confirm(args.subscriber, SubscriberStatus.CONFIRMED)
        store.save()
        _out({"name": e.name, "subscriber": args.subscriber, "confirmed_count": e.confirmed_count()})
        return 0

    if args.action == "fire":
        store = EventStore(args.dir)
        fired = store.fire_due(args.now_unix)
        store.save()
        _out({
            "fired": [{"name": e.name, "status": e.status.value} for e in fired],
            "count": len(fired),
        })
        return 0

    if args.action == "reap":
        store = EventStore(args.dir)
        missed = store.reap_missed(args.now_unix)
        store.save()
        _out({
            "missed": [{"name": e.name, "status": e.status.value} for e in missed],
            "count": len(missed),
        })
        return 0

    _err(f"unknown event action {args.action!r}")
    return 1


# ---------------------------------------------------------------------------
# deadline subcommand
# ---------------------------------------------------------------------------

def cmd_deadline(args: argparse.Namespace) -> int:
    from swarm_tminus.deadlines import (
        DeadlineNode, DeadlineTree, cascade_cancel,
    )

    if args.action == "start":
        root = DeadlineNode(name=args.name, duration_seconds=args.duration)
        if args.parent:
            # We're starting a child under a parent tree
            try:
                tree = DeadlineTree.load(args.dir, args.parent)
            except FileNotFoundError:
                _err(f"parent tree {args.parent!r} not found in {args.dir}")
            parent = tree.find(args.parent)
            if parent is None:
                _err(f"parent node {args.parent!r} not found in tree")
            child = parent.add_child(args.name, args.duration)
            child.start(args.now_unix)
            tree.save(args.dir)
        else:
            root.start(args.now_unix)
            tree = DeadlineTree(root=root)
            tree.save(args.dir)
        _out({"started": args.name, "duration": args.duration})
        return 0

    if args.action == "cancel":
        try:
            tree = DeadlineTree.load(args.dir, args.root)
        except FileNotFoundError:
            _err(f"tree {args.root!r} not found in {args.dir}")
        cancelled = tree.cancel(args.name)
        tree.save(args.dir)
        _out({"cancelled": args.name, "count": cancelled})
        return 0

    if args.action == "show":
        try:
            tree = DeadlineTree.load(args.dir, args.root)
        except FileNotFoundError:
            _err(f"tree {args.root!r} not found in {args.dir}")
        def ser(n):
            return {
                "name": n.name,
                "duration_seconds": n.duration_seconds,
                "status": n.status.value,
                "children": [ser(c) for c in n.children],
            }
        _out(ser(tree.root))
        return 0

    _err(f"unknown deadline action {args.action!r}")
    return 1


# ---------------------------------------------------------------------------
# rate subcommand
# ---------------------------------------------------------------------------

def cmd_rate(args: argparse.Namespace) -> int:
    from swarm_tminus.rate import TokenBucket, LeakyBucket, RatePair

    if args.action == "token":
        b = TokenBucket(capacity=args.cap, refill_per_sec=args.refill)
        results = []
        for _ in range(args.consume):
            ok = b.try_consume(1.0)
            results.append({"consumed": ok, "tokens": b.tokens})
        _out({"results": results, "final_tokens": b.tokens})
        return 0

    if args.action == "leaky":
        b = LeakyBucket(capacity=args.cap, drip_per_sec=args.drip)
        results = []
        for _ in range(args.add):
            ok = b.add(1.0)
            results.append({"added": ok, "level": b.level})
        _out({"results": results, "final_level": b.level})
        return 0

    if args.action == "pair":
        tb = TokenBucket(capacity=args.cap, refill_per_sec=args.refill)
        lb = LeakyBucket(capacity=args.cap, drip_per_sec=args.drip)
        pair = RatePair(token=tb, leaky=lb)
        results = []
        for _ in range(args.consume):
            ok, reason = pair.try_send(1.0)
            results.append({"allowed": ok, "reason": reason})
        _out({"results": results})
        return 0

    _err(f"unknown rate action {args.action!r}")
    return 1


# ---------------------------------------------------------------------------
# tempo subcommand
# ---------------------------------------------------------------------------

def cmd_tempo(args: argparse.Namespace) -> int:
    from swarm_tminus.tempo import TickClock

    if args.action == "tick":
        c = TickClock(bpm=args.bpm, swing=args.swing)
        ticks = []
        for _ in range(args.count):
            t = c.next_tick(now_unix=args.now_unix)
            ticks.append({"id": t.id, "timestamp": t.timestamp, "delta": t.delta})
            args.now_unix = t.timestamp
        _out({"ticks": ticks, "final_bpm": c.bpm})
        return 0

    if args.action == "adapt":
        c = TickClock(bpm=args.bpm)
        c.adapt(args.energy)
        _out({"new_bpm": c.bpm})
        return 0

    if args.action == "interval":
        c = TickClock(bpm=args.bpm, swing=args.swing)
        _out({"interval": c.tick_interval(), "bpm": c.bpm, "swing": c.swing})
        return 0

    _err(f"unknown tempo action {args.action!r}")
    return 1


# ---------------------------------------------------------------------------
# cron subcommand
# ---------------------------------------------------------------------------

def cmd_cron(args: argparse.Namespace) -> int:
    from swarm_tminus.cron import CronParser, next_fire

    if args.action == "parse":
        p = CronParser(args.expr)
        result = p.parse()
        # Sets aren't JSON-serializable; convert to sorted lists
        _out({k: sorted(v) for k, v in result.items()})
        return 0

    if args.action == "next":
        if args.after:
            after_unix = _duration_to_unix(args.after)
        else:
            after_unix = None
        nxt = next_fire(args.expr, after_unix=after_unix)
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(nxt, tz=timezone.utc)
        _out({"expression": args.expr, "next_fire_unix": nxt, "next_fire_iso": dt.strftime("%Y-%m-%dT%H:%M:%SZ")})
        return 0

    _err(f"unknown cron action {args.action!r}")
    return 1


# ---------------------------------------------------------------------------
# campaign subcommand
# ---------------------------------------------------------------------------

def cmd_campaign(args: argparse.Namespace) -> int:
    from swarm_tminus.campaign import Campaign, topological_order

    if args.action == "order":
        # --edges "a,b|b,c" means edges a->b, b->c
        edges = []
        if args.edges:
            for pair in args.edges.split("|"):
                a, b = pair.split(",")
                edges.append((a.strip(), b.strip()))
        nodes = args.nodes.split(",") if args.nodes else None
        order = topological_order(edges, nodes=nodes)
        _out({"order": order})
        return 0

    if args.action == "add-event":
        from swarm_tminus.events import CountdownEvent
        try:
            camp = Campaign.load(args.dir, args.name)
        except FileNotFoundError:
            camp = Campaign(name=args.name)
        e = CountdownEvent(name=args.event, fire_at_unix=args.fire_at)
        camp.add_event(e)
        if args.edges:
            for pair in args.edges.split("|"):
                a, b = pair.split(",")
                camp.add_edge(a.strip(), b.strip())
        camp.save(args.dir)
        _out({"campaign": args.name, "event": args.event, "events": len(camp.events)})
        return 0

    if args.action == "show":
        try:
            camp = Campaign.load(args.dir, args.name)
        except FileNotFoundError:
            _err(f"campaign {args.name!r} not found in {args.dir}")
        try:
            order = camp.topological_order()
        except Exception as ex:
            order = [f"ERROR: {ex}"]
        _out({
            "name": camp.name,
            "events": [e.name for e in camp.events],
            "edges": [list(pair) for pair in camp.edges],
            "topological_order": order,
        })
        return 0

    _err(f"unknown campaign action {args.action!r}")
    return 1


# ---------------------------------------------------------------------------
# matcher subcommand
# ---------------------------------------------------------------------------

def cmd_matcher(args: argparse.Namespace) -> int:
    from swarm_tminus.matcher import EventMatcher, find_matches

    if args.action == "match":
        pattern = json.loads(args.pattern)
        actual = json.loads(args.actual)
        m = EventMatcher(name=args.name, pattern=pattern, confidence=args.confidence, energy_cost=args.cost)
        if m.matches(actual):
            _out({"matched": True, "matcher": args.name, "actual": actual})
        else:
            _out({"matched": False, "matcher": args.name})
        return 0

    if args.action == "find":
        patterns = json.loads(args.patterns)
        actual = json.loads(args.actual)
        matchers = [
            EventMatcher(
                name=p["name"],
                pattern=p["pattern"],
                confidence=p.get("confidence", 1.0),
                energy_cost=p.get("energy_cost", 0.0),
            )
            for p in patterns
        ]
        results = find_matches(matchers, actual)
        _out({"matches": [{"matcher": r.matcher.name, "confidence": r.confidence} for r in results]})
        return 0

    _err(f"unknown matcher action {args.action!r}")
    return 1


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="swarm-tminus",
        description="Time-shaped coordination primitives for swarm-anchor.",
    )
    sub = p.add_subparsers(dest="module", required=True)

    # ---- predict ----
    pp = sub.add_parser("predict", help="Predict-and-confirm primitives")
    ppp = pp.add_subparsers(dest="action", required=True)
    pp_adv = ppp.add_parser("advance")
    pp_adv.add_argument("--bpm", type=float, default=120.0)
    pp_adv.add_argument("--key", default="")
    pp_adv.add_argument("--beats", type=float, default=4.0)
    pp_adv.add_argument("--ahead", type=float, default=4.0)
    pp_adv.add_argument("--event", default="chord_change")
    pp_save = ppp.add_parser("add")
    pp_save.add_argument("--bpm", type=float, default=120.0)
    pp_save.add_argument("--key", default="")
    pp_save.add_argument("--event", default="chord_change")
    pp_save.add_argument("--ahead", type=float, required=True)
    pp_save.add_argument("--confidence", type=float, default=0.9)
    pp_sav = ppp.add_parser("savings")
    pp_sav.add_argument("--bpm", type=float, default=120.0)
    pp_sav.add_argument("--key", default="")
    pp_sav.add_argument("--predictions", type=int, default=5)
    pp_sav.add_argument("--confirmations", type=int, default=1)
    pp.set_defaults(func=cmd_predict)

    # ---- event ----
    ep = sub.add_parser("event", help="Countdown + quorum event store")
    epp = ep.add_subparsers(dest="action", required=True)
    epp.add_parser("list").add_argument("--dir", default=".swarm")
    ea = epp.add_parser("add")
    ea.add_argument("--dir", default=".swarm")
    ea.add_argument("--name", required=True)
    ea.add_argument("--in-seconds", required=True, help="e.g. +5m, +1h, +30s")
    ea.add_argument("--quorum", type=int, default=1)
    ec = epp.add_parser("confirm")
    ec.add_argument("--dir", default=".swarm")
    ec.add_argument("--name", required=True)
    ec.add_argument("--subscriber", required=True)
    ef = epp.add_parser("fire")
    ef.add_argument("--dir", default=".swarm")
    ef.add_argument("--now-unix", type=float, default=None)
    er = epp.add_parser("reap")
    er.add_argument("--dir", default=".swarm")
    er.add_argument("--now-unix", type=float, default=None)
    ep.set_defaults(func=cmd_event)

    # ---- deadline ----
    dp = sub.add_parser("deadline", help="Hierarchical deadline trees")
    dpp = dp.add_subparsers(dest="action", required=True)
    ds = dpp.add_parser("start")
    ds.add_argument("--dir", default=".swarm")
    ds.add_argument("--name", required=True)
    ds.add_argument("--duration", type=float, required=True)
    ds.add_argument("--parent", default=None, help="Existing parent tree name")
    ds.add_argument("--now-unix", type=float, default=None)
    dc = dpp.add_parser("cancel")
    dc.add_argument("--dir", default=".swarm")
    dc.add_argument("--root", required=True, help="Root tree name")
    dc.add_argument("--name", required=True, help="Node to cancel")
    dsh = dpp.add_parser("show")
    dsh.add_argument("--dir", default=".swarm")
    dsh.add_argument("--root", required=True)
    dp.set_defaults(func=cmd_deadline)

    # ---- rate ----
    rp = sub.add_parser("rate", help="Token / leaky bucket rate limiters")
    rpp = rp.add_subparsers(dest="action", required=True)
    rt = rpp.add_parser("token")
    rt.add_argument("--cap", type=float, required=True)
    rt.add_argument("--refill", type=float, required=True)
    rt.add_argument("--consume", type=int, default=1)
    rl = rpp.add_parser("leaky")
    rl.add_argument("--cap", type=float, required=True)
    rl.add_argument("--drip", type=float, required=True)
    rl.add_argument("--add", type=int, default=1)
    rpa = rpp.add_parser("pair")
    rpa.add_argument("--cap", type=float, required=True)
    rpa.add_argument("--refill", type=float, required=True)
    rpa.add_argument("--drip", type=float, required=True)
    rpa.add_argument("--consume", type=int, default=1)
    rp.set_defaults(func=cmd_rate)

    # ---- tempo ----
    tp = sub.add_parser("tempo", help="BPM-adaptive tick clocks")
    tpp = tp.add_subparsers(dest="action", required=True)
    tt = tpp.add_parser("tick")
    tt.add_argument("--bpm", type=float, default=120.0)
    tt.add_argument("--swing", type=float, default=0.0)
    tt.add_argument("--count", type=int, default=4)
    tt.add_argument("--now-unix", type=float, default=None)
    ta = tpp.add_parser("adapt")
    ta.add_argument("--bpm", type=float, default=120.0)
    ta.add_argument("--energy", type=float, required=True)
    ti = tpp.add_parser("interval")
    ti.add_argument("--bpm", type=float, default=120.0)
    ti.add_argument("--swing", type=float, default=0.0)
    tp.set_defaults(func=cmd_tempo)

    # ---- cron ----
    cp = sub.add_parser("cron", help="5-field cron parser")
    cpp = cp.add_subparsers(dest="action", required=True)
    cpa = cpp.add_parser("parse")
    cpa.add_argument("--expr", required=True)
    cpn = cpp.add_parser("next")
    cpn.add_argument("--expr", required=True)
    cpn.add_argument("--after", default=None)
    cp.set_defaults(func=cmd_cron)

    # ---- campaign ----
    cap = sub.add_parser("campaign", help="DAG-ordered campaigns")
    capp = cap.add_subparsers(dest="action", required=True)
    cao = capp.add_parser("order")
    cao.add_argument("--edges", default="")
    cao.add_argument("--nodes", default=None)
    cae = capp.add_parser("add-event")
    cae.add_argument("--dir", default=".swarm")
    cae.add_argument("--name", required=True)
    cae.add_argument("--event", required=True)
    cae.add_argument("--fire-at", type=float, required=True)
    cae.add_argument("--edges", default=None)
    cash = capp.add_parser("show")
    cash.add_argument("--dir", default=".swarm")
    cash.add_argument("--name", required=True)
    cap.set_defaults(func=cmd_campaign)

    # ---- matcher ----
    mp = sub.add_parser("matcher", help="Typed event matching")
    mpp = mp.add_subparsers(dest="action", required=True)
    mm = mpp.add_parser("match")
    mm.add_argument("--name", required=True)
    mm.add_argument("--pattern", required=True, help="JSON pattern dict")
    mm.add_argument("--actual", required=True, help="JSON actual dict")
    mm.add_argument("--confidence", type=float, default=1.0)
    mm.add_argument("--cost", type=float, default=0.0)
    mf = mpp.add_parser("find")
    mf.add_argument("--patterns", required=True, help="JSON list of {name, pattern, ...}")
    mf.add_argument("--actual", required=True)
    mp.set_defaults(func=cmd_matcher)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())