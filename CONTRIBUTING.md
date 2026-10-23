# Contributing

1. Never commit secrets (see [`docs/SECURITY.md`](docs/SECURITY.md)).
2. Keep examples generic (`example.com`, `YOUR_BRAND`, `YOUR_CUSTOMER_ID`).
3. Prefer `READ_ONLY` demos in docs and CI.
4. Add or update tests under `tests/` for behavior changes.
5. Run `PYTHONPATH=src pytest -q` before opening a PR.

## Local first-time setup

```bash
python scripts/init_project.py --non-interactive
# edit .env + config/product_facts.json
```
