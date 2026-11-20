"""Persistent reservation ledger and response cache for paid calls."""

import fcntl
import sqlite3
import time
from contextlib import closing, contextmanager
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from uuid import uuid4

NANORUPEES = 1_000_000_000


class BudgetError(RuntimeError):
    """A charge would exceed the budget or its preflight allocation."""


def _units(inr, *, rounding=ROUND_CEILING):
    amount = Decimal(str(inr))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Budget amounts must be finite and nonnegative")
    return int((amount * NANORUPEES).to_integral_value(rounding=rounding))


def _inr(units):
    return units / NANORUPEES


def asr_cost(seconds):
    return _inr(_units(Decimal(str(seconds)) / 600))


def speak_cost(text):
    return _inr(len(text) * 600_000)


def translation_cost(tokens):
    return _inr(tokens * 20_000)


def _rollup(rows):
    estimated = sum(row['charge'] for row in rows if row['status'] == 'estimated')
    measured = sum(row['charge'] for row in rows if row['status'] == 'measured')
    uncertain = sum(row['charge'] for row in rows if row['status'] in {'reserved', 'uncertain'})
    return {
        'estimated_inr': _inr(estimated),
        'measured_usage_inr': _inr(measured),
        'spent_inr': _inr(estimated + measured),
        'uncertain_inr': _inr(uncertain),
        'cache_hits': sum(row['status'] == 'cached' for row in rows),
        'requests': sum(row['status'] != 'cached' for row in rows),
        'retries': sum(row['attempt'] > 1 for row in rows),
        'latency_seconds': round(sum(row['elapsed'] for row in rows), 3),
        'reported_usd': round(sum(row['reported_usd'] or 0 for row in rows), 9),
    }


class Budget:
    def __init__(self, path, cap_inr=10, prior_inr=0.02):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.locks = self.path.with_name(f'{self.path.name}.locks')
        self.locks.mkdir(exist_ok=True)
        self.run_id = uuid4().hex
        self._pool_files = {}
        with closing(self._connect()) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (
                    name TEXT PRIMARY KEY, amount INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pools (
                    id TEXT PRIMARY KEY, amount INTEGER NOT NULL, label TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS calls (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage TEXT NOT NULL,
                    language TEXT NOT NULL, key TEXT NOT NULL, pool TEXT, attempt INTEGER NOT NULL,
                    status TEXT NOT NULL, reserved INTEGER NOT NULL, charge INTEGER NOT NULL,
                    elapsed REAL NOT NULL DEFAULT 0, detail TEXT, request_id TEXT, reported_usd REAL
                );
                CREATE INDEX IF NOT EXISTS calls_run ON calls(run_id);
                CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, response BLOB NOT NULL);
            ''')
        self.cap = min(_units(cap_inr, rounding=ROUND_FLOOR), 10 * NANORUPEES)
        self.prior = _units(prior_inr)
        with self._transaction() as db:
            db.execute("INSERT OR IGNORE INTO settings VALUES ('prior', ?)", (self.prior,))
            db.execute("INSERT OR IGNORE INTO settings VALUES ('cap', ?)", (self.cap,))
            settings = dict(db.execute('SELECT name, amount FROM settings').fetchall())
            self.cap = min(self.cap, settings['cap'])
            self.prior = settings['prior']
            if self.prior > self.cap:
                raise BudgetError('Prior smoke estimate already exceeds the configured cap')

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def _transaction(self):
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            yield db

    @contextmanager
    def slot(self, name, count=1, timeout=180):
        """Use a bounded number of process-wide slots, including across CLI invocations."""
        deadline = time.monotonic() + timeout
        while True:
            for index in range(count):
                with (self.locks / f'{name}-{index}').open('ab') as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    yield
                    return
            if time.monotonic() >= deadline:
                raise TimeoutError(f'Timed out waiting for {name} capacity')
            time.sleep(0.05)

    def _reap_crashed_pools(self, db):
        for row in db.execute('SELECT id FROM pools').fetchall():
            pool = row['id']
            path = self.locks / f'pool-{pool}'
            with path.open('ab') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                db.execute('DELETE FROM pools WHERE id = ?', (pool,))
                path.unlink(missing_ok=True)

    def _available(self, db):
        spent = db.execute('SELECT COALESCE(SUM(charge), 0) FROM calls').fetchone()[0]
        held = db.execute('SELECT COALESCE(SUM(amount), 0) FROM pools').fetchone()[0]
        return self.cap - self.prior - spent - held

    def hold(self, inr, label='baseline'):
        """Protect forecast capacity before dispatch. Passed calls consume the pool."""
        amount = _units(inr)
        pool = uuid4().hex
        path = self.locks / f'pool-{pool}'
        lock = path.open('ab')
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with self._transaction() as db:
                self._reap_crashed_pools(db)
                available = self._available(db)
                if amount > available:
                    raise BudgetError(f'{label} requires ₹{_inr(amount):.6f}, available ₹{_inr(available):.6f}')
                db.execute('INSERT INTO pools VALUES (?, ?, ?)', (pool, amount, label))
        except BaseException:
            lock.close()
            path.unlink(missing_ok=True)
            raise
        self._pool_files[pool] = lock
        return pool

    def release(self, pool):
        """Release unused capacity. In-flight and uncertain charges remain reserved."""
        lock = self._pool_files.get(pool)
        if lock is None:
            return
        with self._transaction() as db:
            db.execute('DELETE FROM pools WHERE id = ?', (pool,))
        self._pool_files.pop(pool).close()
        (self.locks / f'pool-{pool}').unlink(missing_ok=True)

    def cached(self, key):
        with closing(self._connect()) as db:
            row = db.execute('SELECT response FROM cache WHERE key = ?', (key,)).fetchone()
        return row['response'] if row else None

    def cache_hit(self, stage, language, key):
        with self._transaction() as db:
            db.execute('''INSERT INTO calls (id, run_id, stage, language, key, attempt, status, reserved, charge)
                          VALUES (?, ?, ?, ?, ?, 0, 'cached', 0, 0)''', (uuid4().hex, self.run_id, stage, language, key))

    def reserve(self, inr, *, stage, language, key, attempt=1, pool=None, category_cap_inr=None):
        amount = _units(inr)
        call_id = uuid4().hex
        with self._transaction() as db:
            self._reap_crashed_pools(db)
            if category_cap_inr is not None:
                spent = db.execute('SELECT COALESCE(SUM(charge), 0) FROM calls WHERE language = ?', (language,)).fetchone()[0]
                if spent + amount > _units(category_cap_inr, rounding=ROUND_FLOOR):
                    raise BudgetError(f'{stage} exceeds the ₹{category_cap_inr:.6f} allocation for {language}')
            if pool is None:
                available = self._available(db)
            else:
                row = db.execute('SELECT amount FROM pools WHERE id = ?', (pool,)).fetchone()
                available = row['amount'] if row else 0
            if amount > available:
                raise BudgetError(f'{stage} for {language} requires ₹{_inr(amount):.6f}, available ₹{_inr(available):.6f}')
            if pool is not None:
                db.execute('UPDATE pools SET amount = amount - ? WHERE id = ?', (amount, pool))
            db.execute('''INSERT INTO calls (id, run_id, stage, language, key, pool, attempt, status, reserved, charge)
                          VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?)''',
                       (call_id, self.run_id, stage, language, key, pool, attempt, amount, amount))
        return call_id

    def finish(self, call_id, status, *, elapsed, detail=None, request_id=None, cost_inr=None, reported_usd=None, response=None):
        with self._transaction() as db:
            row = db.execute("SELECT * FROM calls WHERE id = ? AND status = 'reserved'", (call_id,)).fetchone()
            if row is None:
                raise RuntimeError('Call reservation is unavailable')
            charge = row['reserved'] if cost_inr is None else _units(cost_inr)
            if status == 'rejected':
                charge = 0
            db.execute('''UPDATE calls SET status = ?, charge = ?, elapsed = ?, detail = ?, request_id = ?, reported_usd = ?
                          WHERE id = ?''', (status, charge, elapsed, detail, request_id, reported_usd, call_id))
            if charge < row['reserved'] and row['pool'] is not None:
                db.execute('UPDATE pools SET amount = amount + ? WHERE id = ?', (row['reserved'] - charge, row['pool']))
            if response is not None:
                db.execute('INSERT OR IGNORE INTO cache VALUES (?, ?)', (row['key'], response))

    def reconcile(self, call_id, actual_inr):
        """Record independently confirmed spend for an uncertain or estimated charge."""
        with self._transaction() as db:
            result = db.execute("""UPDATE calls SET status = 'measured', charge = ? WHERE id = ?
                                   AND status IN ('reserved', 'uncertain', 'estimated')""", (_units(actual_inr), call_id))
            if result.rowcount != 1:
                raise ValueError('Call does not have a charge to reconcile')

    def snapshot(self):
        with self._transaction() as db:
            self._reap_crashed_pools(db)
            lifetime_rows = db.execute('SELECT * FROM calls ORDER BY rowid').fetchall()
            available = self._available(db)
            held = db.execute('SELECT COALESCE(SUM(amount), 0) FROM pools').fetchone()[0]
        rows = [row for row in lifetime_rows if row['run_id'] == self.run_id]
        return {
            'cap_inr': _inr(self.cap),
            'prior_smoke_estimate_inr': _inr(self.prior),
            'available_inr': _inr(available),
            'held_inr': _inr(held),
            'lifetime': _rollup(lifetime_rows),
            'run': _rollup(rows),
            'by_stage': {stage: _rollup([row for row in rows if row['stage'] == stage])
                         for stage in sorted({row['stage'] for row in rows})},
            'by_language': {lang: _rollup([row for row in rows if row['language'] == lang])
                            for lang in sorted({row['language'] for row in rows})},
            'calls': [{
                'id': row['id'], 'cache_key': row['key'], 'stage': row['stage'], 'language': row['language'],
                'status': row['status'], 'attempt': row['attempt'], 'elapsed_seconds': round(row['elapsed'], 3),
                'reserved_inr': _inr(row['reserved']), 'charge_inr': _inr(row['charge']),
                'detail': row['detail'], 'request_id': row['request_id'], 'reported_usd': row['reported_usd'],
            } for row in rows],
        }
