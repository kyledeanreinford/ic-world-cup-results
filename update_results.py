#!/usr/bin/env python3
"""
Update data.json with finished-match results from ESPN's public JSON API.

Deterministic, no API key, no LLM. For every match still "upcoming" whose
kickoff is in the past, it looks up the ESPN game, and if it's full-time pulls
the final score and red cards (half from period, goalkeeper from the roster).
Then it rewrites data.json and (unless --dry-run) commits and pushes.

House-rule fields produced per red card: { player, goalie, half }.

Usage:
  python3 update_results.py            # apply, commit, push
  python3 update_results.py --dry-run  # show what would change, write nothing
"""
import json
import sys
import re
import subprocess
import unicodedata
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

LEAGUE = "fifa.world"
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/scoreboard?dates={date}"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/summary?event={eid}"
DATA = Path(__file__).with_name("data.json")

# ESPN spells a handful of nations differently than our canonical names.
ALIASES = {
    "turkey": "turkiye", "turkiye": "turkiye",
    "unitedstates": "usa", "usa": "usa",
    "czechrepublic": "czechia", "czechia": "czechia",
    "cotedivoire": "ivorycoast", "ivorycoast": "ivorycoast",
    "drcongo": "drcongo", "congodr": "drcongo", "democraticrepublicofcongo": "drcongo",
    "bosniaandherzegovina": "bosniaherzegovina", "bosniaherzegovina": "bosniaherzegovina",
    "republicofkorea": "southkorea", "southkorea": "southkorea", "korearepublic": "southkorea",
    "capeverde": "capeverde", "caboverde": "capeverde",
}


def norm(name):
    """Accent-fold, lowercase, strip to alnum, then apply alias map."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    return ALIASES.get(s, s)


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ic-world-cup-results/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def scoreboard_index(date_yyyymmdd):
    """Map frozenset of the two normalized team names -> ESPN competition dict."""
    data = get_json(SCOREBOARD.format(lg=LEAGUE, date=date_yyyymmdd))
    idx = {}
    for ev in data.get("events", []):
        comp = ev.get("competitions", [{}])[0]
        teams = comp.get("competitors", [])
        names = [norm(t.get("team", {}).get("displayName", "")) for t in teams]
        if len(names) == 2:
            idx[frozenset(names)] = {"id": ev["id"], "comp": comp,
                                     "status": comp.get("status", {}).get("type", {}).get("name", "")}
    return idx


def extract_result(eid, home, away):
    """Return {home:{goals,reds}, away:{goals,reds}} for a finished ESPN event."""
    summ = get_json(SUMMARY.format(lg=LEAGUE, eid=eid))

    # Goalkeeper athlete ids, from the rosters (position abbreviation "G").
    gk_ids = set()
    for grp in summ.get("rosters", []):
        for pl in grp.get("roster", []):
            pos = (pl.get("position") or {})
            if pos.get("abbreviation") == "G":
                aid = (pl.get("athlete") or {}).get("id")
                if aid:
                    gk_ids.add(str(aid))

    sides = {norm(home): {"goals": 0, "reds": []},
             norm(away): {"goals": 0, "reds": []}}

    # Final score from the header competitors.
    for c in summ.get("header", {}).get("competitions", [{}])[0].get("competitors", []):
        nm = norm(c.get("team", {}).get("displayName", ""))
        if nm in sides:
            try:
                sides[nm]["goals"] = int(c.get("score", 0))
            except (TypeError, ValueError):
                pass

    # Red cards from keyEvents.
    for e in summ.get("keyEvents", []):
        if e.get("type", {}).get("type") != "red-card":
            continue
        team = norm(e.get("team", {}).get("displayName", ""))
        if team not in sides:
            continue
        parts = e.get("participants") or []
        aid = str((parts[0].get("athlete") or {}).get("id")) if parts else ""
        player = (parts[0].get("athlete") or {}).get("displayName", "Unknown") if parts else "Unknown"
        half = e.get("period", {}).get("number", 2)
        sides[team]["reds"].append(
            {"player": player, "goalie": aid in gk_ids, "half": half}
        )

    return {"home": sides[norm(home)], "away": sides[norm(away)]}


def main():
    dry = "--dry-run" in sys.argv
    doc = json.loads(DATA.read_text())
    now = datetime.now(timezone.utc)

    # Group the matches that need checking by their scoreboard date.
    due = [m for m in doc["matches"]
           if m.get("status") == "upcoming"
           and datetime.fromisoformat(m["kickoff"].replace("Z", "+00:00")) < now]
    if not due:
        print("No kicked-off matches awaiting results.")
        return

    sb_cache, changed = {}, []
    for m in due:
        d = m["date"].replace("-", "")
        if d not in sb_cache:
            try:
                sb_cache[d] = scoreboard_index(d)
            except Exception as ex:
                print(f"  ! scoreboard {d} failed: {ex}")
                sb_cache[d] = {}
        ev = sb_cache[d].get(frozenset((norm(m["home"]), norm(m["away"]))))
        if not ev:
            print(f"  - {m['id']} {m['home']} v {m['away']}: no ESPN event yet")
            continue
        if ev["status"] != "STATUS_FULL_TIME":
            print(f"  · {m['id']} {m['home']} v {m['away']}: in progress ({ev['status']})")
            continue
        try:
            res = extract_result(ev["id"], m["home"], m["away"])
        except Exception as ex:
            print(f"  ! {m['id']} summary failed: {ex}")
            continue
        m["status"] = "final"
        m["result"] = res
        h, a = res["home"], res["away"]
        print(f"  ✓ {m['id']} {m['home']} {h['goals']}-{a['goals']} {m['away']} "
              f"(reds {len(h['reds'])}/{len(a['reds'])}, "
              f"gk {sum(r['goalie'] for r in h['reds']+a['reds'])})")
        changed.append(m["id"])

    if not changed:
        print("Nothing finished to record.")
        return

    if dry:
        print(f"\n[dry-run] would finalize {len(changed)}: {', '.join(changed)}")
        return

    DATA.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    repo = DATA.parent
    subprocess.run(["git", "-C", repo, "add", "data.json"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-m",
                    f"Results: finalize {', '.join(changed)}"], check=True)
    subprocess.run(["git", "-C", repo, "push", "origin", "main"], check=True)
    print(f"\nPushed {len(changed)} result(s).")


if __name__ == "__main__":
    main()
