# tools/ — manual integration probes, regressions & seeders

These are **not** unit tests (CI's `pytest` only collects `tests/test_*.py`). They
run against a **live** backend (`http://localhost:7981`) plus a real Postgres/Redis,
and they import each other (`import tournament_regression as tr`) and `src.*`.

Run them through the isolated runner so they never touch the uvicorn process and
so `src` + sibling imports resolve:

```bash
# from the backend project root, with the API running:
./scripts/run_task.sh tools/tournament_regression.py
./scripts/run_task.sh tools/edge_case_regression.py
./scripts/run_task.sh tools/seed_demo_tournament.py
```

| Group | Files | Purpose |
|---|---|---|
| Regressions | `tournament_regression.py`, `edge_case_regression.py`, `live_match_regression.py` | end-to-end flow assertions |
| Probes | `probe_*.py` | targeted scenario checks (favorites, retired-hurt, NRR, concurrency, …) |
| Seeders | `seed_demo_tournament.py`, `seed_for_account.py` | populate demo data for manual QA |

> `tournament_regression.py` is the shared base — several probes/seeders import it,
> so keep these files together in this folder.
