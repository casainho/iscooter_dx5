#!/usr/bin/env python3
"""Log the background review's observed state every minute."""
import datetime, json, os, sys, time
from pathlib import Path
from zoneinfo import ZoneInfo

root = Path(__file__).resolve().parent.parent
state_path = root / 'iscooter-posts' / 'background-review-state.json'
log_path = root / 'iscooter-posts' / 'background-review-progress.log'

while True:
    now = datetime.datetime.now(ZoneInfo('Europe/Lisbon')).strftime('%Y-%m-%d %H:%M:%S %Z')
    try:
        state = json.loads(state_path.read_text())
        pid = int(state.get('pid', 0))
        try:
            os.kill(pid, 0)
            alive = True
        except (ProcessLookupError, PermissionError):
            alive = False
        status = state.get('status', 'unknown')
        line = (f'{now} | estado={status} | processo={"ativo" if alive else "inativo"} | '
                f'capturas={state.get("processed", 0)} | último={state.get("last_id") or "nenhum"} | '
                f'em curso={state.get("in_progress") or "nenhum"} | atualizado={state.get("updated", "desconhecido")}')
        if state.get('error'):
            line += f' | erro={state["error"]}'
    except (OSError, ValueError, TypeError) as exc:
        line = f'{now} | estado indisponível: {type(exc).__name__}'
        status, alive = 'unknown', False
    with log_path.open('a') as log:
        log.write(line + '\n')
    print(line, flush=True)
    if status in {'stopped', 'finished_with_partials'} or (status != 'starting' and not alive):
        break
    time.sleep(60)
