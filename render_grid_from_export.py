#!/usr/bin/env python3
"""
render_grid_from_export.py

Read an export folder produced by generate_glyphs.py (metadata.csv) and render
a mosaic of randomly sampled glyphs with spacing done in *cell units*.

Spacing rules (cell units):
 - inner_margin: number of empty cells around each glyph when rendering it (default 0)
 - inter_gap: number of empty cells between glyph tiles (default 1)
 - outer_border: number of empty cells at the outer border around the whole mosaic (default 1)

A cell is converted to pixels by multiplying by `scale` (pixels per cell).

Usage:
    python render_grid_from_export.py /path/to/export_dir  --cols 10 --rows 10
"""
import sys
from pathlib import Path
import csv
import random
from math import ceil
from PIL import Image
from tqdm import tqdm
import argparse

# Defaults
DEFAULT_EXPORT_DIR = Path("foo")
DEFAULT_COLS = 10
DEFAULT_ROWS = 10
DEFAULT_SCALE = 20  # pixels per cell
DEFAULT_INNER_MARGIN = 0  # cells around each glyph
DEFAULT_INTER_GAP = 1  # cells between tiles
DEFAULT_OUTER_BORDER = 1  # cells around mosaic
DEFAULT_SEED = None
BG = (255, 255, 255)


def render_bitstring_to_image(bs: str, w: int, h: int, scale: int = 20, inner_margin: int = 0) -> Image.Image:
	"""
	Render a single glyph from bitstring using stored width/height.
	inner_margin is in cells (will be converted to pixels by scale).
	"""
	bits = [1 if ch == "1" else 0 for ch in bs]
	tile_cell_w = w + 2 * inner_margin
	tile_cell_h = h + 2 * inner_margin
	W_px = tile_cell_w * scale
	H_px = tile_cell_h * scale
	img = Image.new("RGB", (W_px, H_px), BG)
	px = img.load()
	# glyph origin inside tile in cells
	ox = inner_margin
	oy = inner_margin
	for r in range(h):
		for c in range(w):
			if bits[r * w + c]:
				# top-left pixel in pixels
				x0 = (ox + c) * scale
				y0 = (oy + r) * scale
				# fill a scale x scale block
				for dx in range(scale):
					for dy in range(scale):
						px[x0 + dx, y0 + dy] = (0, 0, 0)
	return img


def load_metadata(export_dir: Path):
	meta_file = export_dir / "metadata.csv"
	if not meta_file.exists():
		raise SystemExit(f"metadata.csv not found in {export_dir}. Run generator first.")
	rows = []
	with open(meta_file, newline="") as mf:
		rd = csv.DictReader(mf)
		if not rd.fieldnames:
			raise SystemExit("metadata.csv appears empty or malformed (no header).")
		for r in rd:
			nr = {k.lower(): v for k, v in r.items()}
			if "bitstring" not in nr:
				raise SystemExit("metadata.csv must contain 'bitstring' column")
			if "width" not in nr or "height" not in nr:
				raise SystemExit("metadata.csv must contain integer 'width' and 'height' columns")
			try:
				w = int(nr["width"])
				h = int(nr["height"])
			except Exception:
				raise SystemExit("width/height must be integers in metadata.csv")
			rows.append({
				"bitstring": nr["bitstring"],
				"width": w,
				"height": h,
				"filename": nr.get("filename") or nr.get("file") or ""
			})
	return rows


def build_mosaic(sample_rows, cols, scale, inner_margin, inter_gap, outer_border):
	"""
	Build mosaic from sample_rows (list of dicts with bitstring,width,height).
	Spacing parameters are in *cells*; convert to pixels with scale.
	Returns PIL.Image.
	"""
	n = len(sample_rows)
	rows_needed = ceil(n / cols)

	# Organize samples into matrix row-major with possibly last row partially filled
	grid = []
	it = iter(sample_rows)
	for r in range(rows_needed):
		row_list = []
		for c in range(cols):
			try:
				row_list.append(next(it))
			except StopIteration:
				row_list.append(None)
		grid.append(row_list)

	# compute tile cell sizes per glyph
	# tile_cell_width = glyph_width + 2*inner_margin
	# tile_cell_height = glyph_height + 2*inner_margin
	tile_cell_w_grid = [[(cell["width"] + 2 * inner_margin) if cell is not None else 0 for cell in row] for row in grid]
	tile_cell_h_grid = [[(cell["height"] + 2 * inner_margin) if cell is not None else 0 for cell in row] for row in
						grid]

	# per-column width (cells) is max over rows of that column
	col_cell_widths = []
	for c in range(cols):
		mw = 0
		for r in range(rows_needed):
			v = tile_cell_w_grid[r][c]
			if v > mw:
				mw = v
		col_cell_widths.append(mw if mw > 0 else 0)

	# per-row height (cells) is max over columns of that row
	row_cell_heights = []
	for r in range(rows_needed):
		mh = 0
		for c in range(cols):
			v = tile_cell_h_grid[r][c]
			if v > mh:
				mh = v
		row_cell_heights.append(mh if mh > 0 else 0)

	# convert to pixels
	col_px_widths = [w * scale for w in col_cell_widths]
	row_px_heights = [h * scale for h in row_cell_heights]
	gap_px = inter_gap * scale
	outer_px = outer_border * scale

	# canvas size in pixels
	canvas_w = outer_px * 2 + sum(col_px_widths) + gap_px * max(0, cols - 1)
	canvas_h = outer_px * 2 + sum(row_px_heights) + gap_px * max(0, rows_needed - 1)

	canvas = Image.new("RGB", (canvas_w, canvas_h), BG)

	# Pre-render images (with their inner_margin) so we can paste them centered in their cell
	pre_rendered = {}
	for info in sample_rows:
		key = (info["bitstring"], info["width"], info["height"])
		if key not in pre_rendered:
			pre_rendered[key] = render_bitstring_to_image(info["bitstring"], info["width"], info["height"],
														  scale=scale, inner_margin=inner_margin)

	# compute x offsets for each column and y offsets for each row
	col_x_offsets = []
	x = outer_px
	for idx, w_px in enumerate(col_px_widths):
		col_x_offsets.append(x)
		x += w_px + gap_px
	row_y_offsets = []
	y = outer_px
	for idx, h_px in enumerate(row_px_heights):
		row_y_offsets.append(y)
		y += h_px + gap_px

	# paste each glyph centered within its column cell width & row cell height
	for r in range(rows_needed):
		for c in range(cols):
			cell = grid[r][c]
			if cell is None:
				continue
			key = (cell["bitstring"], cell["width"], cell["height"])
			img = pre_rendered[key]
			tw, th = img.size
			cell_w_px = col_px_widths[c]
			cell_h_px = row_px_heights[r]
			x0 = col_x_offsets[c] + (cell_w_px - tw) // 2
			y0 = row_y_offsets[r] + (cell_h_px - th) // 2
			canvas.paste(img, (x0, y0))

	return canvas


def main():
	p = argparse.ArgumentParser(description="Render a mosaic of random glyphs from an export folder.")
	p.add_argument("export_dir", nargs="?", default=str(DEFAULT_EXPORT_DIR),
				   help="Export folder (contains metadata.csv)")
	p.add_argument("--cols", type=int, default=DEFAULT_COLS, help="Columns in mosaic (default: 10)")
	p.add_argument("--rows", type=int, default=DEFAULT_ROWS, help="Rows in mosaic (default: 10)")
	p.add_argument("--scale", type=int, default=DEFAULT_SCALE, help="Pixels per cell (default: 20)")
	p.add_argument("--inner-margin", type=int, default=DEFAULT_INNER_MARGIN,
				   help="Cells margin around each glyph (default 0)")
	p.add_argument("--inter-gap", type=int, default=DEFAULT_INTER_GAP, help="Cells gap between tiles (default 1)")
	p.add_argument("--outer-border", type=int, default=DEFAULT_OUTER_BORDER, help="Cells outer border (default 1)")
	p.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Random seed (optional)")
	p.add_argument("--outname", type=str, default=None, help="Optional output filename (without path)")
	args = p.parse_args()

	export_dir = Path(args.export_dir)
	if not export_dir.exists():
		raise SystemExit(f"Export folder not found: {export_dir}")

	# load metadata
	rows = load_metadata(export_dir)
	total_glyphs = len(rows)
	if total_glyphs == 0:
		raise SystemExit("No glyphs found in metadata.csv")

	cols = max(1, args.cols)
	rows_n = max(1, args.rows)
	want = cols * rows_n

	# sampling
	rng = random.Random(args.seed)
	if want <= total_glyphs:
		sampled = rng.sample(rows, want)
	else:
		print(f"Warning: requested {want} glyphs but only {total_glyphs} available. Sampling with replacement.")
		sampled = list(rows)
		while len(sampled) < want:
			sampled.append(rng.choice(rows))

	# build mosaic (spacing in cell units)
	mosaic = build_mosaic(sampled, cols=cols, scale=args.scale,
						  inner_margin=args.inner_margin, inter_gap=args.inter_gap, outer_border=args.outer_border)

	# save
	renders_dir = export_dir / "renders"
	renders_dir.mkdir(parents=True, exist_ok=True)
	if args.outname:
		out_fname = args.outname
	else:
		out_fname = f"grid_{cols}x{rows_n}_{want}glyphs.png"
	out_path = renders_dir / out_fname
	mosaic.save(str(out_path))
	print("Saved mosaic to:", out_path)


if __name__ == "__main__":
	main()
