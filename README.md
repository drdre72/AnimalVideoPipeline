# Naminal Hub

Local-first animal documentary production. The GitHub repository is
`AnimalVideoPipeline`; Naminal Hub is the public-facing name for exports.
Research evidence, asset rights, generated copy, and operator approval are
separate. **Nothing in this project publishes or sends messages.**

```bash
uv sync --no-editable --extra dev
uv run --no-editable python main.py doctor
uv run --no-editable python main.py --help
uv run --no-editable --extra dev pytest
```

On some macOS setups, editable-install `.pth` files acquire the hidden
filesystem flag and Python ignores them; the non-editable commands avoid it.
Run from the repository root or set `NAMINAL_ROOT` to its absolute path.

See [architecture](docs/ARCHITECTURE.md) and [operations](docs/OPERATIONS.md).
The fixture simulation uses synthetic test patterns, not wildlife footage; it
is not eligible for publication.
