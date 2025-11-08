#!/usr/bin/env python3
"""
render_glyphs_from_db.py

Reads glyphs_{w}_{h}.db (produced by create_glyph_db.py) and renders PNGs.
Buckets by overall_entropy into folders entropy_0 .. entropy_10 (0.0..1.0 by 0.1 bins).
"""

import sqlite3
from pathlib import Path
from PIL import Image
import csv
from tqdm import tqdm

# ---------------------------
# CONFIG
# ---------------------------
DB_PATH = Path("dbs") / "glyphs_5_4.db"  # note: file name should match TEMPLATE dimensions or edit here
if not DB_PATH.exists():
	raise SystemExit(f"Database not found: {DB_PATH} -- create it first with create_glyph_db.py")
OUT_BASE = Path("glyphs") / "from_db_entropy_buckets"
SCALE = 20
MARGIN = 1  # in cells
WHERE_CLAUSE = ""  # optional SQL WHERE clause


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
		# infer dims by trying to find factors close to square: here assume square fallback
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


def main():
	conn = sqlite3.connect(str(DB_PATH))
	cur = conn.cursor()
	w, h = read_db_meta(conn)
	print(f"Rendering glyphs from DB {DB_PATH} (size {w}x{h})")

	# prepare buckets entropy_0 .. entropy_10 (11 bins)
	OUT_BASE.mkdir(parents=True, exist_ok=True)
	for k in range(11):
		(OUT_BASE / f"entropy_{k}" / "images").mkdir(parents=True, exist_ok=True)

	# CSV writers
	master_csv_path = OUT_BASE / "metadata.csv"
	master_f = open(master_csv_path, "w", newline="")
	mw = csv.writer(master_f)
	mw.writerow(["id", "int_repr", "bitstring", "filled", "components", "overall_entropy", "bucket", "filename"])

	bucket_files = {}
	bucket_writers = {}
	for k in range(11):
		bf = open(OUT_BASE / f"entropy_{k}" / "metadata.csv", "w", newline="")
		bw = csv.writer(bf)
		bw.writerow(["id", "int_repr", "bitstring", "filled", "components", "filename"])
		bucket_files[k] = bf
		bucket_writers[k] = bw

	# SELECT includes overall_entropy
	where = WHERE_CLAUSE.strip()
	if where:
		query = f"SELECT id, bitstring, int_repr, filled, components, overall_entropy FROM glyphs WHERE {where} ORDER BY id"
	else:
		query = "SELECT id, bitstring, int_repr, filled, components, overall_entropy FROM glyphs ORDER BY id"

	cur.execute(query)
	rows = cur.fetchall()
	print(f"Selected {len(rows)} glyphs to render.")

	for row in tqdm(rows, desc="Rendering", unit="glyph"):
		gid, bitstring, intrepr, filled, components, overall = row
		if overall is None:
			# defensive: if DB lacks entropy (old DB), compute on the fly
			grid = grid_from_bitstring(bitstring, w, h)

			# compute entropy as rows+cols average (same function used in generator)
			# inline compute to avoid reuse of generator file
			def bin_ent(p):
				if p <= 0.0 or p >= 1.0:
					return 0.0
				return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))

			row_ent = sum(bin_ent(sum(grid[r][c] for c in range(w)) / w) for r in range(h)) / h
			col_ent = sum(bin_ent(sum(grid[r][c] for r in range(h)) / h) for c in range(w)) / w
			overall = (row_ent + col_ent) / 2.0

		# determine bucket 0..10
		bucket_idx = int(overall * 10)
		if bucket_idx < 0:
			bucket_idx = 0
		if bucket_idx > 10:
			bucket_idx = 10

		fname = f"{bitstring}.png"
		img_path = OUT_BASE / f"entropy_{bucket_idx}" / "images" / fname
		grid = grid_from_bitstring(bitstring, w, h)
		render_grid_to_png(grid, SCALE, MARGIN, img_path)

		rel_path = f"entropy_{bucket_idx}/images/{fname}"
		mw.writerow([gid, intrepr, bitstring, filled, components, overall, bucket_idx, rel_path])
		bucket_writers[bucket_idx].writerow([gid, intrepr, bitstring, filled, components, f"images/{fname}"])

	# close
	master_f.close()
	for f in bucket_files.values():
		f.close()
	conn.close()
	print("Rendering done. Master CSV:", master_csv_path)
	print("Buckets in:", OUT_BASE)


if __name__ == "__main__":
	import math

	main()
