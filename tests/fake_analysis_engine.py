from __future__ import annotations

import json
import sys
import time


slow = "--slow" in sys.argv
for line in sys.stdin:
    request = json.loads(line)
    if slow:
        time.sleep(20)
    response = {
        "id": request["id"],
        "rootInfo": {
            "currentPlayer": request.get("initialPlayer", "B"),
            "winrate": 0.6,
            "scoreLead": 2.0,
            "visits": request["maxVisits"],
        },
        "moveInfos": [
            {"move": "A9", "winrate": 0.62, "scoreLead": 3.0, "visits": request["maxVisits"]}
        ],
    }
    print(json.dumps(response), flush=True)
