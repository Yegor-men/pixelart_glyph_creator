#!/usr/bin/env python3
"""
create_glyph_db.py

Generates a DB of square glyphs (GLYPH_SIDE x GLYPH_SIDE) using blacklist/whitelist
kernels, a TEMPLATE to force on/off pixels, optional symmetry-rule filtering.
Stores only boolean symmetry columns and an integer symmetry score in the DB (no floats).

Shows a tqdm bar "Assignments" (total = 2 ** (nbits - forced_fixed_bits)).
The bar's postfix shows how many glyphs have been inserted into the DB so far.
No intermediate prints while running; a single summary print is shown at the end.
"""

import sqlite3
from collections import deque
import ast
from pathlib import Path
from tqdm import tqdm

# ---------------------------
# CONFIG - edit to taste
# ---------------------------
GLYPH_SIDE = 5  # square glyph side length
GLYPH_W = GLYPH_H = GLYPH_SIDE

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
]  # Kernels that MUSTN'T be present anywhere in the glyph, can be discarded early

WHITELISTED_KERNELS = [

]  # Kernels that MUST be present somewhere in the glyph, checked on leaf time at the very end

TEMPLATE = [
	[0, -1, -1, -1, 0],
	[0, -1, -1, -1, 0],
	[0, -1, -1, -1, 0],
	[0, -1, -1, -1, 0],
	[0, -1, -1, -1, 0],
]  # Template dictates what positions must be on (1), off (0), or don't care about (-1)


def template_to_forced_positions(template, w, h):
	"""
	Convert a full-size template (h rows of w cols) containing 1,0,-1 into
	two sets: forced_on_positions, forced_off_positions (linear indices).
	Requires template to exactly match glyph size.
	"""
	if template is None:
		return set(), set()
	if len(template) != h or any(len(row) != w for row in template):
		raise ValueError("TEMPLATE must be same size as glyph (rows x cols)")

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
	# -1 -> don't care, skip
	# sanity: no overlaps
	if forced_on & forced_off:
		raise ValueError("TEMPLATE contains conflicting forced 1 and 0 at same position(s)")
	return forced_on, forced_off


# Convert the TEMPLATE into forced index sets (you as the user only touch TEMPLATE)
FORCED_ON_POSITIONS, FORCED_OFF_POSITIONS = template_to_forced_positions(TEMPLATE, GLYPH_W, GLYPH_H)

# Optional boolean symmetry rule expression (uses names: horizontal, vertical, diag1, diag2, rot90, rot180).
# Leave empty to skip filtering at generation-time (we still compute and store booleans).
SYMMETRY_RULE = ""

OUT_DB = Path("dbs") / f"glyphs_{GLYPH_SIDE}_{GLYPH_SIDE}.db"
OUT_DB.parent.mkdir(parents=True, exist_ok=True)

VERBOSE = True


# ---------------------------


# helpers
def bits_to_bitstring(bits):
	return "".join("1" if b else "0" for b in bits)


def bitlist_to_grid(bits, w, h):
	return [[bits[r * w + c] for c in range(w)] for r in range(h)]


# transforms for square grids
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
	Returns dict of booleans:
	horizontal, vertical, diag1, diag2, rot90, rot180
	and integer score = sum of booleans (0..6)
	"""
	sym = {}
	sym["vertical"] = equal_grid(grid, reflect_vertical(grid))
	sym["horizontal"] = equal_grid(grid, reflect_horizontal(grid))
	try:
		sym["diag1"] = equal_grid(grid, reflect_main_diag(grid))
	except Exception:
		sym["diag1"] = False
	try:
		sym["diag2"] = equal_grid(grid, reflect_anti_diag(grid))
	except Exception:
		sym["diag2"] = False
	try:
		sym["rot90"] = equal_grid(grid, rotate90(grid))
	except Exception:
		sym["rot90"] = False
	try:
		sym["rot180"] = equal_grid(grid, rotate180(grid))
	except Exception:
		sym["rot180"] = False
	score = sum(1 for v in sym.values() if v)
	return sym, score


# connected components (4-neighbor)
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


# kernel matching
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


# safe boolean evaluator for SYMMETRY_RULE
class SafeBoolExprEvaluator(ast.NodeVisitor):
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
		else:
			raise ValueError(f"Unsupported expression node: {type(node)}")


def eval_symmetry_rule(rule_str, sym_map):
	if not rule_str:
		return True
	try:
		tree = ast.parse(rule_str, mode="eval")
		evaluator = SafeBoolExprEvaluator(sym_map)
		return evaluator.visit(tree)
	except Exception as e:
		raise ValueError(f"Invalid symmetry rule '{rule_str}': {e}")


# -----------------------
# generation
# -----------------------
def create_db(db_path):
	w = h = GLYPH_SIDE
	nbits = w * h

	blacklist_pre = [{"kernel": k, "anchors": precompute_kernel_anchors(w, h, k)} for k in BLACKLISTED_KERNELS]
	whitelist_pre = [{"kernel": k, "anchors": precompute_kernel_anchors(w, h, k)} for k in WHITELISTED_KERNELS]

	# use the sets created from TEMPLATE at the top of the file
	forced_on = set(FORCED_ON_POSITIONS)
	forced_off = set(FORCED_OFF_POSITIONS)

	# validate indices are in-range and there are no conflicts
	if any((p < 0 or p >= nbits) for p in forced_on | forced_off):
		raise ValueError("Some forced positions are out of range for this glyph size")
	if forced_on & forced_off:
		raise ValueError("Conflict: some positions are forced both ON and OFF: " + str(sorted(forced_on & forced_off)))

	conn = sqlite3.connect(str(db_path))
	cur = conn.cursor()

	# create table with only booleans + integer score
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
	cur.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
	cur.execute("DELETE FROM meta WHERE k IN ('w','h','nbits')")
	cur.execute("INSERT OR REPLACE INTO meta(k,v) VALUES (?,?)", ("w", str(w)))
	cur.execute("INSERT OR REPLACE INTO meta(k,v) VALUES (?,?)", ("h", str(h)))
	cur.execute("INSERT OR REPLACE INTO meta(k,v) VALUES (?,?)", ("nbits", str(nbits)))
	conn.commit()

	# precompute blacklist anchors mapped by max_idx for pruning
	blacklist_map_by_maxidx = {}
	for item in blacklist_pre:
		k = item["kernel"]
		for a in item["anchors"]:
			m = a["max_idx"]
			blacklist_map_by_maxidx.setdefault(m, []).append((k, a))

	# precompute whitelist map (not used for pruning by default)
	whitelist_map_by_maxidx = {}
	for item in whitelist_pre:
		k = item["kernel"]
		for a in item["anchors"]:
			m = a["max_idx"]
			whitelist_map_by_maxidx.setdefault(m, []).append((k, a))

	# initialize bits and pre-set forced-on positions
	bits = [0] * nbits
	for p in forced_on:
		bits[p] = 1

	# forced_off positions are already 0; they will be skipped in the recursion

	def anchor_matches_current(bits, kernel, anchor_info):
		ar, ac = anchor_info["anchor"]
		return kernel_matches_at(bits, w, h, kernel, ar, ac)

	insert_count = 0
	leaf_count = 0

	# ---- print status BEFORE creating bars so bars draw cleanly ----
	if VERBOSE:
		print(
			f"Starting generation for {w}x{h} glyphs ({nbits} bits), assignments={2 ** (nbits - len(forced_on) - len(forced_off))}")
		print(f"Output DB: {db_path}")
		print(f"Blacklisted kernels: {len(BLACKLISTED_KERNELS)}; Whitelisted kernels: {len(WHITELISTED_KERNELS)}")
		if SYMMETRY_RULE:
			print(f"Applying symmetry rule: {SYMMETRY_RULE}")

	# ---- create progress bar AFTER prints to avoid mixing output ----
	total_assignments = 2 ** (nbits - len(forced_on) - len(forced_off))
	assign_bar = tqdm(total=total_assignments, desc="Assignments", unit="assign")

	def backtrack(pos):
		nonlocal insert_count, leaf_count
		if pos == nbits:
			leaf_count += 1
			assign_bar.update(1)

			bs = bits_to_bitstring(bits)
			intrepr = int(bs, 2)
			grid = bitlist_to_grid(bits, w, h)

			# whitelist must be present if configured
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
			syms, score = compute_symmetries(grid)

			# symmetry rule filter (if set)
			if SYMMETRY_RULE:
				try:
					passes = eval_symmetry_rule(SYMMETRY_RULE, syms)
				except Exception as e:
					raise RuntimeError(f"Error evaluating symmetry rule: {e}")
				if not passes:
					return

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
			if cur.rowcount != 0:
				insert_count += 1
				# update postfix on the single progress bar to reflect inserted count
				assign_bar.set_postfix(inserted=insert_count)
			# occasional commit (no printing while bars are live)
			if VERBOSE and insert_count % 5000 == 0:
				conn.commit()
			return

		# forced-on: treat as set but still check pruning anchors
		if pos in forced_on:
			if pos in blacklist_map_by_maxidx:
				for k, a in blacklist_map_by_maxidx[pos]:
					if anchor_matches_current(bits, k, a):
						return
			backtrack(pos + 1)
			return

		# forced-off: treat as fixed zero; skip branching but still check pruning anchors
		if pos in forced_off:
			# ensure bit is zero (it should be by default)
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

		bits[pos] = 0

	backtrack(0)
	conn.commit()
	assign_bar.close()
	if VERBOSE:
		print(f"Generation complete. Inserted {insert_count} glyphs. Leaves visited: {leaf_count}")
	conn.close()


if __name__ == "__main__":
	create_db(OUT_DB)
