# Naminal Hub

Local-first animal documentary production. The GitHub repository is
`AnimalVideoPipeline`; Naminal Hub is the public-facing name for exports.
Research evidence, asset rights, generated copy, and operator approval are
separate. **Nothing in this project publishes or sends messages.**

```bash
uv sync --extra dev
uv run python main.py doctor
uv run python main.py --help
uv run pytest
```

See [architecture](docs/ARCHITECTURE.md) and [operations](docs/OPERATIONS.md).
The fixture simulation uses synthetic test patterns, not wildlife footage; it
is not eligible for publication.
