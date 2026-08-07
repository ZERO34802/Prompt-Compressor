"""Hand-written seed corpus of realistic multi-turn coding conversations.

Every conversation follows the same arc the whole project is about: the developer
opens with something nearly useless ("my code is broken"), and the real detail --
file names, line numbers, function names, the actual root cause -- only leaks out
over the following turns. The final assistant turn always contains the working fix.

This module has zero external dependencies on purpose: it is the fallback that
guarantees the pipeline runs offline.
"""

from __future__ import annotations


def _c(conv_id: str, language: str, turns: list[tuple[str, str]]) -> dict:
    return {
        "conv_id": conv_id,
        "language": language,
        "turns": [{"role": role, "content": content.strip()} for role, content in turns],
    }


def make_seed() -> list[dict]:
    """Return 15 multi-turn coding conversations (mixed python / javascript)."""
    return [
        _c(
            "seed-001",
            "python",
            [
                ("user", "my code is broken, can you help"),
                (
                    "assistant",
                    "Happy to help. I need a bit more to go on though -- what is the code "
                    "supposed to do, and what actually happens when you run it? If there's "
                    "a traceback, paste the whole thing including the exception type.",
                ),
                (
                    "user",
                    "it's a flask api and logging people in fails sometimes. not always. "
                    "works when i just logged in but breaks the next morning",
                ),
                (
                    "assistant",
                    "\"Works now, fails later\" almost always points at an expiry or timestamp "
                    "comparison. A few questions:\n"
                    "1. Where does the login flow check token validity?\n"
                    "2. Are you storing the expiry as a naive or timezone-aware datetime?\n"
                    "3. What's the actual error the client sees -- a 401, or a 500?\n"
                    "Paste the function that validates the token.",
                ),
                (
                    "user",
                    "it's in auth.py, function refresh_token(). client gets a 500 and the log says "
                    "TypeError: can't compare offset-naive and offset-aware datetimes. here it is:\n\n"
                    "```python\n"
                    "def refresh_token(token_row):\n"
                    "    if token_row.expires_at < datetime.now():\n"
                    "        raise TokenExpired(token_row.id)\n"
                    "    return issue_access_token(token_row.user_id)\n"
                    "```\n\n"
                    "expires_at comes out of postgres as a timestamptz column",
                ),
                (
                    "assistant",
                    "That's the whole bug. `expires_at` is timezone-aware because the column is "
                    "`timestamptz`, but `datetime.now()` returns a naive local datetime, so the "
                    "`<` comparison raises `TypeError` and Flask turns the uncaught exception into a 500.\n\n"
                    "It only reproduces \"the next morning\" because when the row is fresh some code "
                    "paths short-circuit before the comparison.\n\n"
                    "Fix in `auth.py`:\n\n"
                    "```python\n"
                    "from datetime import datetime, timezone\n\n"
                    "def refresh_token(token_row):\n"
                    "    if token_row.expires_at <= datetime.now(timezone.utc):\n"
                    "        raise TokenExpired(token_row.id)\n"
                    "    return issue_access_token(token_row.user_id)\n"
                    "```\n\n"
                    "Two details worth keeping: use `datetime.now(timezone.utc)` rather than "
                    "`utcnow()` (which also returns a naive object and would reintroduce the same "
                    "bug), and use `<=` so a token expiring exactly now is treated as expired. "
                    "Also add a handler so `TokenExpired` maps to a 401 instead of a 500:\n\n"
                    "```python\n"
                    "@app.errorhandler(TokenExpired)\n"
                    "def _expired(err):\n"
                    "    return {'error': 'token_expired'}, 401\n"
                    "```",
                ),
            ],
        ),
        _c(
            "seed-002",
            "javascript",
            [
                ("user", "something's wrong with my code, help please"),
                (
                    "assistant",
                    "Sure -- what's the stack (Node, browser, framework?), what were you trying "
                    "to do, and what's the error message or wrong output?",
                ),
                (
                    "user",
                    "express api. when two people sign up with the same email the server returns "
                    "500 instead of a nice error",
                ),
                (
                    "assistant",
                    "So the database unique constraint is firing and the rejection is escaping your "
                    "handler. Which file is the route in, and are you using an ORM or raw pg? The "
                    "error code in the log would help -- Postgres uses `23505` for unique violations.",
                ),
                (
                    "user",
                    "api/routes.js, raw pg client. log shows `error: duplicate key value violates "
                    "unique constraint \"users_email_key\"` with code 23505. handler is:\n\n"
                    "```js\n"
                    "router.post('/users', async (req, res) => {\n"
                    "  const { email, name } = req.body;\n"
                    "  const { rows } = await db.query(\n"
                    "    'INSERT INTO users (email, name) VALUES ($1, $2) RETURNING id',\n"
                    "    [email, name]\n"
                    "  );\n"
                    "  res.status(201).json({ id: rows[0].id });\n"
                    "});\n"
                    "```",
                ),
                (
                    "assistant",
                    "The `await` rejects, nothing catches it, and Express's default error handler "
                    "returns a bare 500. You want to catch the specific Postgres error code and "
                    "translate it to a 409 Conflict.\n\n"
                    "In `api/routes.js`:\n\n"
                    "```js\n"
                    "router.post('/users', async (req, res, next) => {\n"
                    "  const { email, name } = req.body;\n"
                    "  if (!email || !name) {\n"
                    "    return res.status(400).json({ error: 'email and name are required' });\n"
                    "  }\n"
                    "  try {\n"
                    "    const { rows } = await db.query(\n"
                    "      'INSERT INTO users (email, name) VALUES ($1, $2) RETURNING id',\n"
                    "      [email, name]\n"
                    "    );\n"
                    "    return res.status(201).json({ id: rows[0].id });\n"
                    "  } catch (err) {\n"
                    "    if (err.code === '23505' && err.constraint === 'users_email_key') {\n"
                    "      return res.status(409).json({ error: 'email_already_registered' });\n"
                    "    }\n"
                    "    return next(err);\n"
                    "  }\n"
                    "});\n"
                    "```\n\n"
                    "Checking `err.constraint` as well as `err.code` matters -- otherwise a future "
                    "unique index on, say, `username` would also report itself as a duplicate email. "
                    "Passing anything unexpected to `next(err)` keeps real bugs visible instead of "
                    "swallowing them as 409s.",
                ),
            ],
        ),
        _c(
            "seed-003",
            "python",
            [
                ("user", "my parser is broken, can you help"),
                (
                    "assistant",
                    "What kind of parser, and what's the symptom -- wrong output, exception, or hang? "
                    "If some inputs work and others don't, the smallest failing input plus the "
                    "function that splits the text would tell me most of what I need.",
                ),
                (
                    "user",
                    "it reads a config format i made up. most files parse fine but a couple lose the "
                    "last setting -- and now that you mention it the broken ones don't end with a "
                    "newline. it's parser.py, tokenize() around line 88:\n\n"
                    "```python\n"
                    "def tokenize(text):\n"
                    "    tokens = []\n"
                    "    buf = ''\n"
                    "    for ch in text:\n"
                    "        if ch == '\\n':\n"
                    "            tokens.append(buf)\n"
                    "            buf = ''\n"
                    "        else:\n"
                    "            buf += ch\n"
                    "    return tokens\n"
                    "```",
                ),
                (
                    "assistant",
                    "Confirmed off-by-one: you only flush `buf` when you *see* a newline, so whatever "
                    "is in `buf` when the string ends is silently dropped. Files with a trailing "
                    "newline work by accident.\n\n"
                    "Fix in `parser.py`:\n\n"
                    "```python\n"
                    "def tokenize(text):\n"
                    "    tokens = []\n"
                    "    buf = ''\n"
                    "    for ch in text:\n"
                    "        if ch == '\\n':\n"
                    "            tokens.append(buf)\n"
                    "            buf = ''\n"
                    "        else:\n"
                    "            buf += ch\n"
                    "    if buf:\n"
                    "        tokens.append(buf)   # flush the final unterminated line\n"
                    "    return tokens\n"
                    "```\n\n"
                    "Guarding with `if buf:` keeps the old behaviour for input that *does* end in a "
                    "newline, so you don't start emitting a spurious empty token.\n\n"
                    "A regression test that would have caught this:\n\n"
                    "```python\n"
                    "def test_tokenize_without_trailing_newline():\n"
                    "    assert tokenize('a\\nb') == ['a', 'b']\n"
                    "```",
                ),
            ],
        ),
        _c(
            "seed-004",
            "javascript",
            [
                ("user", "help, my react app is broken"),
                (
                    "assistant",
                    "What's broken specifically -- a crash, a blank screen, a warning in the console, "
                    "or wrong behaviour on screen?",
                ),
                (
                    "user",
                    "the console keeps warning about too many re-renders and eventually the tab freezes. "
                    "it's a dashboard page that fetches stats",
                ),
                (
                    "assistant",
                    "\"Too many re-renders\" plus a freeze is almost always a `useEffect` whose "
                    "dependency changes every render, so the effect sets state, which re-renders, "
                    "which recreates the dependency. Paste the component's hooks -- especially any "
                    "`useEffect` that calls `setState`.",
                ),
                (
                    "user",
                    "it's src/components/Dashboard.jsx:\n\n"
                    "```jsx\n"
                    "function Dashboard({ userId }) {\n"
                    "  const [stats, setStats] = useState(null);\n"
                    "  const filters = { userId, range: '30d' };\n\n"
                    "  useEffect(() => {\n"
                    "    fetchStats(filters).then(setStats);\n"
                    "  }, [filters]);\n\n"
                    "  return <StatGrid stats={stats} />;\n"
                    "}\n"
                    "```",
                ),
                (
                    "assistant",
                    "There it is. `filters` is a fresh object literal on every render, so "
                    "`[filters]` never compares equal (React uses `Object.is`), the effect re-runs, "
                    "`setStats` triggers another render, and you're in an infinite loop.\n\n"
                    "Fix in `src/components/Dashboard.jsx` -- depend on the primitive values instead "
                    "of the object identity:\n\n"
                    "```jsx\n"
                    "function Dashboard({ userId }) {\n"
                    "  const [stats, setStats] = useState(null);\n"
                    "  const range = '30d';\n\n"
                    "  useEffect(() => {\n"
                    "    let cancelled = false;\n"
                    "    fetchStats({ userId, range }).then((s) => {\n"
                    "      if (!cancelled) setStats(s);\n"
                    "    });\n"
                    "    return () => { cancelled = true; };\n"
                    "  }, [userId, range]);\n\n"
                    "  return <StatGrid stats={stats} />;\n"
                    "}\n"
                    "```\n\n"
                    "If you genuinely need the object elsewhere in the component, wrap it instead: "
                    "`const filters = useMemo(() => ({ userId, range }), [userId, range])`. The "
                    "`cancelled` flag is a separate small fix -- without it a slow response for an "
                    "old `userId` can land after a newer one and overwrite it.",
                ),
            ],
        ),
        _c(
            "seed-005",
            "python",
            [
                ("user", "my script is broken and i can't figure out why"),
                (
                    "assistant",
                    "Let's narrow it down -- what does the script do, and what's the failure? Paste "
                    "the traceback if there is one.",
                ),
                (
                    "user",
                    "it's a data cleaning script over a csv of orders. it runs fine but the totals "
                    "are wrong, like way too low",
                ),
                (
                    "assistant",
                    "No exception but wrong aggregates suggests rows are being skipped or a column is "
                    "being coerced badly. Two things to check: are any values `NaN` after parsing, and "
                    "does the row count after cleaning match what you expect? Paste the cleaning function.",
                ),
                (
                    "user",
                    "row count drops from 48211 to 31004, which is way more than i expected. it's in "
                    "etl/clean.py, clean_orders():\n\n"
                    "```python\n"
                    "def clean_orders(df):\n"
                    "    df['amount'] = pd.to_numeric(df['amount'], errors='coerce')\n"
                    "    df = df.dropna()\n"
                    "    return df\n"
                    "```\n\n"
                    "amount looks fine, but there's a discount_code column that's empty for most orders",
                ),
                (
                    "assistant",
                    "That's it. `df.dropna()` with no arguments drops any row with a null in *any* "
                    "column, so every order without a `discount_code` gets thrown away -- roughly the "
                    "17k rows you're missing. Your totals are low because you deleted the orders, not "
                    "because the amounts are wrong.\n\n"
                    "Fix in `etl/clean.py`:\n\n"
                    "```python\n"
                    "def clean_orders(df):\n"
                    "    df = df.copy()\n"
                    "    df['amount'] = pd.to_numeric(df['amount'], errors='coerce')\n"
                    "    df = df.dropna(subset=['amount'])          # only drop unparseable amounts\n"
                    "    df['discount_code'] = df['discount_code'].fillna('')\n"
                    "    return df\n"
                    "```\n\n"
                    "Scoping `dropna` with `subset=` is the actual fix. The `.copy()` avoids a "
                    "`SettingWithCopyWarning` when the caller passes a slice, and filling "
                    "`discount_code` with `''` makes the \"no discount\" case explicit downstream.\n\n"
                    "Worth logging the drop so this can't hide again:\n\n"
                    "```python\n"
                    "logger.info('clean_orders dropped %d of %d rows', before - len(df), before)\n"
                    "```",
                ),
            ],
        ),
        _c(
            "seed-006",
            "javascript",
            [
                ("user", "code broken, need help"),
                (
                    "assistant",
                    "Give me a little more -- what are you building, and what goes wrong?",
                ),
                (
                    "user",
                    "node script that hits an api for a list of ids. it works with 10 ids but with "
                    "2000 it crashes",
                ),
                (
                    "assistant",
                    "Crashing only at scale points at unbounded concurrency -- firing 2000 requests at "
                    "once exhausts sockets or memory. What's the actual error (`ECONNRESET`, "
                    "`ETIMEDOUT`, heap out of memory)? And paste the loop that issues the requests.",
                ),
                (
                    "user",
                    "it's `FetchError: request to https://api.internal/items/... failed, reason: connect "
                    "ETIMEDOUT` and sometimes the heap OOM one. code is scripts/sync-items.js:\n\n"
                    "```js\n"
                    "const results = await Promise.all(\n"
                    "  ids.map((id) => fetch(`${BASE}/items/${id}`).then((r) => r.json()))\n"
                    ");\n"
                    "```",
                ),
                (
                    "assistant",
                    "Exactly as suspected: `Promise.all` over 2000 ids opens 2000 sockets "
                    "simultaneously, the server starts timing out, and holding every in-flight "
                    "response blows the heap. You need a bounded worker pool.\n\n"
                    "Fix in `scripts/sync-items.js` -- no dependencies needed:\n\n"
                    "```js\n"
                    "async function mapWithConcurrency(items, limit, fn) {\n"
                    "  const results = new Array(items.length);\n"
                    "  let next = 0;\n"
                    "  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {\n"
                    "    while (next < items.length) {\n"
                    "      const i = next++;\n"
                    "      results[i] = await fn(items[i], i);\n"
                    "    }\n"
                    "  });\n"
                    "  await Promise.all(workers);\n"
                    "  return results;\n"
                    "}\n\n"
                    "const results = await mapWithConcurrency(ids, 8, async (id) => {\n"
                    "  const res = await fetch(`${BASE}/items/${id}`);\n"
                    "  if (!res.ok) throw new Error(`item ${id}: HTTP ${res.status}`);\n"
                    "  return res.json();\n"
                    "});\n"
                    "```\n\n"
                    "Eight workers drain a shared cursor, so there are never more than eight sockets "
                    "open regardless of list size, and results stay in input order because each "
                    "worker writes to its own index. The `res.ok` check matters too -- your original "
                    "called `.json()` on error pages, which fails with a confusing parse error "
                    "instead of the real HTTP status.",
                ),
            ],
        ),
        _c(
            "seed-007",
            "python",
            [
                ("user", "my tests are broken, can you help"),
                (
                    "assistant",
                    "What's failing, and is it consistent or intermittent? Paste the pytest output. "
                    "Also worth checking: do the tests pass when you run them one at a time? If so, "
                    "show me any fixture the failing tests share.",
                ),
                (
                    "user",
                    "that's the annoying part -- they pass individually but fail as a suite, and which "
                    "one fails moves around. `AssertionError: assert 3 == 1` in "
                    "tests/test_cart.py::test_add_single_item. the fixture is in conftest.py:\n\n"
                    "```python\n"
                    "@pytest.fixture(scope='module')\n"
                    "def cart():\n"
                    "    return Cart()\n"
                    "```",
                ),
                (
                    "assistant",
                    "That's the leak. `scope='module'` means every test in the file shares one `Cart` "
                    "instance, so items added by earlier tests are still there -- hence `3 == 1`. It "
                    "moves around because pytest's ordering changes what has run before.\n\n"
                    "Fix in `conftest.py`:\n\n"
                    "```python\n"
                    "@pytest.fixture                      # function scope is the default\n"
                    "def cart():\n"
                    "    return Cart()\n"
                    "```\n\n"
                    "Each test now gets a fresh cart. Module scope is only appropriate for expensive "
                    "*immutable* setup -- a read-only database connection, a compiled schema -- never "
                    "for an object tests mutate.\n\n"
                    "To stop this class of bug recurring, add `-p no:randomly` off and instead run the "
                    "suite shuffled in CI:\n\n"
                    "```bash\n"
                    "pytest -p randomly --randomly-seed=last\n"
                    "```\n\n"
                    "Order-dependent state then fails loudly on the first run rather than weeks later.",
                ),
            ],
        ),
        _c(
            "seed-008",
            "javascript",
            [
                ("user", "hey my code doesn't work, any ideas"),
                (
                    "assistant",
                    "I'd need to know what it does and how it fails -- error message, wrong output, "
                    "or nothing happening at all?",
                ),
                (
                    "user",
                    "form submit button. clicking it reloads the whole page instead of saving, and my "
                    "console.log never shows up",
                ),
                (
                    "assistant",
                    "A full page reload on submit means the browser's native form submission is still "
                    "happening -- your handler either isn't attached or isn't preventing the default. "
                    "The missing log suggests the former. Paste the form and the handler.",
                ),
                (
                    "user",
                    "src/SignupForm.jsx:\n\n"
                    "```jsx\n"
                    "function SignupForm() {\n"
                    "  const [email, setEmail] = useState('');\n\n"
                    "  async function handleSubmit() {\n"
                    "    console.log('submitting', email);\n"
                    "    await api.signup(email);\n"
                    "  }\n\n"
                    "  return (\n"
                    "    <form>\n"
                    "      <input value={email} onChange={(e) => setEmail(e.target.value)} />\n"
                    "      <button onClick={handleSubmit}>Sign up</button>\n"
                    "    </form>\n"
                    "  );\n"
                    "}\n"
                    "```",
                ),
                (
                    "assistant",
                    "Two things compound here. The button has no `type`, so it defaults to "
                    "`type=\"submit\"` and triggers the form's native submission -- that's your page "
                    "reload. And `handleSubmit` is wired to the button's click rather than the form's "
                    "submit event and never calls `preventDefault`, so the reload tears the page down "
                    "before the log flushes.\n\n"
                    "Fix in `src/SignupForm.jsx` -- handle `onSubmit` on the form:\n\n"
                    "```jsx\n"
                    "function SignupForm() {\n"
                    "  const [email, setEmail] = useState('');\n"
                    "  const [busy, setBusy] = useState(false);\n\n"
                    "  async function handleSubmit(e) {\n"
                    "    e.preventDefault();\n"
                    "    setBusy(true);\n"
                    "    try {\n"
                    "      await api.signup(email);\n"
                    "    } finally {\n"
                    "      setBusy(false);\n"
                    "    }\n"
                    "  }\n\n"
                    "  return (\n"
                    "    <form onSubmit={handleSubmit}>\n"
                    "      <input value={email} onChange={(e) => setEmail(e.target.value)} />\n"
                    "      <button type=\"submit\" disabled={busy}>Sign up</button>\n"
                    "    </form>\n"
                    "  );\n"
                    "}\n"
                    "```\n\n"
                    "Using `onSubmit` rather than the button's `onClick` also gets you Enter-to-submit "
                    "and native validation for free. The `busy` flag prevents a double signup from a "
                    "fast second click.",
                ),
            ],
        ),
        _c(
            "seed-009",
            "python",
            [
                ("user", "broken code, please help"),
                (
                    "assistant",
                    "What's the code doing and what's going wrong? A traceback or sample of the bad "
                    "output would help.",
                ),
                (
                    "user",
                    "a function that builds a list of tags for records. every record ends up with all "
                    "the tags from every other record",
                ),
                (
                    "assistant",
                    "Every caller sharing one list is the classic mutable-default-argument bug: "
                    "`def f(x, acc=[])` evaluates `[]` once at definition time, so all calls append "
                    "to the same list. Paste the signature.",
                ),
                (
                    "user",
                    "yeah that's exactly what it looks like. utils/tagging.py:\n\n"
                    "```python\n"
                    "def collect_tags(record, tags=[]):\n"
                    "    tags.append(record['kind'])\n"
                    "    for child in record.get('children', []):\n"
                    "        collect_tags(child, tags)\n"
                    "    return tags\n"
                    "```",
                ),
                (
                    "assistant",
                    "Confirmed. The default `[]` is created once when the module is imported, so it "
                    "persists across every top-level call and accumulates forever.\n\n"
                    "Fix in `utils/tagging.py` -- use `None` as the sentinel:\n\n"
                    "```python\n"
                    "def collect_tags(record, tags=None):\n"
                    "    if tags is None:\n"
                    "        tags = []\n"
                    "    tags.append(record['kind'])\n"
                    "    for child in record.get('children', []):\n"
                    "        collect_tags(child, tags)\n"
                    "    return tags\n"
                    "```\n\n"
                    "The recursive call still passes the same list down, which is what you want -- "
                    "only the top-level entry point gets a fresh one.\n\n"
                    "If you'd rather not expose the accumulator at all, hide it:\n\n"
                    "```python\n"
                    "def collect_tags(record):\n"
                    "    tags = [record['kind']]\n"
                    "    for child in record.get('children', []):\n"
                    "        tags.extend(collect_tags(child))\n"
                    "    return tags\n"
                    "```\n\n"
                    "That version is harder to misuse since there's no shared state to leak.",
                ),
            ],
        ),
        _c(
            "seed-010",
            "javascript",
            [
                ("user", "my code is broken can you help me"),
                (
                    "assistant",
                    "Of course -- what's the symptom, and which part of the codebase is involved? If "
                    "it's an endpoint returning the wrong thing, paste the handler.",
                ),
                (
                    "user",
                    "express endpoint returns an empty array even though the db definitely has rows. "
                    "src/controllers/orders.js:\n\n"
                    "```js\n"
                    "exports.listOrders = (req, res) => {\n"
                    "  const orders = [];\n"
                    "  db.query('SELECT * FROM orders WHERE user_id = $1', [req.user.id])\n"
                    "    .then(({ rows }) => {\n"
                    "      orders.push(...rows);\n"
                    "    });\n"
                    "  res.json(orders);\n"
                    "};\n"
                    "```",
                ),
                (
                    "assistant",
                    "That's the race. `res.json(orders)` runs synchronously, before the `.then` "
                    "callback ever fires, so it always serialises the empty array. The rows do arrive "
                    "-- just a few milliseconds after the response was already sent.\n\n"
                    "Fix in `src/controllers/orders.js`:\n\n"
                    "```js\n"
                    "exports.listOrders = async (req, res, next) => {\n"
                    "  try {\n"
                    "    const { rows } = await db.query(\n"
                    "      'SELECT * FROM orders WHERE user_id = $1',\n"
                    "      [req.user.id]\n"
                    "    );\n"
                    "    res.json(rows);\n"
                    "  } catch (err) {\n"
                    "    next(err);\n"
                    "  }\n"
                    "};\n"
                    "```\n\n"
                    "`await` makes the ordering explicit and drops the pointless accumulator array. "
                    "The `try/catch` with `next(err)` matters because an async handler that rejects "
                    "without one leaves the request hanging until it times out -- Express 4 doesn't "
                    "catch async rejections for you.",
                ),
            ],
        ),
        _c(
            "seed-011",
            "python",
            [
                ("user", "my code is broken, can you help"),
                (
                    "assistant",
                    "Yes -- tell me what it's meant to do and how it's failing. Traceback welcome.",
                ),
                (
                    "user",
                    "django view that exports a report. it times out in prod but is instant on my "
                    "laptop with test data",
                ),
                (
                    "assistant",
                    "Fast locally, slow in prod, with a small local dataset -- that's the shape of an "
                    "N+1 query problem. Ten test rows means ten extra queries you don't notice; "
                    "50,000 production rows means 50,000. Paste the view and say which related fields "
                    "you touch in the loop.",
                ),
                (
                    "user",
                    "reports/views.py, export_orders(). django debug toolbar says 41k queries:\n\n"
                    "```python\n"
                    "def export_orders(request):\n"
                    "    rows = []\n"
                    "    for order in Order.objects.filter(status='shipped'):\n"
                    "        rows.append({\n"
                    "            'id': order.id,\n"
                    "            'customer': order.customer.name,\n"
                    "            'warehouse': order.warehouse.city,\n"
                    "            'items': [i.sku for i in order.items.all()],\n"
                    "        })\n"
                    "    return JsonResponse({'rows': rows})\n"
                    "```",
                ),
                (
                    "assistant",
                    "Textbook N+1. Each iteration lazily fetches `order.customer`, `order.warehouse`, "
                    "and `order.items` -- three extra queries per order, which is your 41k.\n\n"
                    "Fix in `reports/views.py`:\n\n"
                    "```python\n"
                    "def export_orders(request):\n"
                    "    queryset = (\n"
                    "        Order.objects.filter(status='shipped')\n"
                    "        .select_related('customer', 'warehouse')      # FK -> SQL JOIN\n"
                    "        .prefetch_related('items')                    # reverse FK -> one extra query\n"
                    "        .only('id', 'customer__name', 'warehouse__city')\n"
                    "    )\n"
                    "    rows = [\n"
                    "        {\n"
                    "            'id': o.id,\n"
                    "            'customer': o.customer.name,\n"
                    "            'warehouse': o.warehouse.city,\n"
                    "            'items': [i.sku for i in o.items.all()],\n"
                    "        }\n"
                    "        for o in queryset.iterator(chunk_size=2000)\n"
                    "    ]\n"
                    "    return JsonResponse({'rows': rows})\n"
                    "```\n\n"
                    "That takes you from ~41,000 queries to 2: one join for the forward "
                    "foreign keys, one `IN` query for the prefetched items. `select_related` is for "
                    "forward FKs (it joins), `prefetch_related` for reverse/many-to-many (it does a "
                    "second query and stitches in Python) -- using the wrong one silently keeps the "
                    "N+1.\n\n"
                    "One caveat: `.iterator()` and `prefetch_related` only cooperate when you pass "
                    "`chunk_size`, as above. Without it the prefetch is dropped and you're back to "
                    "N+1.",
                ),
            ],
        ),
        _c(
            "seed-012",
            "javascript",
            [
                ("user", "everything's broken, help"),
                (
                    "assistant",
                    "Let's find out what \"everything\" is -- what did you change most recently, and "
                    "what's the exact error? If it's a Node build, also tell me whether you touched "
                    "package.json.",
                ),
                (
                    "user",
                    "i added a new module and set \"type\": \"module\" in package.json so i could use "
                    "import in it. now the build fails:\n\n"
                    "```\n"
                    "file:///app/src/db.js:1\n"
                    "const { Pool } = require('pg');\n"
                    "                 ^\n"
                    "ReferenceError: require is not defined in ES module scope, you can use import instead\n"
                    "```\n\n"
                    "src/db.js is old code i didn't touch",
                ),
                (
                    "assistant",
                    "Right -- `\"type\": \"module\"` applies to every `.js` file in the package, so your "
                    "untouched CommonJS files are now being parsed as ESM. Two clean ways out; pick "
                    "based on how much legacy code you have.\n\n"
                    "**If most files are still CommonJS** -- revert the flag and rename just the new "
                    "file to `.mjs`:\n\n"
                    "```bash\n"
                    "# remove \"type\": \"module\" from package.json\n"
                    "mv src/newthing.js src/newthing.mjs\n"
                    "```\n\n"
                    "**If you want to move to ESM properly** -- keep the flag and convert `src/db.js`:\n\n"
                    "```js\n"
                    "import pg from 'pg';\n"
                    "const { Pool } = pg;\n\n"
                    "export const pool = new Pool({ connectionString: process.env.DATABASE_URL });\n"
                    "```\n\n"
                    "Note `import pg from 'pg'` then destructuring, rather than "
                    "`import { Pool } from 'pg'` -- `pg` is CommonJS, and named imports from CJS only "
                    "work when Node's static analysis can detect the exports, which it can't reliably "
                    "do here. You'll also need to add explicit file extensions to every relative "
                    "import (`./db.js`, not `./db`), since ESM doesn't do extension resolution.",
                ),
            ],
        ),
        _c(
            "seed-013",
            "python",
            [
                ("user", "can you help, my code is broken"),
                (
                    "assistant",
                    "Sure. What's it supposed to do, and what happens instead?",
                ),
                (
                    "user",
                    "a fastapi endpoint that uploads files to s3. small files work, big ones fail",
                ),
                (
                    "assistant",
                    "Works small, fails large is usually memory or a timeout. What's the error, how "
                    "big is \"big\", and are you reading the upload into memory before sending it? "
                    "Paste the endpoint.",
                ),
                (
                    "user",
                    "anything over ~200MB. the container gets OOM killed. api/upload.py:\n\n"
                    "```python\n"
                    "@app.post('/upload')\n"
                    "async def upload(file: UploadFile):\n"
                    "    contents = await file.read()\n"
                    "    s3.put_object(Bucket=BUCKET, Key=file.filename, Body=contents)\n"
                    "    return {'key': file.filename}\n"
                    "```",
                ),
                (
                    "assistant",
                    "`await file.read()` with no argument pulls the entire upload into a single "
                    "`bytes` object, so a 200MB file needs at least 200MB of RSS -- more once boto3 "
                    "copies it for the request body. That's your OOM.\n\n"
                    "Fix in `api/upload.py` -- stream it instead, and let boto3 do a multipart upload:\n\n"
                    "```python\n"
                    "from boto3.s3.transfer import TransferConfig\n\n"
                    "_CONFIG = TransferConfig(multipart_threshold=8 * 1024 * 1024,\n"
                    "                         multipart_chunksize=8 * 1024 * 1024)\n\n"
                    "@app.post('/upload')\n"
                    "async def upload(file: UploadFile):\n"
                    "    key = safe_key(file.filename)\n"
                    "    s3.upload_fileobj(file.file, BUCKET, key, Config=_CONFIG)\n"
                    "    return {'key': key}\n"
                    "```\n\n"
                    "`upload_fileobj` reads from the file object in 8MB chunks, so peak memory is "
                    "bounded by the chunk size and concurrency rather than by the file size. Note it "
                    "takes `file.file` (the underlying `SpooledTemporaryFile`), not the `UploadFile` "
                    "wrapper.\n\n"
                    "Two related things while you're in here. `upload_fileobj` is blocking, so on a "
                    "busy server move it off the event loop:\n\n"
                    "```python\n"
                    "await anyio.to_thread.run_sync(\n"
                    "    lambda: s3.upload_fileobj(file.file, BUCKET, key, Config=_CONFIG)\n"
                    ")\n"
                    "```\n\n"
                    "And never use `file.filename` directly as the S3 key -- a client-supplied name "
                    "containing `../` or a leading `/` lets them write outside the prefix you expect. "
                    "Hence `safe_key()`.",
                ),
            ],
        ),
        _c(
            "seed-014",
            "javascript",
            [
                ("user", "my code broke and i don't know why, help"),
                (
                    "assistant",
                    "What kind of project, and what's the failure -- exception, wrong values, or "
                    "something visual?",
                ),
                (
                    "user",
                    "a util that groups items by date for a chart. the dates are off by one day for "
                    "some users and not others",
                ),
                (
                    "assistant",
                    "Off-by-one dates that vary per user is a timezone problem -- you're almost "
                    "certainly formatting a UTC instant using local-time getters, so anyone west of "
                    "UTC sees the previous day. Paste the grouping function and say what format the "
                    "timestamps arrive in.",
                ),
                (
                    "user",
                    "timestamps come from the api as ISO strings like \"2024-03-14T02:30:00Z\". "
                    "src/utils/groupByDay.js:\n\n"
                    "```js\n"
                    "export function groupByDay(items) {\n"
                    "  return items.reduce((acc, item) => {\n"
                    "    const d = new Date(item.createdAt);\n"
                    "    const key = `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;\n"
                    "    (acc[key] ||= []).push(item);\n"
                    "    return acc;\n"
                    "  }, {});\n"
                    "}\n"
                    "```",
                ),
                (
                    "assistant",
                    "Confirmed. `getFullYear/getMonth/getDate` return values in the *browser's* local "
                    "zone. For `2024-03-14T02:30:00Z` a user in UTC-5 gets March 13 -- hence off by "
                    "one for some users only. You also have a padding bug: `2024-3-4` instead of "
                    "`2024-03-04`, which sorts incorrectly as a string.\n\n"
                    "Fix in `src/utils/groupByDay.js` -- group by the UTC calendar day:\n\n"
                    "```js\n"
                    "export function groupByDay(items) {\n"
                    "  return items.reduce((acc, item) => {\n"
                    "    const key = new Date(item.createdAt).toISOString().slice(0, 10); // YYYY-MM-DD, UTC\n"
                    "    (acc[key] ||= []).push(item);\n"
                    "    return acc;\n"
                    "  }, {});\n"
                    "}\n"
                    "```\n\n"
                    "`toISOString().slice(0, 10)` is always UTC and always zero-padded, so it fixes "
                    "both bugs and the keys now sort lexicographically.\n\n"
                    "One decision to make deliberately: this makes the chart identical for every "
                    "user, which is right for reporting. If you instead want each user to see days in "
                    "*their own* zone, be explicit about it rather than relying on the ambient "
                    "default:\n\n"
                    "```js\n"
                    "const fmt = new Intl.DateTimeFormat('en-CA', {\n"
                    "  timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',\n"
                    "});\n"
                    "const key = fmt.format(new Date(item.createdAt)); // YYYY-MM-DD in that zone\n"
                    "```",
                ),
            ],
        ),
        _c(
            "seed-015",
            "python",
            [
                ("user", "help my code is broken"),
                (
                    "assistant",
                    "What's the code and what's the failure mode? Paste any error output.",
                ),
                (
                    "user",
                    "a worker that pulls jobs off a queue. it processes some jobs twice and customers "
                    "are getting duplicate emails",
                ),
                (
                    "assistant",
                    "Duplicate processing usually means the job is acknowledged after the side effect "
                    "but the ack can be lost, or the visibility timeout expires while you're still "
                    "working and another worker picks the job up. Which queue is this, how long does "
                    "a job take, and where do you delete/ack the message? Paste the loop.",
                ),
                (
                    "user",
                    "sqs, visibility timeout is 30s. sending the email plus rendering the pdf takes "
                    "like 45s sometimes. workers/mailer.py:\n\n"
                    "```python\n"
                    "while True:\n"
                    "    msgs = sqs.receive_message(QueueUrl=QUEUE, MaxNumberOfMessages=10)\n"
                    "    for m in msgs.get('Messages', []):\n"
                    "        job = json.loads(m['Body'])\n"
                    "        send_email(job)\n"
                    "        sqs.delete_message(QueueUrl=QUEUE, ReceiptHandle=m['ReceiptHandle'])\n"
                    "```",
                ),
                (
                    "assistant",
                    "There's the root cause, and it's not a race you can fix by reordering: a job "
                    "takes up to 45s but the visibility timeout is 30s, so SQS makes the message "
                    "visible again while you're still rendering the PDF, a second worker receives it, "
                    "and the customer gets two emails. Batching 10 messages makes it worse -- the "
                    "tenth message has been invisible for 30s before you even start it.\n\n"
                    "Fix in `workers/mailer.py` -- two parts, and you need both:\n\n"
                    "```python\n"
                    "VISIBILITY = 120   # comfortably above the p99 job duration\n\n"
                    "while True:\n"
                    "    msgs = sqs.receive_message(\n"
                    "        QueueUrl=QUEUE,\n"
                    "        MaxNumberOfMessages=1,          # don't hold a batch you can't finish in time\n"
                    "        WaitTimeSeconds=20,             # long poll instead of hot-spinning\n"
                    "        VisibilityTimeout=VISIBILITY,\n"
                    "    )\n"
                    "    for m in msgs.get('Messages', []):\n"
                    "        job = json.loads(m['Body'])\n"
                    "        if already_sent(job['job_id']):     # idempotency guard\n"
                    "            sqs.delete_message(QueueUrl=QUEUE, ReceiptHandle=m['ReceiptHandle'])\n"
                    "            continue\n"
                    "        send_email(job)\n"
                    "        mark_sent(job['job_id'])\n"
                    "        sqs.delete_message(QueueUrl=QUEUE, ReceiptHandle=m['ReceiptHandle'])\n"
                    "```\n\n"
                    "Raising the timeout and taking one message at a time removes the common case. "
                    "But SQS is at-least-once by design -- a worker can always die between "
                    "`send_email` and `delete_message` -- so the idempotency guard is what actually "
                    "makes duplicates impossible. Implement it as a conditional insert on a unique "
                    "`job_id` so the check and the claim are atomic:\n\n"
                    "```python\n"
                    "def mark_sent(job_id):\n"
                    "    # unique index on sent_jobs.job_id; raises on a second attempt\n"
                    "    db.execute('INSERT INTO sent_jobs (job_id) VALUES (%s)', (job_id,))\n"
                    "```\n\n"
                    "If a long job still risks exceeding the timeout, call "
                    "`sqs.change_message_visibility` periodically to extend the lease while you work.",
                ),
            ],
        ),
    ]


if __name__ == "__main__":
    convos = make_seed()
    print(f"{len(convos)} conversations")
    for c in convos:
        roles = "".join("U" if t["role"] == "user" else "A" for t in c["turns"])
        chars = sum(len(t["content"]) for t in c["turns"])
        print(f"  {c['conv_id']:10s} {c['language']:10s} turns={len(c['turns'])} {roles} chars={chars}")
