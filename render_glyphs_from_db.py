#!/usr/bin/env python3
"""
render_glyphs_from_db.py

Reads glyphs_{w}_{h}.db (produced by create_glyph_db.py) and renders PNGs.
Buckets by effective score (predicted_bt if present else assigned_bt) into
aesthetic_0 .. aesthetic_{N_BUCKETS-1} (uniform partition of observed range).
"""

import sqlite3
from pathlib import Path
from PIL import Image
import csv
from tqdm import tqdm
import math

# ---------------------------
# CONFIG
# ---------------------------
DB_PATH = Path("dbs") / "glyphs_5_5.db"  # edit if DB is named differently
if not DB_PATH.exists():
	raise SystemExit(f"Database not found: {DB_PATH} -- create it first with create_glyph_db.py")
OUT_BASE = Path("glyphs") / "from_db_aesthetic_buckets"
SCALE = 20
MARGIN = 1  # in cells
WHERE_CLAUSE = ""  # optional SQL WHERE clause
N_BUCKETS = 10


# ---------------------------


def read_db_meta(conn):
	cur = conn.cursor()
	try:
		cur.execute("SELECT v FROM meta WHERE k='w'")
		w = int(cur.fetchone()[0])
		cur.execute("SELECT v FROM meta WHERE k='h'")
		h = int(cur.fetchone()[0])
	except Exception:
		cur.execute("SELECT bitstring FROM glyphs LIMIT 1")
		row = cur.fetchone()
		if not row:
			raise RuntimeError("DB empty or corrupt and no meta table")
		bitlen = len(row[0])
		side = int(bitlen ** 0.5)
		w = h = side
	return w, h


def grid_from_bitstring(bs, w, h):
	bits = [1 if ch == "1" else 0 for ch in bs]
	return [[bits[r * w + c] for c in range(w)] for r in range(h)]


def render_grid_to_png(grid, scale, margin, out_path):
	h = len(grid)
	w = len(grid[0])
	size_px = (w + 2 * margin) * scale, (h + 2 * margin) * scale
	img = Image.new("RGB", size_px, (255, 255, 255))
	px = img.load()
	for r in range(h):
		for c in range(w):
			if grid[r][c]:
				x0 = (c + margin) * scale
				y0 = (r + margin) * scale
				for dx in range(scale):
					for dy in range(scale):
						px[x0 + dx, y0 + dy] = (0, 0, 0)
	out_path.parent.mkdir(parents=True, exist_ok=True)
	img.save(str(out_path))


def compute_fallback_entropy(grid):
	# old fallback: row+col binary entropy average (kept for compatibility)
	h = len(grid);
	w = len(grid[0])

	def bin_ent(p):
		if p <= 0.0 or p >= 1.0:
			return 0.0
		return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))

	row_ent = sum(bin_ent(sum(grid[r][c] for c in range(w)) / w) for r in range(h)) / h
	col_ent = sum(bin_ent(sum(grid[r][c] for r in range(h)) / h) for c in range(w)) / w
	return (row_ent + col_ent) / 2.0


def main():
	conn = sqlite3.connect(str(DB_PATH))
	cur = conn.cursor()
	w, h = read_db_meta(conn)
	print(f"Rendering glyphs from DB {DB_PATH} (size {w}x{h})")

	# Build WHERE-aware query and fetch
	where = WHERE_CLAUSE.strip()
	if where:
		query = f"SELECT id, bitstring, int_repr, filled, components, assigned_bt, predicted_bt FROM glyphs WHERE {where} ORDER BY id"
	else:
		query = "SELECT id, bitstring, int_repr, filled, components, assigned_bt, predicted_bt FROM glyphs ORDER BY id"
	cur.execute(query)
	rows = cur.fetchall()
	print(f"Selected {len(rows)} glyphs to render.")

	# compute effective scores and min/max
	effective_scores = []
	row_infos = []
	for r in rows:
		gid, bitstring, intrepr, filled, components, assigned_bt, predicted_bt = r
		if predicted_bt is not None:
			eff = float(predicted_bt)
		elif assigned_bt is not None:
			eff = float(assigned_bt)
		else:
			eff = 0.5
		effective_scores.append(eff)
		row_infos.append((gid, bitstring, intrepr, filled, components, eff))

	if not effective_scores:
		raise RuntimeError("No glyphs found")

	min_score = min(effective_scores)
	max_score = max(effective_scores)

	# Decide bucket strategy
	if math.isclose(min_score, max_score, rel_tol=1e-12, abs_tol=1e-12):
		bins = [(min_score, max_score)]
		print(f"All scores equal ({min_score:.6g}) — single bucket 'aesthetic_0'.")
	else:
		width = (max_score - min_score) / N_BUCKETS
		bins = []
		for i in range(N_BUCKETS):
			a = min_score + i * width
			b = min_score + (i + 1) * width
			bins.append((a, b))
		print(f"aesthetic_score range: min={min_score:.6g}, max={max_score:.6g}, buckets={len(bins)}")

	# prepare output directories and CSV writers
	OUT_BASE.mkdir(parents=True, exist_ok=True)
	bucket_count = len(bins)
	bucket_files = {}
	bucket_writers = {}
	for i in range(bucket_count):
		dirpath = OUT_BASE / f"aesthetic_{i}" / "images"
		dirpath.mkdir(parents=True, exist_ok=True)
		f = open(OUT_BASE / f"aesthetic_{i}" / "metadata.csv", "w", newline="")
		wr = csv.writer(f)
		wr.writerow(["id", "int_repr", "bitstring", "filled", "components", "filename"])
		bucket_files[i] = f
		bucket_writers[i] = wr

	# master CSV
	master_csv_path = OUT_BASE / "metadata.csv"
	master_f = open(master_csv_path, "w", newline="")
	mw = csv.writer(master_f)
	mw.writerow(["id", "int_repr", "bitstring", "filled", "components", "effective_score", "bucket", "filename"])

	# iterate & render
	for (gid, bitstring, intrepr, filled, components, eff) in tqdm(row_infos, desc="Rendering", unit="glyph"):
		# find bucket index
		if len(bins) == 1:
			bucket_idx = 0
		else:
			bucket_idx = int((eff - min_score) / ((max_score - min_score) / len(bins)))
			if bucket_idx < 0:
				bucket_idx = 0
			if bucket_idx >= len(bins):
				bucket_idx = len(bins) - 1

		fname = f"{bitstring}.png"
		img_path = OUT_BASE / f"aesthetic_{bucket_idx}" / "images" / fname
		grid = grid_from_bitstring(bitstring, w, h)
		render_grid_to_png(grid, SCALE, MARGIN, img_path)

		rel_path = f"aesthetic_{bucket_idx}/images/{fname}"
		mw.writerow([gid, intrepr, bitstring, filled, components, eff, bucket_idx, rel_path])
		bucket_writers[bucket_idx].writerow([gid, intrepr, bitstring, filled, components, f"images/{fname}"])

	# close files
	master_f.close()
	for f in bucket_files.values():
		f.close()
	conn.close()
	print("Rendering done. Master CSV:", master_csv_path)
	print("Buckets in:", OUT_BASE)


if __name__ == "__main__":
	main()
