# gen_glyphs_no_blank2x2.py
# Generates the set of 5x5 glyphs under these rules:
#  - 5x5 grid, four corners (indices 0,4,20,24) must be filled
#  - no fully-filled 2x2 block (no 2x2 with 1111)
#  - no fully-blank 2x2 block (no 2x2 with 0000)
#  - no "pure diagonal" in any 2x2 (for each 2x2, forbid 1001 and 0110 patterns)
#
# Outputs (in repo folder /glyphs):
#  - glyphs/glyphs_no_blank2x2/   (PNGs + metadata.csv)
#  - glyphs/glyphs_no_blank2x2_rules.zip

from PIL import Image
import os, zipfile, csv, sys
from collections import deque

# Output dirs and zip paths (placed beside this script)
OUT_BASE = os.path.join(os.path.dirname(__file__), "glyphs")
DIR_OUT = os.path.join(OUT_BASE, "glyphs_no_blank2x2")
ZIP_OUT = os.path.join(OUT_BASE, "glyphs_no_blank2x2_rules.zip")

os.makedirs(DIR_OUT, exist_ok=True)

# Grid helpers
corner_indices = {0, 4, 20, 24}
all_indices = list(range(25))
variable_indices = [i for i in all_indices if i not in corner_indices]  # 21 bits


def int_to_grid(n):
	"""Convert integer (21 variable bits) to 5x5 grid with corners forced."""
	grid = [0] * 25
	for i in corner_indices:
		grid[i] = 1
	for bitpos, idx in enumerate(variable_indices):
		if (n >> bitpos) & 1:
			grid[idx] = 1
	# return as rows
	return [grid[r * 5:(r + 1) * 5] for r in range(5)]


def has_filled_2x2(grid):
	"""Return True if any 2x2 is fully filled (1111)."""
	for r in range(4):
		for c in range(4):
			if grid[r][c] and grid[r][c + 1] and grid[r + 1][c] and grid[r + 1][c + 1]:
				return True
	return False


def has_blank_2x2(grid):
	"""Return True if any 2x2 is fully blank (0000)."""
	for r in range(4):
		for c in range(4):
			if (not grid[r][c]) and (not grid[r][c + 1]) and (not grid[r + 1][c]) and (not grid[r + 1][c + 1]):
				return True
	return False


def has_pure_diagonal_2x2(grid):
	"""
	Return True if any 2x2 contains a pure diagonal pair only:
	  forbids patterns:
		a b
		c d
	  where (a==1 and d==1 and b==0 and c==0)  -> 1001
	  or    (a==0 and d==0 and b==1 and c==1)  -> 0110
	This allows diagonal pairs when at least one orthogonal neighbor in that 2x2 is also filled
	(e.g. 1110 is OK).
	"""
	for r in range(4):
		for c in range(4):
			a = grid[r][c]
			b = grid[r][c + 1]
			c_ = grid[r + 1][c]
			d = grid[r + 1][c + 1]
			if a and d and (not b) and (not c_):
				return True
			if b and c_ and (not a) and (not d):
				return True
	return False


def count_components(grid):
	"""4-connected components count for filled pixels."""
	visited = [[False] * 5 for _ in range(5)]
	dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
	comp = 0
	for r in range(5):
		for c in range(5):
			if grid[r][c] and not visited[r][c]:
				comp += 1
				dq = deque()
				dq.append((r, c));
				visited[r][c] = True
				while dq:
					x, y = dq.popleft()
					for dx, dy in dirs:
						nx, ny = x + dx, y + dy
						if 0 <= nx < 5 and 0 <= ny < 5 and not visited[nx][ny] and grid[nx][ny]:
							visited[nx][ny] = True
							dq.append((nx, ny))
	return comp


# Iterate all possibilities and collect valid ones under the combined rule set
TOTAL = 1 << 21
valid = []
meta = []

print("Scanning all combinations (2^21 = {})...".format(TOTAL))
sys.stdout.flush()
for n in range(TOTAL):
	grid = int_to_grid(n)
	# apply rules
	if has_filled_2x2(grid):  # disallow fully filled 2x2
		continue
	if has_blank_2x2(grid):  # disallow fully blank 2x2 (your selected rule)
		continue
	if has_pure_diagonal_2x2(grid):  # disallow pure diagonal-only pairs in any 2x2
		continue
	# passed all rules
	filled = sum(sum(row) for row in grid)
	comps = count_components(grid)
	valid.append(n)
	meta.append((filled, comps))
	if (n & 0x1FFFF) == 0 and n > 0:
		print("Reached", n, "valid so far", len(valid))
		sys.stdout.flush()

print("Scan complete. Valid count =", len(valid))


# Image save helper: black glyph on white background, with optional margin
def save_grid_png(grid, path, scale=20, margin=1):
	size = (5 + 2 * margin) * scale
	img = Image.new("RGB", (size, size), (255, 255, 255))  # white background
	px = img.load()
	for r in range(5):
		for c in range(5):
			if grid[r][c]:
				for i in range(scale):
					for j in range(scale):
						px[(c + margin) * scale + j, (r + margin) * scale + i] = (0, 0, 0)
	img.save(path)


# Write images + metadata.csv
csv_path = os.path.join(DIR_OUT, "metadata.csv")
with open(csv_path, "w", newline="") as cf:
	writer = csv.writer(cf)
	writer.writerow(["id", "int_repr", "bitstring25", "filled", "components", "filename"])
	for idx, n in enumerate(valid):
		grid = int_to_grid(n)
		flat = [str(bit) for row in grid for bit in row]
		bitstr = "".join(flat)
		fname = f"glyph_{idx:04d}.png"
		save_grid_png(grid, os.path.join(DIR_OUT, fname), scale=20, margin=1)
		writer.writerow([idx, n, bitstr, meta[idx][0], meta[idx][1], fname])
		if idx % 500 == 0 and idx > 0:
			print("Saved", idx, "images...")
			sys.stdout.flush()

# Create ZIP
print("Creating ZIP:", ZIP_OUT)
with zipfile.ZipFile(ZIP_OUT, "w", compression=zipfile.ZIP_DEFLATED) as zf:
	for root, dirs, files in os.walk(DIR_OUT):
		for file in files:
			zf.write(os.path.join(root, file), arcname=os.path.join(os.path.relpath(root, DIR_OUT), file))

print("Done. Output folder:", DIR_OUT)
print("ZIP created:", ZIP_OUT)
