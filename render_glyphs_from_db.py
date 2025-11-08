#!/usr/bin/env python3
"""
render_glyphs_from_db.py

Reads DB (created by create_glyph_db.py) and renders PNGs into folders:
 - score_1 .. score_6 (unchanged behavior; integer score comes from DB)
 - score_0 contains *subfolders* score_0_0 .. score_0_9 where the float symmetry
   measure for formerly-score-0 glyphs is computed and used to place them into
   the 0.x buckets: score_0_0 => [0.0,0.1), score_0_1 => [0.1,0.2), ..., score_0_9 => [0.9,1.0]

Only compute the float when the DB's score == 0. The DB is not modified.
Progress bar via tqdm shows rendering progress.
"""

import sqlite3
from pathlib import Path
from PIL import Image
import csv
from tqdm import tqdm

# ---------------------------
# CONFIG
# ---------------------------
DB_PATH = Path("dbs") / "glyphs_5_5.db"
if not DB_PATH.exists():
	raise SystemExit(f"Database not found: {DB_PATH} -- create it first with create_glyph_db.py")
OUT_BASE = Path("glyphs") / "5x5_from_db_split0float"
SCALE = 20
MARGIN = 1  # in cells
WHERE_CLAUSE = ""  # optional DB filter


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


# transforms (square)
def rotate90(grid):
	h = len(grid)
	return [[grid[h - 1 - c][r] for c in range(h)] for r in range(h)]


def rotate180(grid):
	return [list(reversed(row)) for row in reversed(grid)]


def reflect_vertical(grid):
	return [list(reversed(row)) for row in grid]


def reflect_horizontal(grid):
	return list(reversed([list(r) for r in grid]))


def reflect_main_diag(grid):
	h = len(grid)
	return [[grid[c][r] for c in range(h)] for r in range(h)]


def reflect_anti_diag(grid):
	h = len(grid)
	return [[grid[h - 1 - c][h - 1 - r] for c in range(h)] for r in range(h)]


# compute fractional match for ONE transform:
# fraction = (# positions where original == 1 AND transformed == 1) / (# ones in original)
# if original has zero ones -> return 1.0 (trivial symmetry); this won't usually happen for DB score==0
def frac_match_for_transform(grid, transform_fn):
	h = len(grid)
	w = len(grid[0])
	orig_bits = [1 if grid[r][c] else 0 for r in range(h) for c in range(w)]
	trans = transform_fn(grid)
	trans_bits = [1 if trans[r][c] else 0 for r in range(h) for c in range(w)]
	total_ones = sum(orig_bits)
	if total_ones == 0:
		return 1.0
	matches = sum(1 for ob, tb in zip(orig_bits, trans_bits) if ob == 1 and tb == 1)
	return matches / total_ones


def compute_0float_score(grid):
	# compute fraction for each of the 6 transforms and average
	fh = frac_match_for_transform(grid, reflect_horizontal)
	fv = frac_match_for_transform(grid, reflect_vertical)
	fd1 = frac_match_for_transform(grid, reflect_main_diag)
	fd2 = frac_match_for_transform(grid, reflect_anti_diag)
	fr90 = frac_match_for_transform(grid, rotate90)
	fr180 = frac_match_for_transform(grid, rotate180)
	overall = (fh + fv + fd1 + fd2 + fr90 + fr180) / 6.0
	return overall


def main():
	conn = sqlite3.connect(str(DB_PATH))
	cur = conn.cursor()
	w, h = read_db_meta(conn)
	print(f"Rendering glyphs from DB {DB_PATH} (size {w}x{h})")

	# prepare folders: top-level score_1 .. score_6 and score_0 with sub-buckets
	OUT_BASE.mkdir(parents=True, exist_ok=True)
	for k in range(1, 7):
		(OUT_BASE / f"score_{k}" / "images").mkdir(parents=True, exist_ok=True)

	# create score_0 and its subbuckets score_0_0 .. score_0_9
	(OUT_BASE / "score_0").mkdir(parents=True, exist_ok=True)
	for i in range(10):
		(OUT_BASE / f"score_0/score_0_{i}" / "images").mkdir(parents=True, exist_ok=True)

	# CSV writers
	master_csv_path = OUT_BASE / "metadata.csv"
	master_f = open(master_csv_path, "w", newline="")
	mw = csv.writer(master_f)
	mw.writerow(["id", "int_repr", "bitstring", "filled", "components", "score", "subbucket_or_bucket", "filename"])

	bucket_files = {}
	bucket_writers = {}
	for k in range(1, 7):
		bf = open(OUT_BASE / f"score_{k}" / "metadata.csv", "w", newline="")
		bw = csv.writer(bf)
		bw.writerow(["id", "int_repr", "bitstring", "filled", "components", "filename"])
		bucket_files[k] = bf
		bucket_writers[k] = bw

	# metadata csvs for score_0 subbuckets
	subbucket_files = {}
	subbucket_writers = {}
	for i in range(10):
		path = OUT_BASE / f"score_0/score_0_{i}" / "metadata.csv"
		f = open(path, "w", newline="")
		wr = csv.writer(f)
		wr.writerow(["id", "int_repr", "bitstring", "filled", "components", "float_score_bucket", "filename"])
		subbucket_files[i] = f
		subbucket_writers[i] = wr

	# query
	where = WHERE_CLAUSE.strip()
	if where:
		query = f"SELECT id, bitstring, int_repr, filled, components, horizontal, vertical, diag1, diag2, rot90, rot180, score FROM glyphs WHERE {where} ORDER BY id"
	else:
		query = "SELECT id, bitstring, int_repr, filled, components, horizontal, vertical, diag1, diag2, rot90, rot180, score FROM glyphs ORDER BY id"

	cur.execute(query)
	rows = cur.fetchall()
	print(f"Selected {len(rows)} glyphs to render.")

	# render with progress bar
	for row in tqdm(rows, desc="Rendering", unit="glyph"):
		(gid, bitstring, intrepr, filled, components,
		 h_sym, v_sym, d1, d2, r90, r180, score) = row

		fname = f"{bitstring}.png"

		# if score > 0: keep old behavior (score_{score})
		if score and score > 0:
			img_path = OUT_BASE / f"score_{score}" / "images" / fname
			grid = grid_from_bitstring(bitstring, w, h)
			render_grid_to_png(grid, SCALE, MARGIN, img_path)
			rel_path = f"score_{score}/images/{fname}"
			mw.writerow([gid, intrepr, bitstring, filled, components, score, f"score_{score}", rel_path])
			bucket_writers[score].writerow([gid, intrepr, bitstring, filled, components, f"images/{fname}"])
			continue

		# score == 0: compute float score (average of 6 fractional matches)
		grid = grid_from_bitstring(bitstring, w, h)
		overall = compute_0float_score(grid)
		# bucket 0..9 for ranges [0.0,0.1), [0.1,0.2), ... [0.9,1.0]
		bucket_idx = int(overall * 10)
		if bucket_idx < 0:
			bucket_idx = 0
		if bucket_idx > 9:
			bucket_idx = 9

		img_path = OUT_BASE / f"score_0/score_0_{bucket_idx}" / "images" / fname
		render_grid_to_png(grid, SCALE, MARGIN, img_path)
		rel_path = f"score_0/score_0_{bucket_idx}/images/{fname}"
		mw.writerow([gid, intrepr, bitstring, filled, components, score, f"score_0_{bucket_idx}", rel_path])
		subbucket_writers[bucket_idx].writerow(
			[gid, intrepr, bitstring, filled, components, bucket_idx, f"images/{fname}"])

	# close
	master_f.close()
	for f in bucket_files.values():
		f.close()
	for f in subbucket_files.values():
		f.close()
	conn.close()
	print("Rendering done. Master CSV:", master_csv_path)
	print("Buckets in:", OUT_BASE)


if __name__ == "__main__":
	main()
