"""Durable immutable decision snapshots and explicitly sampled paper excursions."""
import hashlib
import json
import sqlite3
from risk_engine import timestamp, number, fresh, kill_reasons


class SetupStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS decisions (
            id TEXT PRIMARY KEY, as_of TEXT NOT NULL, config_hash TEXT NOT NULL,
            snapshot TEXT NOT NULL, decision TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS positions (
            id TEXT PRIMARY KEY REFERENCES decisions(id), side TEXT NOT NULL,
            opened_at TEXT NOT NULL, entry REAL NOT NULL, structure_level REAL NOT NULL,
            target REAL NOT NULL, large_loser_distance REAL NOT NULL,
            mae REAL NOT NULL DEFAULT 0, mfe REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'OPEN', closed_at TEXT, pnl REAL,
            large_loser INTEGER, exit_reason TEXT, last_quote_at TEXT,
            coverage TEXT NOT NULL DEFAULT 'sampled_quotes_not_full_path');
        CREATE TABLE IF NOT EXISTS observations (
            position_id TEXT NOT NULL, observed_at TEXT NOT NULL, exit_price REAL NOT NULL,
            pnl REAL NOT NULL, PRIMARY KEY(position_id, observed_at));
        ''')

    def close(self):
        self.db.close()

    def record(self, snapshot, decision):
        raw = json.dumps(snapshot, sort_keys=True, allow_nan=False)
        # Same market snapshot + config is immutable across UI reruns.
        stable_decision = {k: v for k, v in decision.items() if k != 'as_of'}
        key = hashlib.sha256((raw+json.dumps(stable_decision, sort_keys=True, allow_nan=False)).encode()).hexdigest()
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO decisions VALUES (?,?,?,?,?)',
                            (key, decision['as_of'], decision['config_hash'], raw,
                             json.dumps(decision, sort_keys=True, allow_nan=False)))
        return key

    def open_paper(self, key, snapshot, decision, c):
        if not decision['tradeable']:
            raise ValueError('blocked setup cannot open paper position')
        side = snapshot['side']
        entry = number(snapshot['quote']['ask' if side == 'LONG' else 'bid'])
        sign = 1 if side == 'LONG' else -1
        with self.db:
            if self.db.execute("SELECT 1 FROM positions WHERE status='OPEN'").fetchone():
                raise ValueError('only one paper position at a time')
            self.db.execute('''INSERT OR IGNORE INTO positions
                (id,side,opened_at,entry,structure_level,target,large_loser_distance)
                VALUES (?,?,?,?,?,?,?)''', (key,side,snapshot['as_of'],entry,
                number(snapshot['setup_level']),entry+sign*c['tp_distance'], c['large_loser_distance']))

    def update(self, snapshot, decision, c):
        self.db.row_factory = sqlite3.Row
        rows = self.db.execute("SELECT * FROM positions WHERE status='OPEN'").fetchall()
        for row in rows:
            p = dict(row)
            reasons = kill_reasons(p, snapshot, decision, c)
            try:
                q = snapshot['quote']
                observed = timestamp(q['observed_at'])
                if not fresh(q['observed_at'], timestamp(snapshot['as_of']), c['max_age_seconds']['quote'], 0):
                    continue  # never invent a fill on unavailable/stale data
                if observed < timestamp(p['opened_at']) or (p['last_quote_at'] and observed <= timestamp(p['last_quote_at'])):
                    continue
                price = number(q['bid' if p['side'] == 'LONG' else 'ask'])
                if not 0 < number(q['bid']) < number(q['ask']):
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            pnl = (price-p['entry'])*(1 if p['side'] == 'LONG' else -1)
            mae, mfe = max(p['mae'], -pnl), max(p['mfe'], pnl)
            if (price-p['target'])*(1 if p['side'] == 'LONG' else -1) >= 0:
                reasons.append('TP_OBSERVED')
            closed = bool(reasons)
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?,?)', (p['id'],q['observed_at'],price,pnl))
                self.db.execute('''UPDATE positions SET mae=?,mfe=?,last_quote_at=?,status=?,
                    closed_at=?,pnl=?,large_loser=?,exit_reason=? WHERE id=?''',
                    (mae,mfe,q['observed_at'],'CLOSED' if closed else 'OPEN',
                     q['observed_at'] if closed else None,pnl if closed else None,
                     int(mae >= p['large_loser_distance']) if closed else None,
                     ','.join(reasons) if closed else None,p['id']))

    def summary(self, c):
        count = self.db.execute('SELECT count(*) FROM decisions').fetchone()[0]
        closed, losses = self.db.execute("SELECT count(*),coalesce(sum(large_loser),0) FROM positions WHERE status='CLOSED'").fetchone()
        eligible = closed >= c['minimum_model_samples'] and losses >= c['minimum_model_losses']
        return {'decisions': count, 'closed_paper': closed, 'large_losers': losses,
                'count_threshold_met': eligible, 'large_loser_probability': None,
                'model_status': 'VALIDATION_REQUIRED' if eligible else 'INSUFFICIENT_DATA',
                'coverage': 'Sampled quotes; incomplete paths excluded from model training'}
