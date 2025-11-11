#!/usr/bin/env python3
"""
streamlit_app.py

Glyph Studio (updated)

Key fixes / features in this version:
 - Label tab bug fixes (no stale records shown, delete button present,
   Next updates image immediately)
 - Template tab: Reset grid button, templates/blacklist/whitelist previews
 - Each glyph stores its own w,h in DB to avoid rendering size mismatches
 - Safe "New session (fresh DB file)" and "Clear all glyphs" controls (no experimental_rerun calls)
 - Gephi edges = Hamming distance 1
"""

import sqlite3
import tempfile
import os
import math
import random
import csv
import shutil
from pathlib import Path
from collections import deque
from typing import List, Tuple

import streamlit as st
from PIL import Image
import numpy as np
from tqdm import tqdm

# optional torch
try:
	import torch
	import torch.nn as nn
	import torch.optim as optim

	TORCH = True
except Exception:
	TORCH = False


# -----------------------
# DATABASE helpers
# -----------------------
def new_temp_db_path():
	# new unique temp DB file
	tf = tempfile.NamedTemporaryFile(prefix="glyphs_db_", suffix=".db", delete=False)
	tf.close()
	return tf.name


def get_session_db_path():
	# ensure one temp DB path per session (create if missing)
	if "db_path" not in st.session_state:
		st.session_state.db_path = new_temp_db_path()
		st.session_state.db_created_at = None
	return st.session_state.db_path


def get_conn():
	path = get_session_db_path()
	# allow multi-thread use by st (streamlit)
	return sqlite3.connect(path, check_same_thread=False)


def ensure_glyphs_schema(conn):
	# Ensure glyphs table exists and has the required columns (bitstring, int_repr, score, w, h)
	cur = conn.cursor()
	cur.execute("""
                CREATE TABLE IF NOT EXISTS glyphs
                (
                    id
                    INTEGER
                    PRIMARY
                    KEY,
                    bitstring
                    TEXT
                    UNIQUE,
                    int_repr
                    INTEGER,
                    score
                    REAL,
                    w
                    INTEGER,
                    h
                    INTEGER
                )
				""")
	conn.commit()
	# add any missing columns if older schema (defensive)
	cur.execute("PRAGMA table_info(glyphs)")
	existing = [r[1] for r in cur.fetchall()]
	adds = []
	if "score" not in existing:
		adds.append(("score", "REAL"))
	if "w" not in existing:
		adds.append(("w", "INTEGER"))
	if "h" not in existing:
		adds.append(("h", "INTEGER"))
	for name, typ in adds:
		try:
			cur.execute(f"ALTER TABLE glyphs ADD COLUMN {name} {typ}")
		except Exception:
			pass
	conn.commit()


def init_db():
	conn = get_conn()
	ensure_glyphs_schema(conn)
	# meta table
	cur = conn.cursor()
	cur.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
	conn.commit()
	conn.close()


def clear_db_rows():
	conn = get_conn()
	cur = conn.cursor()
	cur.execute("DELETE FROM glyphs")
	cur.execute("DELETE FROM meta")
	conn.commit()
	conn.close()


def new_session_db():
	# Remove existing temp file (if any), then create a fresh temp DB and set path in session_state
	if "db_path" in st.session_state:
		try:
			p = st.session_state["db_path"]
			if p and os.path.exists(p):
				os.remove(p)
		except Exception:
			pass
	st.session_state.db_path = new_temp_db_path()
	init_db()


def insert_bitstring(bitstring: str, w: int, h: int, default_score: float = 0.5):
	intrepr = int(bitstring, 2)
	conn = get_conn()
	ensure_glyphs_schema(conn)
	cur = conn.cursor()
	cur.execute("INSERT OR IGNORE INTO glyphs (bitstring,int_repr,score,w,h) VALUES (?,?,?,?,?)",
				(bitstring, intrepr, float(default_score), int(w), int(h)))
	conn.commit()
	conn.close()


def fetch_random_any():
	conn = get_conn()
	cur = conn.cursor()
	cur.execute("SELECT bitstring, score, w, h FROM glyphs ORDER BY RANDOM() LIMIT 1")
	r = cur.fetchone()
	conn.close()
	return r  # tuple or None


def update_score(bitstring: str, score: float):
	conn = get_conn()
	cur = conn.cursor()
	cur.execute("UPDATE glyphs SET score=? WHERE bitstring=?", (float(score), bitstring))
	conn.commit()
	conn.close()


def delete_glyph(bitstring: str):
	conn = get_conn()
	cur = conn.cursor()
	cur.execute("DELETE FROM glyphs WHERE bitstring=?", (bitstring,))
	conn.commit()
	conn.close()


def fetch_all_glyphs():
	conn = get_conn()
	cur = conn.cursor()
	cur.execute("SELECT id, bitstring, int_repr, score, w, h FROM glyphs ORDER BY id")
	rows = cur.fetchall()
	conn.close()
	return rows


def count_glyphs() -> int:
	conn = get_conn()
	cur = conn.cursor()
	cur.execute("SELECT COUNT(*) FROM glyphs")
	n = cur.fetchone()[0]
	conn.close()
	return n


# -----------------------
# kernel & matching helpers
# -----------------------
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


# -----------------------
# generation backtrack
# -----------------------
def generate_from_template(template: List[List[int]],
						   blacklist_kernels: List[List[List[int]]],
						   whitelist_kernels: List[List[List[int]]],
						   max_results: int = None,
						   progress_callback=None,
						   expected_total=None) -> Tuple[int, int]:
	h = len(template)
	w = len(template[0])
	nbits = w * h

	# forced sets
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

	blacklist_pre = [{"kernel": k, "anchors": precompute_kernel_anchors(w, h, k)} for k in blacklist_kernels]
	whitelist_pre = [{"kernel": k, "anchors": precompute_kernel_anchors(w, h, k)} for k in whitelist_kernels]

	blacklist_map_by_maxidx = {}
	for item in blacklist_pre:
		k = item["kernel"]
		for a in item["anchors"]:
			m = a["max_idx"]
			blacklist_map_by_maxidx.setdefault(m, []).append((k, a))

	def anchor_matches_current(bits, kernel, anchor_info):
		ar, ac = anchor_info["anchor"]
		return kernel_matches_at(bits, w, h, kernel, ar, ac)

	bits = [0] * nbits
	for p in forced_on:
		bits[p] = 1

	inserted = 0
	leaves = 0

	def backtrack(pos):
		nonlocal inserted, leaves
		if max_results is not None and inserted >= max_results:
			return
		if pos == nbits:
			leaves += 1
			if whitelist_kernels:
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
			bs = "".join("1" if b else "0" for b in bits)
			insert_bitstring(bs, w, h, default_score=0.5)
			inserted += 1
			if progress_callback:
				progress_callback(inserted, expected_total)
			return

		# forced-on
		if pos in forced_on:
			if pos in blacklist_map_by_maxidx:
				for k, a in blacklist_map_by_maxidx[pos]:
					if anchor_matches_current(bits, k, a):
						return
			backtrack(pos + 1)
			return

		# forced-off
		if pos in forced_off:
			bits[pos] = 0
			if pos in blacklist_map_by_maxidx:
				for k, a in blacklist_map_by_maxidx[pos]:
					if anchor_matches_current(bits, k, a):
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

		bits[pos] = 0

	backtrack(0)
	return inserted, leaves


# -----------------------
# image helpers (with -1 rendering)
# -----------------------
def render_bitstring_to_image(bitstring: str, w: int, h: int, scale: int = 20, margin: int = 1) -> Image.Image:
	# Validate length
	if len(bitstring) != w * h:
		# defensive: if mismatch, fallback to square inference
		L = len(bitstring)
		side = int(math.sqrt(L))
		if side * side == L:
			w = h = side
		else:
			# try to resize to given w,h by padding/truncating (shouldn't be typical)
			bitstring = bitstring.ljust(w * h, "0")[:w * h]
	bits = [1 if ch == "1" else 0 for ch in bitstring]
	img_w = (w + 2 * margin) * scale
	img_h = (h + 2 * margin) * scale
	img = Image.new("RGB", (img_w, img_h), (255, 255, 255))
	px = img.load()
	for r in range(h):
		for c in range(w):
			if bits[r * w + c]:
				x0 = (c + margin) * scale
				y0 = (r + margin) * scale
				for dx in range(scale):
					for dy in range(scale):
						px[x0 + dx, y0 + dy] = (0, 0, 0)
	return img


def render_small_preview_from_grid(grid: List[List[int]], scale=10, margin=0) -> Image.Image:
	h = len(grid);
	w = len(grid[0])
	img_w = (w + 2 * margin) * scale
	img_h = (h + 2 * margin) * scale
	img = Image.new("RGB", (img_w, img_h), (255, 255, 255))
	px = img.load()
	for r in range(h):
		for c in range(w):
			val = grid[r][c]
			x0 = (c + margin) * scale
			y0 = (r + margin) * scale
			# red for -1 (don't care), black for 1, white for 0
			color = (255, 120, 120) if val == -1 else ((0, 0, 0) if val == 1 else (255, 255, 255))
			for dx in range(scale):
				for dy in range(scale):
					px[x0 + dx, y0 + dy] = color
	return img


# -----------------------
# Streamlit UI
# -----------------------
st.set_page_config(layout="wide", page_title="Glyph Studio (fixed)", initial_sidebar_state="expanded")
st.title("Glyph Studio — fixes & improvements")

# init session state defaults
if "initialized" not in st.session_state:
	# create fresh DB on first load
	get_session_db_path()
	init_db()
	st.session_state.initialized = True

if "template" not in st.session_state:
	st.session_state.template = [[-1] * 5 for _ in range(5)]
if "templates" not in st.session_state:
	st.session_state.templates = []  # list of templates (each is grid)
if "templates_selected_idx" not in st.session_state:
	st.session_state.templates_selected_idx = None
if "blacklist" not in st.session_state:
	st.session_state.blacklist = []
if "whitelist" not in st.session_state:
	st.session_state.whitelist = []
if "current_label_bs" not in st.session_state:
	st.session_state.current_label_bs = None
if "current_label_score" not in st.session_state:
	st.session_state.current_label_score = 0.5


# safe session reset (no experimental rerun)
def safe_reset_session_dbfile():
	# create a fresh DB file and reinit
	new_session_db()
	st.success("New session DB created. UI will re-run automatically on next interaction.")
	st.stop()


# Sidebar controls
with st.sidebar:
	st.header("Session")
	col1, col2 = st.columns([1, 1])
	if col1.button("Clear all glyphs (keep DB file)"):
		clear_db_rows()
		st.success("Cleared glyph rows. DB file preserved.")
		st.stop()
	if col2.button("New session (fresh DB file)"):
		safe_reset_session_dbfile()
	st.write("Glyph count:", count_glyphs())
	st.write("DB file:", get_session_db_path())

tabs = st.tabs(["Template", "Generate", "Label", "Train", "Export"])

# -----------------------
# Template tab
# -----------------------
with tabs[0]:
	st.header("Template editor")

	# size controls at top-right
	size_col, grid_col = st.columns([1, 3])
	with size_col:
		W = st.number_input("Width", min_value=1, max_value=64, value=len(st.session_state.template[0]),
							key="width_input")
		H = st.number_input("Height", min_value=1, max_value=64, value=len(st.session_state.template),
							key="height_input")
		if st.button("Reset grid to all -1"):
			st.session_state.template = [[-1] * W for _ in range(H)]
			st.success("Working grid reset to don't-care (-1).")
			st.stop()

	with grid_col:
		# ensure resize reflects in working template
		grid = st.session_state.template
		if len(grid) != H or len(grid[0]) != W:
			new = [[-1] * W for _ in range(H)]
			for r in range(min(len(grid), H)):
				for c in range(min(len(grid[0]), W)):
					new[r][c] = grid[r][c]
			st.session_state.template = new
			grid = new

		st.write("Click cells to cycle: 🟥 (-1) → ⬛ (1) → ⬜ (0)")


		def toggle_cell(r, c):
			g = st.session_state.template
			v = g[r][c]
			if v == -1:
				g[r][c] = 1
			elif v == 1:
				g[r][c] = 0
			else:
				g[r][c] = -1
			st.session_state.template = g


		# render grid as buttons (one-row at a time)
		for r in range(len(grid)):
			cols = st.columns(len(grid[0]))
			for c in range(len(grid[0])):
				val = grid[r][c]
				label = "🟥" if val == -1 else ("⬛" if val == 1 else "⬜")
				cols[c].button(label, key=f"cell_{r}_{c}", on_click=toggle_cell, args=(r, c))

		st.markdown("**Working grid (rows)**")
		st.code("\n".join(" ".join(str(x) for x in row) for row in st.session_state.template))
		try:
			st.image(render_small_preview_from_grid(st.session_state.template, scale=12, margin=1),
					 caption="Working grid preview", width=220)
		except Exception:
			pass

	# bottom: three columns for Templates / Blacklist / Whitelist
	st.markdown("---")
	c1, c2, c3 = st.columns(3)
	with c1:
		st.subheader("Templates")
		if st.button("Add current grid to Templates"):
			st.session_state.templates.append([row[:] for row in st.session_state.template])
			st.success("Appended template.")
		if st.button("Clear all Templates"):
			st.session_state.templates = []
			st.success("Cleared templates.")
		if st.session_state.templates:
			labels = [f"Template #{i}" for i in range(len(st.session_state.templates))]
			sel_idx = st.radio("Select template", options=list(range(len(st.session_state.templates))),
							   index=st.session_state.templates_selected_idx or 0,
							   format_func=lambda i: labels[i])
			st.session_state.templates_selected_idx = sel_idx
			st.image(render_small_preview_from_grid(st.session_state.templates[sel_idx], scale=12, margin=1), width=180)
			if st.button("Use selected as current working grid"):
				st.session_state.template = [row[:] for row in st.session_state.templates[sel_idx]]
				st.success("Copied template into working grid.")
			# removal controls
			for idx in range(len(st.session_state.templates)):
				if st.button(f"Remove template #{idx}", key=f"rem_tpl_{idx}"):
					st.session_state.templates.pop(idx)
					st.success("Removed template.")
					break
		else:
			st.write("(none)")

	with c2:
		st.subheader("Blacklist kernels")
		if st.button("Add current grid to Blacklist"):
			st.session_state.blacklist.append([row[:] for row in st.session_state.template])
			st.success("Added blacklist kernel.")
		if st.button("Clear blacklist"):
			st.session_state.blacklist = []
			st.success("Blacklist cleared.")
		if st.session_state.blacklist:
			for idx, k in enumerate(st.session_state.blacklist):
				st.image(render_small_preview_from_grid(k, scale=10, margin=0), width=80)
				if st.button(f"Remove blacklist #{idx}", key=f"rem_blk_{idx}"):
					st.session_state.blacklist.pop(idx)
					st.success("Removed blacklist kernel.")
					break
		else:
			st.write("(none)")

	with c3:
		st.subheader("Whitelist kernels")
		if st.button("Add current grid to Whitelist"):
			st.session_state.whitelist.append([row[:] for row in st.session_state.template])
			st.success("Added whitelist kernel.")
		if st.button("Clear whitelist"):
			st.session_state.whitelist = []
			st.success("Whitelist cleared.")
		if st.session_state.whitelist:
			for idx, k in enumerate(st.session_state.whitelist):
				st.image(render_small_preview_from_grid(k, scale=10, margin=0), width=80)
				if st.button(f"Remove whitelist #{idx}", key=f"rem_wl_{idx}"):
					st.session_state.whitelist.pop(idx)
					st.success("Removed whitelist kernel.")
					break
		else:
			st.write("(none)")

# -----------------------
# Generate tab
# -----------------------
with tabs[1]:
	st.header("Generate glyphs from template")
	st.write("Choose an active template (Templates tab) or use working grid below.")

	max_results_input = st.number_input("Max insertions (0 = exhaust all possibilities)", min_value=0, value=0)
	max_results = None if max_results_input == 0 else int(max_results_input)

	# determine which template to use
	if st.session_state.templates and st.session_state.templates_selected_idx is not None:
		active_template = st.session_state.templates[st.session_state.templates_selected_idx]
	else:
		active_template = st.session_state.template

	st.write("Active template preview:")
	st.image(render_small_preview_from_grid(active_template, scale=12, margin=1), width=220)

	if st.button("Generate (clear DB rows first)"):
		clear_db_rows()
		init_db()
		st.info("Generation starting...")
		# expected combos if small
		flex = sum(1 for r in active_template for v in r if v == -1)
		expected = None
		if flex <= 24:
			expected = 2 ** flex

		progress = st.progress(0)
		status = st.empty()


		def progress_cb(count, expected_total):
			if expected_total:
				progress.progress(min(1.0, count / expected_total))
				status.text(f"Inserted {count} / {expected_total}")
			else:
				status.text(f"Inserted {count}")


		inserted, leaves = generate_from_template(active_template,
												  st.session_state.blacklist,
												  st.session_state.whitelist,
												  max_results=max_results,
												  progress_callback=progress_cb,
												  expected_total=expected)
		progress.progress(1.0)
		status.text(f"Generation complete — inserted {inserted}, leaves visited {leaves}")
		st.success("Generation finished.")
		st.write("Glyphs in DB:", count_glyphs())
		st.stop()

	st.write("Current glyph count:", count_glyphs())

# -----------------------
# Label tab
# -----------------------
with tabs[2]:
	st.header("Label / score glyphs (single preview)")

	if count_glyphs() == 0:
		st.warning("No glyphs in DB — generate them first (Template → Generate).")
	else:
		# ensure a current candidate is present
		if st.session_state.current_label_bs is None:
			row = fetch_random_any()
			if row is not None:
				st.session_state.current_label_bs = row[0]
				st.session_state.current_label_score = float(row[1]) if row[1] is not None else 0.5
				# store slider value too
				st.session_state.label_slider = st.session_state.current_label_score

		# fetch fresh row to ensure using DB for width/height
		cur_row = None
		if st.session_state.current_label_bs:
			allrows = fetch_all_glyphs()
			lookup = {r[1]: r for r in allrows}  # bitstring -> row
			if st.session_state.current_label_bs in lookup:
				# (id, bitstring, int_repr, score, w, h)
				cur_row = lookup[st.session_state.current_label_bs]

		if cur_row is None:
			row = fetch_random_any()
			if row is not None:
				st.session_state.current_label_bs = row[0]
				st.session_state.current_label_score = float(row[1]) if row[1] is not None else 0.5
				st.session_state.label_slider = st.session_state.current_label_score
				cur_row = (None, row[0], None, row[1], row[2], row[3])

		if cur_row is None:
			st.error("Could not obtain a glyph from DB.")
		else:
			_gid, bs, _intrepr, scr, w, h = cur_row
			if w is None or h is None:
				# fallback to working grid dims
				w = len(st.session_state.template[0])
				h = len(st.session_state.template)
			score_val = float(scr) if scr is not None else st.session_state.current_label_score

			img = render_bitstring_to_image(bs, int(w), int(h), scale=18, margin=1)
			st.image(img, caption=f"{bs} — current DB score: {score_val:.3f}")

			# use slider keyed by current bitstring so it updates correctly on Next
			slider_key = f"label_slider_{bs}"
			if slider_key not in st.session_state:
				st.session_state[slider_key] = score_val
			val = st.slider("Assign score 0.01..0.99 (change then press Save)", min_value=0.01, max_value=0.99,
							value=st.session_state[slider_key], step=0.01, key=slider_key)
			st.session_state[slider_key] = val
			st.session_state.current_label_score = val

			col1, col2 = st.columns(2)
			with col1:
				if st.button("Save score for current"):
					update_score(bs, st.session_state.current_label_score)
					st.success(f"Saved {st.session_state.current_label_score:.3f} for {bs}")
			with col2:
				if st.button("Next (random)"):
					row = fetch_random_any()
					if row is not None:
						st.session_state.current_label_bs = row[0]
						# set session slider for new bs so slider updates immediately on rerun
						newscore = float(row[1]) if row[1] is not None else 0.5
						st.session_state[f"label_slider_{row[0]}"] = newscore
						st.session_state.current_label_score = newscore
						st.experimental_rerun = None  # noop placeholder (we don't call experimental_rerun)
						st.stop()  # stop so UI reruns and shows new glyph immediately

			st.markdown("### Recently labeled (most recent 20 rows, newest last)")
			allrows = fetch_all_glyphs()
			for gid, bstr, _, scr, gw, gh in list(reversed(allrows))[:20]:
				rcols = st.columns([1, 4, 1])
				try:
					img = render_bitstring_to_image(bstr, int(gw), int(gh), scale=12, margin=1)
					rcols[0].image(img, width=56)
				except Exception:
					rcols[0].write("imgerr")
				rcols[1].write(f"{bstr} — score={'' if scr is None else f'{scr:.3f}'}")
				if rcols[2].button("Delete", key=f"del_{gid}"):
					delete_glyph(bstr)
					st.success("Deleted glyph from DB.")
					st.stop()

# -----------------------
# Train tab (kept minimal for now)
# -----------------------
with tabs[3]:
	st.header("Train small MLP & predict (optional)")
	all_rows = fetch_all_glyphs()
	labeled = [r for r in all_rows if r[3] is not None]
	st.write(f"Total glyphs: {len(all_rows)}; labeled: {len(labeled)}")

	st.write("This tab is intentionally minimal in this release. Use the Label tab to assign scores.")
	st.write("If you want I can add a full training UI next (MLP, EMA, plot training curves).")

# -----------------------
# Export tab
# -----------------------
with tabs[4]:
	st.header("Export images + optional Gephi CSVs")
	outdir = st.text_input("Export folder", value="glyphs_export")
	use_scores = st.checkbox("Bucket by score", value=True)
	n_buckets = st.number_input("Number of buckets", min_value=1, max_value=50, value=10)
	export_gephi = st.checkbox("Export Gephi CSVs (nodes + edges)", value=False)
	if st.button("Export now"):
		OUT = Path(outdir)
		if OUT.exists():
			shutil.rmtree(OUT)
		OUT.mkdir(parents=True, exist_ok=True)
		rows = fetch_all_glyphs()
		if not rows:
			st.error("No glyphs to export.")
		else:
			# compute bucket ranges if requested
			scores = [float(r[3]) if r[3] is not None else 0.5 for r in rows]
			if use_scores:
				min_s = min(scores);
				max_s = max(scores)
				if math.isclose(min_s, max_s):
					bins = [(min_s, max_s)]
				else:
					width = (max_s - min_s) / n_buckets
					bins = [(min_s + i * width, min_s + (i + 1) * width) for i in range(n_buckets)]
			else:
				bins = [(0.0, 1.0)]

			# master csv
			master_csv = OUT / "metadata.csv"
			with open(master_csv, "w", newline="") as mf:
				wr = csv.writer(mf)
				wr.writerow(
					["bitstring", "int_repr", "filled", "filled_ratio", "components", "filename", "score", "bucket"])
				N = len(rows)
				progress = st.progress(0)
				for idx, (gid, bs, intrepr, scr, w, h) in enumerate(rows):
					score = float(scr) if scr is not None else 0.5
					# validate w/h
					if w is None or h is None:
						w = len(st.session_state.template[0])
						h = len(st.session_state.template)
					bits = [1 if ch == '1' else 0 for ch in bs]
					filled = sum(bits);
					filled_ratio = filled / (w * h)
					# components
					grid = [[bits[r * w + c] for c in range(w)] for r in range(h)]
					visited = [[False] * w for _ in range(h)]
					dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
					comp = 0
					for rr in range(h):
						for cc in range(w):
							if grid[rr][cc] and not visited[rr][cc]:
								comp += 1
								dq = deque([(rr, cc)])
								visited[rr][cc] = True
								while dq:
									x, y = dq.popleft()
									for dx, dy in dirs:
										nx, ny = x + dx, y + dy
										if 0 <= nx < h and 0 <= ny < w and not visited[nx][ny] and grid[nx][ny]:
											visited[nx][ny] = True
											dq.append((nx, ny))
					# bucket
					if len(bins) == 1:
						b_idx = 0
					else:
						if math.isclose(min_s, max_s):
							b_idx = 0
						else:
							b_idx = int((score - min_s) / ((max_s - min_s) / len(bins)))
							if b_idx < 0: b_idx = 0
							if b_idx >= len(bins): b_idx = len(bins) - 1

					# write image
					if use_scores:
						img_dir = OUT / f"bucket_{b_idx}"
					else:
						img_dir = OUT / "glyphs"
					img_dir.mkdir(parents=True, exist_ok=True)
					img = render_bitstring_to_image(bs, int(w), int(h), scale=18, margin=1)
					fname = f"{bs}.png"
					img.save(img_dir / fname)
					wr.writerow(
						[bs, intrepr, filled, filled_ratio, comp, str((img_dir / fname).relative_to(OUT)), score,
						 b_idx])
					progress.progress((idx + 1) / N)
			st.success(f"Exported {len(rows)} glyphs to {OUT}, master CSV: {master_csv}")

			# Gephi export (edges by Hamming distance 1)
			if export_gephi:
				st.info("Exporting gephi nodes+edges (Hamming distance == 1)")
				nodes_path = OUT / "nodes.csv"
				edges_path = OUT / "edges.csv"
				all_bits = [r[1] for r in rows]
				bitset = set(all_bits)
				with open(nodes_path, "w", newline="") as nf:
					nw = csv.writer(nf)
					nw.writerow(["Id", "Label", "score"])
					for (_id, bs, _int, scr, _w, _h) in rows:
						nw.writerow([bs, bs, "" if scr is None else f"{scr:.6g}"])
				nbits = len(all_bits[0])
				with open(edges_path, "w", newline="") as ef:
					ew = csv.writer(ef)
					ew.writerow(["Source", "Target", "Weight"])
					progress = st.progress(0)
					total = len(all_bits)
					for idx, bs in enumerate(all_bits):
						bs_list = list(bs)
						for i in range(nbits):
							orig = bs_list[i]
							bs_list[i] = "0" if orig == "1" else "1"
							neighbor = "".join(bs_list)
							bs_list[i] = orig
							if neighbor in bitset and bs < neighbor:
								ew.writerow([bs, neighbor, 1])
						if idx % 100 == 0:
							progress.progress((idx + 1) / total)
				st.success(f"Gephi CSVs written to {OUT}")

st.write("Session DB path:", get_session_db_path())
