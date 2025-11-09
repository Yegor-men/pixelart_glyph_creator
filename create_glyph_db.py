#!/usr/bin/env python3
"""
create_glyph_db.py

Generates an SQLite DB of glyph bitstrings driven by TEMPLATE shape.
- TEMPLATE defines glyph H x W and forced-on (1) / forced-off (0) / don't-care (-1).
- Blacklisted kernels prune during search; whitelisted kernels are required (leaf-time).
- Initializes assigned Bradley-Terry (assigned_bt) to 0.5 for every created glyph and
  assigned_logit to 0.0. predicted_bt is left NULL for the model to fill later.

Writes DB to: dbs/glyphs_{W}_{H}.db
"""

import sqlite3
from collections import deque
from pathlib import Path
from tqdm import tqdm
import math

# ---------------------------
# CONFIG (edit template & kernels)
# ---------------------------

# TEMPLATE: rows x cols matrix of 1/0/-1 (1=must be ON, 0=must be OFF, -1=don't care)
TEMPLATE = [
	[1, -1, -1, -1, 1],
	[-1, -1, -1, -1, -1],
	[-1, -1, -1, -1, -1],
	[-1, -1, -1, -1, -1],
	[1, -1, -1, -1, 1],
]

# Blacklisted kernels (if matched anywhere -> glyph rejected).
BLACKLISTED_KERNELS = [
	[[1, 0], [0, 1]],
	[[0, 1], [1, 0]],
	[[1, 1], [1, 1]],
	[[0, 0], [0, 0]],
]

# Whitelisted kernels: at least one instance must appear somewhere in the glyph
WHITELISTED_KERNELS = [
	# Example: uncomment to require a plus-shape 3x3 somewhere
	# [
	#    [0,1,0],
	#    [1,1,1],
	#    [0,1,0]
	# ]
]

VERBOSE = True
# ---------------------------

# derive w,h from TEMPLATE
if TEMPLATE is None:
	raise SystemExit("TEMPLATE must be provided (list of rows).")
H = len(TEMPLATE)
if H == 0:
	raise SystemExit("TEMPLATE must have at least one row.")
W = len(TEMPLATE[0])
if any(len(row) != W for row in TEMPLATE):
	raise SystemExit("All TEMPLATE rows must have the same length.")
OUT_DB = Path("dbs") / f"glyphs_{W}_{H}.db"
OUT_DB.parent.mkdir(parents=True, exist_ok=True)


# helpers ---------------------------------------------------------
def template_to_forced_positions(template, w, h):
	"""
	Return two sets (forced_on, forced_off) of linear indices.
	"""
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
	# sanity
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


# kernel matching (unchanged)
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


# -----------------------
# MAIN: create DB
# -----------------------
def create_db(db_path):
	w = W
	h = H
	nbits = w * h

	# precompute kernel anchors
	blacklist_pre = [{"kernel": k, "anchors": precompute_kernel_anchors(w, h, k)} for k in BLACKLISTED_KERNELS]
	whitelist_pre = [{"kernel": k, "anchors": precompute_kernel_anchors(w, h, k)} for k in WHITELISTED_KERNELS]

	# forced positions derived from TEMPLATE
	forced_on, forced_off = template_to_forced_positions(TEMPLATE, w, h)

	# sanity checks
	if any((p < 0 or p >= nbits) for p in forced_on | forced_off):
		raise ValueError("Some forced positions are out of range for this glyph size")
	if forced_on & forced_off:
		raise ValueError("Conflict: some positions are forced both ON and OFF: " + str(sorted(forced_on & forced_off)))

	conn = sqlite3.connect(str(db_path))
	cur = conn.cursor()

	# create table: store assigned_bt (starts at 0.5), assigned_logit (0.0), predicted_bt (NULL)
	cur.execute("""
                CREATE TABLE IF NOT EXISTS glyphs
                (
                    id
                    INTEGER
                    PRIMARY
                    KEY
                    AUTOINCREMENT,
                    bitstring
                    TEXT
                    UNIQUE,
                    int_repr
                    INTEGER,
                    filled
                    INTEGER,
                    filled_ratio
                    REAL,
                    components
                    INTEGER,
                    assigned_bt
                    REAL,
                    assigned_logit
                    REAL,
                    predicted_bt
                    REAL
                )
				""")
	cur.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
	cur.execute("DELETE FROM meta WHERE k IN ('w','h','nbits')")
	cur.execute("INSERT OR REPLACE INTO meta(k,v) VALUES (?,?)", ("w", str(w)))
	cur.execute("INSERT OR REPLACE INTO meta(k,v) VALUES (?,?)", ("h", str(h)))
	cur.execute("INSERT OR REPLACE INTO meta(k,v) VALUES (?,?)", ("nbits", str(nbits)))
	conn.commit()

	# blacklist mapping by max_idx for pruning
	blacklist_map_by_maxidx = {}
	for item in blacklist_pre:
		k = item["kernel"]
		for a in item["anchors"]:
			m = a["max_idx"]
			blacklist_map_by_maxidx.setdefault(m, []).append((k, a))

	# whitelist anchors (checked at leaf-time)
	whitelist_map_by_maxidx = {}
	for item in whitelist_pre:
		k = item["kernel"]
		for a in item["anchors"]:
			m = a["max_idx"]
			whitelist_map_by_maxidx.setdefault(m, []).append((k, a))

	# init bits and apply forced sets
	bits = [0] * nbits
	for p in forced_on:
		bits[p] = 1

	# forced_off positions already 0

	def anchor_matches_current(bits, kernel, anchor_info):
		ar, ac = anchor_info["anchor"]
		return kernel_matches_at(bits, w, h, kernel, ar, ac)

	insert_count = 0
	leaf_count = 0

	# prints before bar
	total_assignments = 2 ** (nbits - len(forced_on) - len(forced_off))
	if VERBOSE:
		print(f"Starting generation for {w}x{h} glyphs ({nbits} bits), assignments={total_assignments}")
		print(f"Output DB: {db_path}")
		print(f"Blacklisted kernels: {len(BLACKLISTED_KERNELS)}; Whitelisted kernels: {len(WHITELISTED_KERNELS)}")

	assign_bar = tqdm(total=total_assignments, desc="Assignments", unit="assign")

	# backtracking
	def backtrack(pos):
		nonlocal insert_count, leaf_count
		if pos == nbits:
			leaf_count += 1
			assign_bar.update(1)

			# candidate leaf
			bs = bits_to_bitstring(bits)
			intrepr = int(bs, 2)
			grid = bitlist_to_grid(bits, w, h)

			# whitelist: require at least one anchor match if any whitelist kernels provided
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

			# initialize assigned BT fields
			assigned_bt = 0.5
			assigned_logit = 0.0
			predicted_bt = None

			cur.execute("""
                        INSERT
                        OR IGNORE INTO glyphs (
                    bitstring,int_repr,filled,filled_ratio,components,assigned_bt,assigned_logit,predicted_bt
                ) VALUES (?,?,?,?,?,?,?,?)
						""", (bs, intrepr, filled, filled_ratio, comps, assigned_bt, assigned_logit, predicted_bt))
			if cur.rowcount != 0:
				insert_count += 1
				assign_bar.set_postfix(inserted=insert_count)
			# occasional commit
			if VERBOSE and insert_count % 5000 == 0:
				conn.commit()
			return

		# if forced-on, skip branching but still check blacklist anchors that finalize at this pos
		if pos in forced_on:
			if pos in blacklist_map_by_maxidx:
				for k, a in blacklist_map_by_maxidx[pos]:
					if anchor_matches_current(bits, k, a):
						return
			backtrack(pos + 1)
			return

		# if forced-off, skip branching similarly
		if pos in forced_off:
			bits[pos] = 0
			if pos in blacklist_map_by_maxidx:
				for k, a in blacklist_map_by_maxidx[pos]:
					if anchor_matches_current(bits, k, a):
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

		# reset bit
		bits[pos] = 0

	# run
	backtrack(0)
	conn.commit()
	assign_bar.close()
	if VERBOSE:
		print(f"Generation complete. Inserted {insert_count} glyphs. Leaves visited: {leaf_count}")
	conn.close()


if __name__ == "__main__":
	create_db(OUT_DB)
