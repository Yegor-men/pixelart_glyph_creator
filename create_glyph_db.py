#!/usr/bin/env python3
"""
create_glyph_db.py

Create an SQLite database of glyph bitstrings for arbitrary glyph sizes,
with blacklist/whitelist kernel rules and optional symmetry filters.

Example usage:
    python create_glyph_db.py

Configuration is near the top of the file (NO CLI parsing for brevity,
but easy to add later).
"""

import sqlite3
from collections import deque
import math
import ast
from pathlib import Path

# ---------------------------
# CONFIG - edit to taste
# ---------------------------
GLYPH_W = 5
GLYPH_H = 5

# Blacklisted kernels (if matched anywhere -> glyph rejected).
# Kernel format: list of rows; cell values: 1 (on), 0 (off), -1 (don't care).
BLACKLISTED_KERNELS = [
	# 2x2 diagonal 1
	[[1, 0],
	 [0, 1]],
	# 2x2 diagonal 2
	[[0, 1],
	 [1, 0]],
	# 2x2 all ones
	[[1, 1],
	 [1, 1]],
	# 2x2 all zeros
	[[0, 0],
	 [0, 0]],
]

# Whitelisted kernels: at least one instance must appear somewhere in the glyph
# (if you want to force corners, use a 5x5 kernel with 1 at corners and -1 elsewhere).
WHITELISTED_KERNELS = [
	# Example: ensure 4 corners of a 5x5 are filled (same idea as original).
	# For GLYPH_W=5, GLYPH_H=5 this kernel is full-size.
	[[1, -1, -1, -1, 1],
	 [-1, -1, -1, -1, -1],
	 [-1, -1, -1, -1, -1],
	 [-1, -1, -1, -1, -1],
	 [1, -1, -1, -1, 1]]
]

# Alternatively, set WHITELISTED_KERNELS = [] to not require any whitelist match.
# To replicate your original "corners forced" behavior you can keep the kernel above.

# Optional explicit forced-on positions as linear indices (row-major).
# Example for 5x5 corners: [0,4,20,24]
FORCED_ON_POSITIONS = []  # [] by default; you can set [0,4,20,24]

# Symmetry rule as logical expression over: horizontal, vertical, diag1, diag2, rot90, rot180
# Examples:
#  - "horizontal or vertical or diag1 or diag2 or rot90 or rot180"  (at least one symmetry)
#  - "horizontal and vertical"  (both)
#  - "" or None -> no symmetry filtering (we will still compute symmetry columns)
# SYMMETRY_RULE = "horizontal or vertical or diag1 or diag2 or rot90 or rot180"
SYMMETRY_RULE = ""

# Output DB filename
OUT_DB = Path("dbs") / f"glyphs_{GLYPH_W}_{GLYPH_H}.db"
OUT_DB.parent.mkdir(parents=True, exist_ok=True)

# Whether to print progress counts to stdout
VERBOSE = True


# ---------------------------


# utility: convert bitlist to bitstring
def bits_to_bitstring(bits):
	return "".join("1" if b else "0" for b in bits)


def bitstring_to_int(bs):
	return int(bs, 2)


# grid helpers
def bitlist_to_grid(bits, w, h):
	return [[bits[r * w + c] for c in range(w)] for r in range(h)]


def grid_to_bitlist(grid):
	return [1 if v else 0 for row in grid for v in row]


# symmetry functions
def rotate90(grid):
	h = len(grid)
	w = len(grid[0])
	return [[grid[h - 1 - c][r] for c in range(h)] for r in range(w)]


def rotate180(grid):
	return [list(reversed(row)) for row in reversed(grid)]


def reflect_vertical(grid):
	# vertical axis (mirror left-right)
	return [list(reversed(row)) for row in grid]


def reflect_horizontal(grid):
	# horizontal axis (mirror top-bottom)
	return list(reversed([list(r) for r in grid]))


def reflect_main_diag(grid):
	h = len(grid)
	w = len(grid[0])
	return [[grid[c][r] for c in range(h)] for r in range(w)]


def reflect_anti_diag(grid):
	h = len(grid)
	w = len(grid[0])
	return [[grid[w - 1 - c][h - 1 - r] for c in range(w)] for r in range(h)]


def equal_grid(a, b):
	if len(a) != len(b) or len(a[0]) != len(b[0]):
		return False
	for r in range(len(a)):
		for c in range(len(a[0])):
			if bool(a[r][c]) != bool(b[r][c]):
				return False
	return True


def compute_symmetries(grid):
	"""
	Returns dict with boolean values for:
	horizontal, vertical, diag1 (main), diag2 (anti), rot90, rot180
	"""
	h = len(grid)
	w = len(grid[0])
	# canonicalize to same shape before comparison if rotations swap dims
	# We'll compare by computing the transformed grid and then comparing with original.
	sym = {}
	sym["vertical"] = equal_grid(grid, reflect_vertical(grid))
	sym["horizontal"] = equal_grid(grid, reflect_horizontal(grid))
	sym["diag1"] = False
	sym["diag2"] = False
	sym["rot90"] = False
	sym["rot180"] = False
	# diag1 and diag2 only make geometric sense when w == h (square),
	# but we still compute compare if shapes match after transpose.
	try:
		sym["diag1"] = equal_grid(grid, reflect_main_diag(grid))
	except Exception:
		sym["diag1"] = False
	try:
		sym["diag2"] = equal_grid(grid, reflect_anti_diag(grid))
	except Exception:
		sym["diag2"] = False
	# rotations
	try:
		sym["rot90"] = equal_grid(grid, rotate90(grid))
	except Exception:
		sym["rot90"] = False
	try:
		sym["rot180"] = equal_grid(grid, rotate180(grid))
	except Exception:
		sym["rot180"] = False
	return sym


# Connected components (4-neighbor)
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


# Kernel matching
def kernel_matches_at(bits, w, h, kernel, anchor_r, anchor_c):
	"""
	bits: flat list length w*h (0/1)
	kernel: list of rows with values in {1,0,-1}
	anchor_r, anchor_c: top-left position where kernel is placed
	"""
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
			bit = bits[r * w + c]
			if bit != kv:
				return False
	return True


# Precompute anchors for each kernel (list of anchors and their covered linear indices)
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


# safe evaluator for symmetry expression
class SafeBoolExprEvaluator(ast.NodeVisitor):
	"""
	Accepts a boolean expression AST comprised of Names, BoolOps (and/or), UnaryOp(not),
	and Parens. Names resolved from `mapping`. Raises on unsafe nodes.
	"""

	def __init__(self, mapping):
		self.mapping = mapping

	def visit(self, node):
		if isinstance(node, ast.Expression):
			return self.visit(node.body)
		elif isinstance(node, ast.BoolOp):
			if isinstance(node.op, ast.And):
				return all(self.visit(v) for v in node.values)
			elif isinstance(node.op, ast.Or):
				return any(self.visit(v) for v in node.values)
			else:
				raise ValueError("Unsupported boolean op")
		elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
			return not self.visit(node.operand)
		elif isinstance(node, ast.Name):
			if node.id in self.mapping:
				return bool(self.mapping[node.id])
			else:
				raise ValueError(f"Unknown name in symmetry rule: {node.id}")
		elif isinstance(node, ast.Constant):
			if isinstance(node.value, bool):
				return node.value
			raise ValueError("Only boolean literals allowed")
		elif isinstance(node, ast.Call):
			raise ValueError("Function calls not allowed")
		else:
			raise ValueError(f"Unsupported expression node: {type(node)}")


def eval_symmetry_rule(rule_str, sym_map):
	if not rule_str:
		return True
	# we allow 'and', 'or', 'not' and names from sym_map
	try:
		tree = ast.parse(rule_str, mode="eval")
		evaluator = SafeBoolExprEvaluator(sym_map)
		return evaluator.visit(tree)
	except Exception as e:
		raise ValueError(f"Invalid symmetry rule '{rule_str}': {e}")


# -----------------------
# Main generation logic
# -----------------------
def create_db(db_path):
	w, h = GLYPH_W, GLYPH_H
	nbits = w * h

	# precompute kernel anchors for pruning (blacklist) and final checks (whitelist)
	blacklist_pre = []
	for k in BLACKLISTED_KERNELS:
		blacklist_pre.append({
			"kernel": k,
			"anchors": precompute_kernel_anchors(w, h, k)
		})
	whitelist_pre = []
	for k in WHITELISTED_KERNELS:
		whitelist_pre.append({
			"kernel": k,
			"anchors": precompute_kernel_anchors(w, h, k)
		})

	# forced-on positions set
	forced_on = set(FORCED_ON_POSITIONS)

	# prepare DB
	conn = sqlite3.connect(str(db_path))
	cur = conn.cursor()

	# tables
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
                    horizontal
                    INTEGER,
                    vertical
                    INTEGER,
                    diag1
                    INTEGER,
                    diag2
                    INTEGER,
                    rot90
                    INTEGER,
                    rot180
                    INTEGER,
                    score
                    INTEGER
                )
				""")
	# store meta about generation
	cur.execute("""
                CREATE TABLE IF NOT EXISTS meta
                (
                    k
                    TEXT
                    PRIMARY
                    KEY,
                    v
                    TEXT
                )
				""")
	cur.execute("DELETE FROM meta WHERE k IN ('w','h','nbits')")
	cur.execute("INSERT OR REPLACE INTO meta(k,v) VALUES (?,?)", ("w", str(w)))
	cur.execute("INSERT OR REPLACE INTO meta(k,v) VALUES (?,?)", ("h", str(h)))
	cur.execute("INSERT OR REPLACE INTO meta(k,v) VALUES (?,?)", ("nbits", str(nbits)))
	conn.commit()

	# incremental/backtracking enumerator with pruning on blacklisted kernels
	insert_count = 0

	# Precompute for each index which kernel anchors become 'fully set' when this index is written.
	# For all blacklist anchors, we will map max_idx -> list of (kernel, anchor_info)
	blacklist_map_by_maxidx = {}
	for item in blacklist_pre:
		k = item["kernel"]
		for a in item["anchors"]:
			m = a["max_idx"]
			blacklist_map_by_maxidx.setdefault(m, []).append((k, a))

	# similarly for whitelists (useful if you want to detect a match early and flag)
	whitelist_map_by_maxidx = {}
	for item in whitelist_pre:
		k = item["kernel"]
		for a in item["anchors"]:
			m = a["max_idx"]
			whitelist_map_by_maxidx.setdefault(m, []).append((k, a))

	# We'll do simple row-major generation.
	bits = [0] * nbits
	# pre-set forced-on positions
	for p in forced_on:
		bits[p] = 1

	# For faster kernel matching in incremental stage, create a mapping from anchor covered indices
	def anchor_matches_current(bits, kernel, anchor_info):
		# anchor_info has 'anchor', 'covered'
		ar, ac = anchor_info["anchor"]
		return kernel_matches_at(bits, w, h, kernel, ar, ac)

	# recursive/backtracking
	def backtrack(pos):
		nonlocal insert_count
		# skipping positions already forced on: forced positions should still be considered "set"
		if pos == nbits:
			# finished candidate: perform final checks (whitelist, symmetry rule)
			bs = bits_to_bitstring(bits)
			intrepr = int(bs, 2)
			grid = bitlist_to_grid(bits, w, h)
			# whitelist check: if any whitelist exists, require at least one match
			ok = True
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
					ok = False
			if not ok:
				return

			# compute metadata
			filled = sum(bits)
			filled_ratio = filled / nbits
			comps = count_components(grid)
			syms = compute_symmetries(grid)
			score = sum(1 for v in syms.values() if v)

			# check symmetry_rule (if provided)
			if SYMMETRY_RULE:
				try:
					passes_sym_rule = eval_symmetry_rule(SYMMETRY_RULE, syms)
				except Exception as e:
					raise RuntimeError(f"Error evaluating symmetry rule: {e}")
				if not passes_sym_rule:
					return

			# insert into DB
			cur.execute("""
                        INSERT
                        OR IGNORE INTO glyphs (
                bitstring,int_repr,filled,filled_ratio,components,
                horizontal,vertical,diag1,diag2,rot90,rot180,score
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
						""", (
							bs, intrepr, filled, filled_ratio, comps,
							1 if syms["horizontal"] else 0,
							1 if syms["vertical"] else 0,
							1 if syms["diag1"] else 0,
							1 if syms["diag2"] else 0,
							1 if syms["rot90"] else 0,
							1 if syms["rot180"] else 0,
							score
						))
			insert_count += 1
			if VERBOSE and insert_count % 5000 == 0:
				conn.commit()
				print(f"Inserted {insert_count} glyphs so far...")
			return

		# if this position is forced-on, it is already set (we set them earlier)
		if pos in forced_on:
			# skip writing (it's already 1) but we still need to run pruning checks as if it was just placed
			# to simplify, we treat it as placed and proceed
			# but we must ensure we don't reassign it here
			# After "placing" we still need to check any blacklist anchors whose max idx == pos
			if pos in blacklist_map_by_maxidx:
				for k, a in blacklist_map_by_maxidx[pos]:
					# check match
					if anchor_matches_current(bits, k, a):
						return  # prune
			# continue
			backtrack(pos + 1)
			return

		# try 0
		bits[pos] = 0
		# prune: check any blacklist anchors whose max_idx == pos
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

		# reset (not strictly necessary because it will be overwritten)
		bits[pos] = 0

	# run generator
	if VERBOSE:
		print(f"Starting generation for {w}x{h} glyphs ({nbits} bits)...")
		print(f"Output DB: {db_path}")
		print(f"Blacklisted kernels: {len(BLACKLISTED_KERNELS)}; Whitelisted kernels: {len(WHITELISTED_KERNELS)}")
		if SYMMETRY_RULE:
			print(f"Applying symmetry rule: {SYMMETRY_RULE}")

	backtrack(0)
	conn.commit()
	if VERBOSE:
		print(f"Generation complete. Inserted {insert_count} glyphs.")
	conn.close()


if __name__ == "__main__":
	create_db(OUT_DB)
