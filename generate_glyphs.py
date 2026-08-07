#!/usr/bin/env python3
"""
generate_glyphs.py

Single-file glyph generator + exporter.

- Edit TEMPLATE, BLACKLISTED_KERNELS, WHITELISTED_KERNELS and EXPORT_DIR below.
- Run: python generate_glyphs.py
- Output structure (if EXPORT_DIR='foo'):
    foo/
      glyphs/           <- PNGs, names are bitstrings (e.g. 010110....png)
      metadata.csv      <- bitstring,width,height,filename
      nodes.csv         <- Id,Label,filled,components
      edges.csv         <- Source,Target,Weight  (undirected edges, weight=1)
"""
from collections import deque
from pathlib import Path
import math
import csv
from tqdm import tqdm
from PIL import Image
import sys

# ---------------------------
# USER CONFIG
# ---------------------------

# TEMPLATE: rows x cols matrix of 1/0/-1 (1=must be ON, 0=must be OFF, -1=don't care)
# TEMPLATE = [
# 	[1, -1, -1, -1, 1],
# 	[-1, -1, -1, -1, -1],
# 	[-1, -1, -1, -1, -1],
# 	[-1, -1, -1, -1, -1],
# 	[1, -1, -1, -1, 1],
# ]

TEMPLATE = [
	[-1, -1, -1, -1],
	[-1, -1, -1, -1],
	[-1, -1, -1, -1],
	[-1, -1, -1, -1],
]

# TEMPLATE = [
# 	[1, -1, 1],
# 	[-1, -1, -1],
# 	[-1, -1, -1],
# 	[-1, -1, -1],
# 	[1, -1, 1],
# ]

# Blacklisted kernels (if matched anywhere -> glyph rejected).
BLACKLISTED_KERNELS = [
	[
		[1, 0],
		[0, 1]
	],
	[
		[0, 1],
		[1, 0]
	],
	[
		[1, 1],
		[1, 1]
	],
	[
		[0, 0],
		[0, 0]
	],
]

# Whitelisted kernels: at least one instance must appear somewhere in the glyph
WHITELISTED_KERNELS = [
	# Example:
	# [
	# 	[0, 1, 0],
	# 	[1, 1, 1],
	# 	[0, 1, 0]
	# ]
]

# Export folder name (edit)
EXPORT_DIR = Path("4x4")

# Image rendering params
SCALE = 20  # pixels per cell
MARGIN = 1  # cells of margin around glyph when saving individual pngs

VERBOSE = True


# ---------------------------


# ---------- helpers ----------
def template_to_forced_positions(template, w, h):
	forced_on = set()
	forced_off = set()
	for r in range(h):
		for c in range(w):
			v = template[r][c]
			idx = r * w + c
			if v == 1:
				forced_on.add(idx)
			elif v == 0:
				forced_off.add(idx)
	if forced_on & forced_off:
		raise ValueError("TEMPLATE contains conflicting forced 1 and 0 at same position(s)")
	return forced_on, forced_off


def bits_to_bitstring(bits):
	return "".join("1" if b else "0" for b in bits)


def bitlist_to_grid(bits, w, h):
	return [[bits[r * w + c] for c in range(w)] for r in range(h)]


def count_components(grid):
	h = len(grid)
	w = len(grid[0])
	visited = [[False] * w for _ in range(h)]
	dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
	comp = 0
	for r in range(h):
		for c in range(w):
			if grid[r][c] and not visited[r][c]:
				comp += 1
				dq = deque()
				dq.append((r, c))
				visited[r][c] = True
				while dq:
					x, y = dq.popleft()
					for dx, dy in dirs:
						nx, ny = x + dx, y + dy
						if 0 <= nx < h and 0 <= ny < w and not visited[nx][ny] and grid[nx][ny]:
							visited[nx][ny] = True
							dq.append((nx, ny))
	return comp


def kernel_matches_at(bits, w, h, kernel, anchor_r, anchor_c):
	kh = len(kernel)
	kw = len(kernel[0])
	for kr in range(kh):
		for kc in range(kw):
			kv = kernel[kr][kc]
			if kv == -1:
				continue
			r = anchor_r + kr
			c = anchor_c + kc
			if r < 0 or r >= h or c < 0 or c >= w:
				return False
			if bits[r * w + c] != kv:
				return False
	return True


def precompute_kernel_anchors(w, h, kernel):
	kh = len(kernel)
	kw = len(kernel[0])
	anchors = []
	for ar in range(h - kh + 1):
		for ac in range(w - kw + 1):
			covered = []
			max_idx = -1
			for kr in range(kh):
				for kc in range(kw):
					r = ar + kr
					c = ac + kc
					idx = r * w + c
					covered.append(idx)
					if idx > max_idx:
						max_idx = idx
			anchors.append({"anchor": (ar, ac), "covered": covered, "max_idx": max_idx})
	return anchors


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


# ---------- main generation & export ----------
def generate_and_export(export_dir: Path):
	# derive sizes
	if TEMPLATE is None:
		raise SystemExit("TEMPLATE must be provided (list of rows).")
	h = len(TEMPLATE)
	if h == 0:
		raise SystemExit("TEMPLATE must have at least one row.")
	w = len(TEMPLATE[0])
	if any(len(row) != w for row in TEMPLATE):
		raise SystemExit("All TEMPLATE rows must have the same length.")
	nbits = w * h

	# precompute anchors
	blacklist_pre = [{"kernel": k, "anchors": precompute_kernel_anchors(w, h, k)} for k in BLACKLISTED_KERNELS]
	whitelist_pre = [{"kernel": k, "anchors": precompute_kernel_anchors(w, h, k)} for k in WHITELISTED_KERNELS]

	forced_on, forced_off = template_to_forced_positions(TEMPLATE, w, h)

	# quick sanity
	if any((p < 0 or p >= nbits) for p in forced_on | forced_off):
		raise ValueError("Some forced positions are out of range for this glyph size")
	if forced_on & forced_off:
		raise ValueError("Conflict: some positions are forced both ON and OFF: " + str(sorted(forced_on & forced_off)))

	# mapping for blacklist anchors by max_idx
	blacklist_map_by_maxidx = {}
	for item in blacklist_pre:
		k = item["kernel"]
		for a in item["anchors"]:
			m = a["max_idx"]
			blacklist_map_by_maxidx.setdefault(m, []).append((k, a))

	# whitelist anchors map (we'll check at leaf time)
	whitelist_map_by_maxidx = {}
	for item in whitelist_pre:
		k = item["kernel"]
		for a in item["anchors"]:
			m = a["max_idx"]
			whitelist_map_by_maxidx.setdefault(m, []).append((k, a))

	bits = [0] * nbits
	for p in forced_on:
		bits[p] = 1

	glyphs = []  # will hold dicts: bitstring,int_repr,filled,filled_ratio,components,w,h

	def anchor_matches_current(bits_local, kernel, anchor_info):
		ar, ac = anchor_info["anchor"]
		return kernel_matches_at(bits_local, w, h, kernel, ar, ac)

	# total assignments (theoretical)
	total_assignments = 2 ** (nbits - len(forced_on) - len(forced_off))
	if VERBOSE:
		print(f"Starting generation for {w}x{h} glyphs ({nbits} bits), theoretical assignments={total_assignments}")
		print(f"Blacklisted kernels: {len(BLACKLISTED_KERNELS)}; Whitelisted kernels: {len(WHITELISTED_KERNELS)}")

	assign_bar = tqdm(total=total_assignments, desc="Leaves", unit="leaf")

	leaf_count = 0

	def backtrack(pos):
		nonlocal leaf_count
		if pos == nbits:
			leaf_count += 1
			assign_bar.update(1)

			# candidate leaf
			bs = bits_to_bitstring(bits)
			intrepr = int(bs, 2)
			grid = bitlist_to_grid(bits, w, h)

			# whitelist: if defined, require at least one anchor match somewhere
			if WHITELISTED_KERNELS:
				found = False
				for item in whitelist_pre:
					k = item["kernel"]
					for a in item["anchors"]:
						if anchor_matches_current(bits, k, a):
							found = True
							break
					if found:
						break
				if not found:
					return

			filled = sum(bits)
			filled_ratio = filled / nbits
			comps = count_components(grid)

			glyphs.append({
				"bitstring": bs,
				"int_repr": intrepr,
				"filled": filled,
				"filled_ratio": filled_ratio,
				"components": comps,
				"w": w,
				"h": h,
			})
			return

		# forced-on pos
		if pos in forced_on:
			if pos in blacklist_map_by_maxidx:
				for k, a in blacklist_map_by_maxidx[pos]:
					if anchor_matches_current(bits, k, a):
						return
			backtrack(pos + 1)
			return

		# forced-off pos
		if pos in forced_off:
			bits[pos] = 0
			if pos in blacklist_map_by_maxidx:
				for k, a in blacklist_map_by_maxidx[pos]:
					if anchor_matches_current(bits, k, a):
						# reset (already zero but to be explicit)
						bits[pos] = 0
						return
			backtrack(pos + 1)
			return

		# try 0
		bits[pos] = 0
		pruned = False
		if pos in blacklist_map_by_maxidx:
			for k, a in blacklist_map_by_maxidx[pos]:
				if anchor_matches_current(bits, k, a):
					pruned = True
					break
		if not pruned:
			backtrack(pos + 1)

		# try 1
		bits[pos] = 1
		pruned = False
		if pos in blacklist_map_by_maxidx:
			for k, a in blacklist_map_by_maxidx[pos]:
				if anchor_matches_current(bits, k, a):
					pruned = True
					break
		if not pruned:
			backtrack(pos + 1)

		# reset
		bits[pos] = 0

	# Run backtracking
	backtrack(0)
	assign_bar.close()
	if VERBOSE:
		print(f"Generation complete. Leaves visited: {leaf_count}. Valid glyphs: {len(glyphs)}")

	# EXPORT: write images + metadata + nodes/edges
	export_dir.mkdir(parents=True, exist_ok=True)
	glyphs_dir = export_dir / "glyphs"
	glyphs_dir.mkdir(parents=True, exist_ok=True)

	metadata_path = export_dir / "metadata.csv"
	nodes_path = export_dir / "nodes.csv"
	edges_path = export_dir / "edges.csv"

	# write individual pngs and minimal metadata (bitstring,width,height,filename)
	with open(metadata_path, "w", newline="") as mf:
		mw = csv.writer(mf)
		mw.writerow(["bitstring", "width", "height", "filename"])
		for g in tqdm(glyphs, desc="Rendering glyph PNGs", unit="glyph"):
			bs = g["bitstring"]
			g_w = g["w"]
			g_h = g["h"]
			grid = bitlist_to_grid([1 if ch == "1" else 0 for ch in bs], g_w, g_h)
			fname = f"{bs}.png"
			out_path = glyphs_dir / fname
			render_grid_to_png(grid, SCALE, MARGIN, out_path)
			mw.writerow([bs, g_w, g_h, f"glyphs/{fname}"])

	# write nodes.csv (Id,Label,filled,components)
	with open(nodes_path, "w", newline="") as nf:
		nw = csv.writer(nf)
		nw.writerow(["Id", "Label", "filled", "components"])
		for g in glyphs:
			bs = g["bitstring"]
			nw.writerow([bs, bs, g["filled"], g["components"]])

	# write edges.csv (pairwise Hamming distance 1)
	bitset = set(g["bitstring"] for g in glyphs)
	nbits_local = nbits
	with open(edges_path, "w", newline="") as ef:
		ew = csv.writer(ef)
		ew.writerow(["Source", "Target", "Weight"])
		for g in tqdm(glyphs, desc="Generating edges", unit="node"):
			bs = g["bitstring"]
			bl = list(bs)
			for i in range(nbits_local):
				orig = bl[i]
				bl[i] = "0" if orig == "1" else "1"
				neigh = "".join(bl)
				bl[i] = orig
				# write only once for undirected graph
				if neigh in bitset and bs < neigh:
					ew.writerow([bs, neigh, 1])

	if VERBOSE:
		print("Export complete:")
		print(" - metadata:", metadata_path)
		print(" - glyph PNGs:", glyphs_dir)
		print(" - nodes:", nodes_path)
		print(" - edges:", edges_path)
		print(f"Total glyphs exported: {len(glyphs)}")

	return export_dir


if __name__ == "__main__":
	# Run generator
	out = generate_and_export(EXPORT_DIR)
	print("Done. Export folder:", out)
