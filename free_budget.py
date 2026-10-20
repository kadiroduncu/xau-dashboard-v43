"""Atomic daily request allowance reserved for the secondary dashboard."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import os
import sqlite3


def claim_request(api_key, limit=100, path=None, now=None):
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(timezone.utc).date().isoformat()
    identity = hashlib.sha256(str(api_key).encode()).hexdigest()
    path = Path(path or os.environ.get('XAU_TD_BUDGET_DB', Path(__file__).parent/'data'/'provider_budget.sqlite'))
    path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path,timeout=10) as db:
        db.execute('CREATE TABLE IF NOT EXISTS usage (account TEXT, day TEXT, count INTEGER NOT NULL, PRIMARY KEY(account,day))')
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT count FROM usage WHERE account=? AND day=?',(identity,day)).fetchone()
        used = row[0] if row else 0
        if used >= limit:
            return False
        db.execute('INSERT INTO usage VALUES(?,?,1) ON CONFLICT(account,day) DO UPDATE SET count=count+1',(identity,day))
        db.execute('DELETE FROM usage WHERE day < date(?,\'-7 days\')',(day,))
    return True
