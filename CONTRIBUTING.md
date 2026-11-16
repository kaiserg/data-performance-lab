# Contributing

Open an issue describing the workload or bug, then propose a focused pull request. Use Python 3.13+, uv and the committed lockfiles. Install with `cd backend` and `uv sync --extra rust`, run `uv run pytest -q`, and run `pnpm build` from `frontend`.

Keep legacy worker positional arguments and HTTP response fields compatible. New operations need equivalent semantics in every engine and independent correctness tests covering nulls and boundaries. Document timing changes; never combine different timing modes into one ranking. Include environment metadata and raw samples for performance claims.

Never commit `data/`, `results/`, secrets, virtual environments, build output or node_modules. Before publishing, inspect the staged file list and confirm no large Parquet files are tracked. Contributions are under the MIT license; source data has its own terms.
