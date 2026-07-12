# Verifying moldgen

## Launch

The dev venv lives at `~/.venvs/moldgen` (WSL home, not in-repo — the repo is
on a slow /mnt/c Windows mount). `make` is not installed in this environment.

```sh
~/.venvs/moldgen/bin/python -m pip install -e ".[dev]"   # once / after dep changes
~/.venvs/moldgen/bin/python -m uvicorn moldgen.main:app --port 8765 &
curl -s http://127.0.0.1:8765/api/health                # {"status":"ok",...}
```

Use a non-default port (8765) — the user may have `make dev` running on 8000.

## Drive the API flow

1. Make a test part with undercuts (mushroom = stem + wider cap) using
   `trimesh.creation.cylinder` + `trimesh.boolean.union(engine="manifold")`,
   export STL to the scratchpad.
2. `curl -F "file=@mushroom.stl" /api/upload` → mesh_id, stats,
   `suggested_parting_z_mm` (should land in the cap band).
3. `POST /api/analyze {mesh_id, parting_z_mm}` — at a height through the stem
   the cap underside must flag (top half not demoldable); at the suggested
   height, zero undercuts.
4. `POST /api/generate` with params → halves with URLs; reload the downloaded
   STLs in trimesh and assert `is_watertight` and sane z-ranges (bottom half
   extends `pin_height` above the parting plane when pins are on).
5. Engine failures (bad parting height, non-watertight input) return
   HTTP 200 with `{"ok": false, "errors": [...]}` — not 4xx/5xx.

## Frontend

Static files served by the same server at `/`. No headless browser in this
environment — `node --input-type=module --check < frontend/main.js` catches
syntax errors; visual checks need the user to open http://localhost:8000 and
paste a screenshot. three.js loads from jsdelivr CDN (needs internet).

## Gotchas

- Uploads write real dirs into `./workdir/` — harmless (gitignored), but
  clean up test uploads if you make many.
- `pkill -f uvicorn` can kill your own shell's process group (exit 144) —
  start the server with `run_in_background` instead and let it die with the
  session, or target the PID.
