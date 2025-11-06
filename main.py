#!/usr/bin/env python3
"""
gen_glyphs_symmetry_buckets.py

Generates 5x5 glyphs under the specified rules and writes them into symmetry buckets.

Outputs (beside this script):
glyphs/
  5x5_filled_corners/
    metadata.csv                 <- master metadata containing bucket path in filename
    score_0/
      images/
      metadata.csv
    score_1/
      images/
      metadata.csv
    ...
    score_6/
      images/
      metadata.csv

Image filenames are the full 25-bit bitstring (row-major) + ".png"
"""

from pathlib import Path
from collections import deque
from PIL import Image
import csv, os, sys, shutil
from tqdm import tqdm

# -------------------------
# Config (adjust if needed)
# -------------------------
OUT_BASE = Path(__file__).resolve().parent / "glyphs" / "5x5_filled_corners"
SCALE = 20  # how big to render each grid cell in the PNGs (for comfortable thumbnailing)
MARGIN = 1  # in cells (keeps a 1-cell white border around the 5x5 inside each PNG)
# -------------------------

# prepare directories
OUT_BASE.mkdir(parents=True, exist_ok=True)
for k in range(7):
	(OUT_BASE / f"score_{k}" / "images").mkdir(parents=True, exist_ok=True)

# constants: corners forced
corner_indices = {0, 4, 20, 24}
all_indices = list(range(25))
variable_indices = [i for i in all_indices if i not in corner_indices]  # 21 bits


def int_to_grid(n: int):
	flat = [0] * 25
	for i in corner_indices:
		flat[i] = 1
	for bitpos, idx in enumerate(variable_indices):
		if (n >> bitpos) & 1:
			flat[idx] = 1
	return [flat[r * 5:(r + 1) * 5] for r in range(5)]


def grid_to_bitstring(grid):
	return "".join(str(bit) for row in grid for bit in row)


def has_filled_2x2(grid):
	for r in range(4):
		for c in range(4):
			if grid[r][c] and grid[r][c + 1] and grid[r + 1][c] and grid[r + 1][c + 1]:
				return True
	return False


def has_blank_2x2(grid):
	for r in range(4):
		for c in range(4):
			if (not grid[r][c]) and (not grid[r][c + 1]) and (not grid[r + 1][c]) and (not grid[r + 1][c + 1]):
				return True
	return False


def has_pure_diagonal_2x2(grid):
	for r in range(4):
		for c in range(4):
			a = grid[r][c]
			b = grid[r][c + 1]
			cc = grid[r + 1][c]
			d = grid[r + 1][c + 1]
			if a and d and (not b) and (not cc):
				return True
			if b and cc and (not a) and (not d):
				return True
	return False


def count_components(grid):
	visited = [[False] * 5 for _ in range(5)]
	dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
	comp = 0
	for r in range(5):
		for c in range(5):
			if grid[r][c] and not visited[r][c]:
				comp += 1
				dq = deque()
				dq.append((r, c))
				visited[r][c] = True
				while dq:
					x, y = dq.popleft()
					for dx, dy in dirs:
						nx, ny = x + dx, y + dy
						if 0 <= nx < 5 and 0 <= ny < 5 and not visited[nx][ny] and grid[nx][ny]:
							visited[nx][ny] = True
							dq.append((nx, ny))
	return comp


# symmetry helpers
def rotate90(grid):
	return [[grid[4 - c][r] for c in range(5)] for r in range(5)]


def rotate180(grid):
	return [[grid[4 - r][4 - c] for c in range(5)] for r in range(5)]


def reflect_vertical(grid):
	return [[grid[r][4 - c] for c in range(5)] for r in range(5)]


def reflect_horizontal(grid):
	return [[grid[4 - r][c] for c in range(5)] for r in range(5)]


def reflect_main_diag(grid):
	return [[grid[c][r] for c in range(5)] for r in range(5)]


def reflect_anti_diag(grid):
	return [[grid[4 - c][4 - r] for c in range(5)] for r in range(5)]


def equal_grid(a, b):
	for r in range(5):
		for c in range(5):
			if a[r][c] != b[r][c]:
				return False
	return True


def symmetry_score(grid):
	score = 0
	if equal_grid(grid, reflect_vertical(grid)): score += 1
	if equal_grid(grid, reflect_horizontal(grid)): score += 1
	if equal_grid(grid, reflect_main_diag(grid)): score += 1
	if equal_grid(grid, reflect_anti_diag(grid)): score += 1
	if equal_grid(grid, rotate90(grid)): score += 1
	if equal_grid(grid, rotate180(grid)): score += 1
	return score


# iterate combinations
TOTAL = 1 << 21
valid_bitstrings = []
meta = []  # (filled, components, score, int_repr)

print("Scanning combinations...")
from tqdm import tqdm

for n in tqdm(range(TOTAL), desc="Scanning", unit="it"):
	grid = int_to_grid(n)
	if has_filled_2x2(grid): continue
	if has_blank_2x2(grid): continue
	if has_pure_diagonal_2x2(grid): continue
	filled = sum(sum(row) for row in grid)
	comps = count_components(grid)
	score = symmetry_score(grid)
	bs = grid_to_bitstring(grid)
	valid_bitstrings.append(bs)
	meta.append((filled, comps, score, n))

print("Scan done. Valid glyphs:", len(valid_bitstrings))

# prepare master metadata
master_csv_path = OUT_BASE / "metadata.csv"
with open(master_csv_path, "w", newline="") as mf:
	mw = csv.writer(mf)
	mw.writerow(["id", "int_repr", "bitstring25", "filled", "components", "score", "filename"])
	# create per-bucket writers
	bucket_files = {}
	bucket_writers = {}
	for k in range(7):
		bucket_meta = OUT_BASE / f"score_{k}" / "metadata.csv"
		f = open(bucket_meta, "w", newline="")
		w = csv.writer(f)
		w.writerow(["id", "int_repr", "bitstring25", "filled", "components", "filename"])
		bucket_files[k] = f
		bucket_writers[k] = w

	# save images directly into their bucket images/ folder
	total = len(valid_bitstrings)
	print("Saving images into buckets (this may take a while)...")
	for idx, bs in enumerate(tqdm(valid_bitstrings, desc="Saving", unit="glyph")):
		filled, comps, score, intrepr = meta[idx]
		# filename = bitstring25.png
		fname = f"{bs}.png"
		# path inside bucket images/
		img_path = OUT_BASE / f"score_{score}" / "images" / fname
		# render PNG (black on white) at SCALE with MARGIN
		size = (5 + 2 * MARGIN) * SCALE
		img = Image.new("RGB", (size, size), (255, 255, 255))
		px = img.load()
		# recreate grid quickly from bs
		for i, ch in enumerate(bs):
			if ch == "1":
				r = i // 5
				c = i % 5
				# draw scaled pixel
				for dy in range(SCALE):
					for dx in range(SCALE):
						px[(c + MARGIN) * SCALE + dx, (r + MARGIN) * SCALE + dy] = (0, 0, 0)
		img.save(img_path)

		# write master and bucket metadata
		rel_path = f"score_{score}/images/{fname}"
		mw.writerow([idx, intrepr, bs, filled, comps, score, rel_path])
		bucket_writers[score].writerow([idx, intrepr, bs, filled, comps, f"images/{fname}"])

	# close bucket files
	for f in bucket_files.values():
		f.close()

print("All saved. Master CSV:", master_csv_path)
print("Buckets are in:", OUT_BASE)
