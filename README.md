# Revised World Cup 26 - data

Public data feed for the [Revised World Cup 26](https://worldcup.thereinfords.com)
site. The site fetches `data.json` from this repo's raw URL at runtime, so
updating a result is just a commit + push here - no rebuild or redeploy of the
site itself.

Live URL the site reads:
`https://raw.githubusercontent.com/kyledeanreinford/ic-world-cup-results/main/data.json`

## Recording a result

Find the match in `data.json`, set `status` to `"final"`, and add a `result`:

```json
{
  "id": "m04", "date": "2026-06-13", "kickoff": "2026-06-13T19:00:00Z",
  "group": "B", "home": "Qatar", "away": "Switzerland", "status": "final",
  "result": {
    "home": { "goals": 1, "reds": [{ "player": "Khoukhi", "goalie": false, "half": 2 }] },
    "away": { "goals": 2, "reds": [] }
  }
}
```

- `goals`: official goals scored.
- `reds`: one entry per red card, each with `player`, `goalie` (true if the
  goalkeeper), and `half` (1 or 2).
- `kickoff` is a UTC ISO timestamp; the site renders it as ET / PT.

## House-rule scoring (applied by the site)

In priority order:

1. 5+ reds by either team in the 2nd half => draw (the meltdown).
2. 5+ reds by one team in the 1st half => that team wins (shoot the moon).
3. Goalkeeper red card => that team wins (automatic).
4. Otherwise the higher revised score (goals + reds) wins; equal scores draw.
