#!/usr/bin/env python3
"""
gen_glyphs_symmetry_sorted.py

Generates 5x5 glyphs under your rules and sorts them into symmetry buckets (0..6).
Outputs beside this script in folder: 5x5_corners_filled/
"""
from PIL import Image
from pathlib import Path
from collections import deque
import os, zipfile, csv, sys, shutil
from tqdm import tqdm

# -------------------------
# Config (adjust if needed)
# -------------------------
OUT_BASE = Path(__file__).resolve().parent / "5x5_corners_filled"
RAW_DIR = OUT_BASE / "raw_glyphs" / "images"
META_MASTER = OUT_BASE / "raw_glyphs" / "metadata.csv"
SYMM_DIR = OUT_BASE / "symmetry_sorted"
ZIP_OUT = OUT_BASE.with_suffix(".zip")  # 5x5_corners_filled.zip
SCALE = 20   # scale factor for each grid cell (pixel size)
MARGIN = 1   # margin in cells around the 5x5 when rendering
# -------------------------

# ensure directories exist
RAW_DIR.mkdir(parents=True, exist_ok=True)
for k in range(7):
    (SYMM_DIR / str(k)).mkdir(parents=True, exist_ok=True)

# grid helpers
corner_indices = {0, 4, 20, 24}
all_indices = list(range(25))
variable_indices = [i for i in all_indices if i not in corner_indices]  # 21 bits

def int_to_grid(n: int):
    """Return 5x5 grid (list of 5 lists) with corners forced filled."""
    flat = [0]*25
    for i in corner_indices:
        flat[i] = 1
    for bitpos, idx in enumerate(variable_indices):
        if (n >> bitpos) & 1:
            flat[idx] = 1
    return [flat[r*5:(r+1)*5] for r in range(5)]

def has_filled_2x2(grid):
    for r in range(4):
        for c in range(4):
            if grid[r][c] and grid[r][c+1] and grid[r+1][c] and grid[r+1][c+1]:
                return True
    return False

def has_blank_2x2(grid):
    for r in range(4):
        for c in range(4):
            if (not grid[r][c]) and (not grid[r][c+1]) and (not grid[r+1][c]) and (not grid[r+1][c+1]):
                return True
    return False

def has_pure_diagonal_2x2(grid):
    for r in range(4):
        for c in range(4):
            a = grid[r][c]
            b = grid[r][c+1]
            cc = grid[r+1][c]
            d = grid[r+1][c+1]
            if a and d and (not b) and (not cc):
                return True
            if b and cc and (not a) and (not d):
                return True
    return False

def count_components(grid):
    visited = [[False]*5 for _ in range(5)]
    dirs = [(1,0),(-1,0),(0,1),(0,-1)]
    comp = 0
    for r in range(5):
        for c in range(5):
            if grid[r][c] and not visited[r][c]:
                comp += 1
                dq = deque()
                dq.append((r,c))
                visited[r][c] = True
                while dq:
                    x,y = dq.popleft()
                    for dx,dy in dirs:
                        nx,ny = x+dx, y+dy
                        if 0<=nx<5 and 0<=ny<5 and not visited[nx][ny] and grid[nx][ny]:
                            visited[nx][ny] = True
                            dq.append((nx,ny))
    return comp

# transformation helpers for symmetry checks
def rotate90(grid):
    # grid[r][c] -> new[r][c] = grid[4-c][r]
    return [[grid[4-c][r] for c in range(5)] for r in range(5)]

def rotate180(grid):
    return [[grid[4-r][4-c] for c in range(5)] for r in range(5)]

def reflect_vertical(grid):
    # mirror across vertical center: (r,c) -> (r,4-c)
    return [[grid[r][4-c] for c in range(5)] for r in range(5)]

def reflect_horizontal(grid):
    # mirror across horizontal center: (r,c) -> (4-r,c)
    return [[grid[4-r][c] for c in range(5)] for r in range(5)]

def reflect_main_diag(grid):
    # main diag (r,c) -> (c,r)
    return [[grid[c][r] for c in range(5)] for r in range(5)]

def reflect_anti_diag(grid):
    # anti-diagonal (r,c) -> (4-c,4-r)
    return [[grid[4-c][4-r] for c in range(5)] for r in range(5)]

def equal_grid(a,b):
    for r in range(5):
        for c in range(5):
            if a[r][c] != b[r][c]:
                return False
    return True

def symmetry_score(grid):
    """Return integer 0..6 counting how many of the specified symmetries grid satisfies."""
    score = 0
    if equal_grid(grid, reflect_vertical(grid)):
        score += 1
    if equal_grid(grid, reflect_horizontal(grid)):
        score += 1
    if equal_grid(grid, reflect_main_diag(grid)):
        score += 1
    if equal_grid(grid, reflect_anti_diag(grid)):
        score += 1
    if equal_grid(grid, rotate90(grid)):
        score += 1
    if equal_grid(grid, rotate180(grid)):
        score += 1
    return score

# scanning
TOTAL = 1 << 21
valid_ints = []
meta = []

print(f"Scanning {TOTAL} combinations with tqdm...")
for n in tqdm(range(TOTAL), desc="Scanning", unit="it"):
    grid = int_to_grid(n)
    # apply rule set:
    if has_filled_2x2(grid):
        continue
    if has_blank_2x2(grid):
        continue
    if has_pure_diagonal_2x2(grid):
        continue
    filled = sum(sum(row) for row in grid)
    comps = count_components(grid)
    valid_ints.append(n)
    meta.append((filled, comps))

print("Scan complete. Valid count =", len(valid_ints))

# prepare csv writers: master and per-bucket
master_f = open(META_MASTER, "w", newline="")
master_writer = csv.writer(master_f)
master_writer.writerow(["id", "int_repr", "bitstring25", "filled", "components", "symmetry_score", "filename"])

bucket_files = {}
bucket_writers = {}
for k in range(7):
    p = SYMM_DIR / str(k) / "metadata.csv"
    f = open(p, "w", newline="")
    w = csv.writer(f)
    w.writerow(["id", "int_repr", "bitstring25", "filled", "components", "filename"])
    bucket_files[k] = f
    bucket_writers[k] = w

# saving images with progress bar
print("Saving images (raw + symmetry buckets)...")
for idx, n in enumerate(tqdm(valid_ints, desc="Saving", unit="glyph")):
    grid = int_to_grid(n)
    filled, comps = meta[idx]
    # master filename (relative inside RAW_DIR)
    fname = f"glyph_{idx:05d}.png"
    raw_path = RAW_DIR / fname

    # render image (black on white) with margin
    size = (5 + 2*MARGIN) * SCALE
    img = Image.new("RGB", (size, size), (255,255,255))
    px = img.load()
    for r in range(5):
        for c in range(5):
            if grid[r][c]:
                for i in range(SCALE):
                    for j in range(SCALE):
                        px[(c + MARGIN)*SCALE + j, (r + MARGIN)*SCALE + i] = (0,0,0)
    img.save(raw_path)

    # compute symmetry score and copy into bucket
    score = symmetry_score(grid)
    bucket_dir = SYMM_DIR / str(score)
    bucket_fname = f"glyph_{idx:05d}.png"  # same name
    bucket_path = bucket_dir / bucket_fname
    shutil.copyfile(str(raw_path), str(bucket_path))

    # write master csv row
    flat = [str(bit) for row in grid for bit in row]
    bitstr = "".join(flat)
    master_writer.writerow([idx, n, bitstr, filled, comps, score, f"images/{fname}"])

    # write per-bucket csv row
    bucket_writers[score].writerow([idx, n, bitstr, filled, comps, f"{bucket_fname}"])

# close csv files
master_f.close()
for f in bucket_files.values():
    f.close()

# create zip of OUT_BASE
print("Creating ZIP:", ZIP_OUT)
with zipfile.ZipFile(ZIP_OUT, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(OUT_BASE):
        for file in files:
            full = os.path.join(root, file)
            arcname = os.path.join(os.path.relpath(root, OUT_BASE), file)
            zf.write(full, arcname=arcname)

print("Done.")
print("Output folder:", OUT_BASE)
print("ZIP created:", ZIP_OUT)
