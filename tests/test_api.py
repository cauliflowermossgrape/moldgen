"""End-to-end API tests: upload → analyze → generate → download."""

import pytest
import trimesh
from fastapi.testclient import TestClient

import moldgen.main as main


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "WORKDIR", tmp_path)
    return TestClient(main.app)


def upload_mushroom(client):
    stem = trimesh.creation.cylinder(radius=5.0, height=20.0, sections=64)
    stem.apply_translation([0, 0, 10.0])
    cap = trimesh.creation.cylinder(radius=15.0, height=5.0, sections=64)
    cap.apply_translation([0, 0, 22.5])
    part = trimesh.boolean.union([stem, cap], engine="manifold")
    data = trimesh.exchange.stl.export_stl(part)
    resp = client.post(
        "/api/upload", files={"file": ("mushroom.stl", data, "model/stl")}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_full_flow(client):
    up = upload_mushroom(client)
    assert up["watertight"] is True
    assert up["triangles"] > 100
    assert up["extents"][0] == pytest.approx(30.0, abs=0.1)
    # optimal parting height is inside the cap band (widest section)
    assert 20.0 < up["suggested_parting_z_mm"] < 25.0

    # preview mesh downloads as binary STL matching the reported face count
    mesh_resp = client.get(up["mesh_url"])
    assert mesh_resp.status_code == 200
    assert len(mesh_resp.content) == 84 + 50 * up["triangles"]

    # analyze at a bad height: cap underside blocks the top half
    an = client.post(
        "/api/analyze", json={"mesh_id": up["mesh_id"], "parting_z_mm": 10.0}
    ).json()
    assert an["undercut_faces"]
    assert max(an["undercut_faces"]) < up["triangles"]
    assert not an["top"]["likely_demoldable"]
    assert an["bottom"]["likely_demoldable"]
    # the stem/cap walls are vertical: reported as low-draft drag area
    assert an["low_draft_faces"]
    assert an["bottom"]["low_draft_area_mm2"] > 0

    # analyze at the suggested height: clean
    an2 = client.post(
        "/api/analyze",
        json={"mesh_id": up["mesh_id"], "parting_z_mm": up["suggested_parting_z_mm"]},
    ).json()
    assert an2["undercut_area_mm2"] == 0.0

    # generate a compression mold with pins and bolts
    gen = client.post(
        "/api/generate",
        json={
            "mesh_id": up["mesh_id"],
            "parting_z_mm": up["suggested_parting_z_mm"],
            "mode": "compression",
            "bolts": True,
        },
    ).json()
    assert gen["ok"] is True, gen.get("errors")
    assert set(gen["halves"]) == {"female", "male"}
    assert "pins" in gen["metadata"]["hardware"]
    assert "bolts" in gen["metadata"]["hardware"]
    assert "pry_slots" in gen["metadata"]["hardware"]
    assert "overflow" in gen["metadata"]["hardware"]
    assert "jack_screws" in gen["metadata"]["hardware"]
    assert gen["metadata"]["forged"]["plunge_mm"] == 10.0
    for name, info in gen["halves"].items():
        assert info["watertight"] is True
        dl = client.get(info["url"])
        assert dl.status_code == 200
        assert len(dl.content) == 84 + 50 * info["triangles"]
        assert "attachment" in dl.headers["content-disposition"]


def test_generate_open_layup(client):
    up = upload_mushroom(client)
    gen = client.post(
        "/api/generate",
        json={
            "mesh_id": up["mesh_id"],
            "parting_z_mm": up["suggested_parting_z_mm"],
            "mode": "open_layup",
            "keep_side": "below",
            "trim_groove": True,
        },
    ).json()
    assert gen["ok"] is True, gen.get("errors")
    assert set(gen["halves"]) == {"tool"}
    assert "trim_groove" in gen["metadata"]["hardware"]


def test_orient_lay_on_face_and_reset(client):
    up = upload_mushroom(client)  # extents 30 x 30 x 25, stem up

    # lay the part on its +X side: height becomes the old x extent
    resp = client.post(
        "/api/orient", json={"mesh_id": up["mesh_id"], "down": [1, 0, 0]}
    )
    assert resp.status_code == 200
    o = resp.json()
    assert o["triangles"] == up["triangles"]  # rotation preserves face order
    assert o["extents"][2] == pytest.approx(30.0, abs=0.1)
    # rested on the plate: bottom at z=0, centered in xy
    assert o["bounds"][0][2] == pytest.approx(0.0, abs=1e-6)
    assert (o["bounds"][0][0] + o["bounds"][1][0]) / 2 == pytest.approx(0.0, abs=1e-6)

    # generation works in the new orientation
    gen = client.post(
        "/api/generate",
        json={"mesh_id": up["mesh_id"], "parting_z_mm": o["suggested_parting_z_mm"]},
    ).json()
    assert gen["ok"] is True, gen.get("errors")

    # reset restores the as-uploaded orientation
    r = client.post("/api/orient", json={"mesh_id": up["mesh_id"], "reset": True}).json()
    assert r["extents"] == pytest.approx(up["extents"], abs=1e-4)
    for restored, uploaded in zip(r["bounds"], up["bounds"]):
        assert restored == pytest.approx(uploaded, abs=1e-4)

    # bad vectors are rejected
    bad = client.post("/api/orient", json={"mesh_id": up["mesh_id"], "down": [0, 0, 0]})
    assert bad.status_code == 400
    bad = client.post("/api/orient", json={"mesh_id": up["mesh_id"]})
    assert bad.status_code == 400


def test_generate_prepreg(client):
    up = upload_mushroom(client)
    gen = client.post(
        "/api/generate",
        json={
            "mesh_id": up["mesh_id"],
            "parting_z_mm": up["suggested_parting_z_mm"],
            "mode": "prepreg",
            "flange_bolt_holes": True,
        },
    ).json()
    assert gen["ok"] is True, gen.get("errors")
    assert set(gen["halves"]) == {"tool"}
    assert gen["metadata"]["prepreg"]["trim_allowance_mm"] == 8.0
    assert gen["metadata"]["prepreg"]["notes"]
    assert "tape_line" in gen["metadata"]["hardware"]
    assert "flange_holes" in gen["metadata"]["hardware"]


def test_generate_engine_error_is_structured(client):
    up = upload_mushroom(client)
    gen = client.post(
        "/api/generate",
        json={"mesh_id": up["mesh_id"], "parting_z_mm": 9999.0},
    ).json()
    assert gen["ok"] is False
    assert gen["errors"]


def test_bad_requests(client):
    resp = client.post("/api/upload", files={"file": ("part.step", b"xx", "text/plain")})
    assert resp.status_code == 400

    resp = client.post("/api/analyze", json={"mesh_id": "nope", "parting_z_mm": 0})
    assert resp.status_code == 400  # malformed id

    resp = client.post(
        "/api/analyze", json={"mesh_id": "0123456789ab", "parting_z_mm": 0}
    )
    assert resp.status_code == 404  # well-formed but unknown

    up = upload_mushroom(client)
    resp = client.get(f"/api/download/{up['mesh_id']}/evil.stl")
    assert resp.status_code == 404

    resp = client.get(f"/api/download/{up['mesh_id']}/female.stl")
    assert resp.status_code == 404  # not generated yet


def test_non_finite_json_is_a_readable_422(client):
    up = upload_mushroom(client)
    body = '{"mesh_id": "%s", "parting_z_mm": 22, "plunge_mm": NaN}' % up["mesh_id"]
    resp = client.post(
        "/api/generate", content=body, headers={"content-type": "application/json"}
    )
    assert resp.status_code == 422
    assert "plunge_mm" in resp.json()["detail"]
    assert "finite" in resp.json()["detail"]


def test_generate_accepts_every_engine_param(client):
    # the request model is derived from MoldParams, so e.g. pin sizing works
    up = upload_mushroom(client)
    gen = client.post(
        "/api/generate",
        json={
            "mesh_id": up["mesh_id"],
            "parting_z_mm": up["suggested_parting_z_mm"],
            "pin_radius_mm": 3.0,
            "shrink_comp_pct": 0.5,
        },
    ).json()
    assert gen["ok"] is True, gen.get("errors")
    assert gen["metadata"]["hardware"]["pins"]["radius"] == 3.0


def test_bundle_zip_and_no_store(client):
    import io
    import zipfile

    up = upload_mushroom(client)
    assert client.get("/api/download/%s/mold.zip" % up["mesh_id"]).status_code == 404
    gen = client.post(
        "/api/generate",
        json={"mesh_id": up["mesh_id"], "parting_z_mm": up["suggested_parting_z_mm"]},
    ).json()
    resp = client.get(gen["bundle_url"])
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = set(zf.namelist())
    assert {"moldgen-female.stl", "moldgen-male.stl", "moldgen-setup.txt"} <= names
    sheet = zf.read("moldgen-setup.txt").decode()
    assert "Charge" in sheet or "forged part" in sheet
    # halves are rewritten under the same path: must never be cached
    half = client.get(gen["halves"]["male"]["url"])
    assert half.headers["cache-control"] == "no-store"


def test_mode_switch_and_orient_clear_stale_halves(client):
    up = upload_mushroom(client)
    mid = up["mesh_id"]
    z = up["suggested_parting_z_mm"]
    assert client.post("/api/generate", json={"mesh_id": mid, "parting_z_mm": z}).json()["ok"]
    assert client.get(f"/api/download/{mid}/female.stl").status_code == 200
    gen = client.post(
        "/api/generate", json={"mesh_id": mid, "parting_z_mm": z, "mode": "open_layup"}
    ).json()
    assert gen["ok"]
    # compression halves from the previous run are gone
    assert client.get(f"/api/download/{mid}/female.stl").status_code == 404
    client.post("/api/orient", json={"mesh_id": mid, "down": [1, 0, 0]})
    assert client.get(f"/api/download/{mid}/tool.stl").status_code == 404


def test_upload_reports_mode_suggestions_and_profile(client):
    up = upload_mushroom(client)
    s = up["suggested"]
    assert set(s) == {"two_part", "below", "above"}
    # a tool below the plane can cover the whole mushroom (cap top up)
    assert s["below"] > 24.0
    prof = up["parting_profile"]
    assert len(prof["z"]) == len(prof["two_part"]) == len(prof["below"]) == len(prof["above"])
    # through the stem the cap locks the two-part mold
    i = min(range(len(prof["z"])), key=lambda k: abs(prof["z"][k] - 10.0))
    assert prof["two_part"][i] > 0


def test_analyze_single_sided_and_min_draft(client):
    up = upload_mushroom(client)
    an = client.post(
        "/api/analyze",
        json={
            "mesh_id": up["mesh_id"],
            "parting_z_mm": up["suggested"]["below"],
            "mode": "prepreg",
            "keep_side": "below",
            "min_draft_deg": 3.0,
        },
    ).json()
    assert an["single_sided"] is True
    assert an["min_draft_deg"] == 3.0
    assert an["bottom"]["likely_demoldable"]
    bad = client.post(
        "/api/analyze",
        json={"mesh_id": up["mesh_id"], "parting_z_mm": 20, "min_draft_deg": 0},
    )
    assert bad.status_code == 400


def test_upload_size_limit(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1000)
    data = trimesh.exchange.stl.export_stl(trimesh.creation.icosphere())
    resp = client.post("/api/upload", files={"file": ("big.stl", data, "model/stl")})
    assert resp.status_code == 413
