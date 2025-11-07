"""
render_random_grid_save_default.py

Render a random grid of glyphs (reconstructed from bitstring filenames in score buckets)
and by default save the upscaled image (each cell -> 20px) into:
  glyphs/5x5_filled_corners/renders/

Defaults:
  cols=5, rows=7, scores=1..6, scale=20 (px per cell), auto-save enabled, display enabled.

Usage examples:
  python render_random_grid_save_default.py
  python render_random_grid_save_default.py --cols 8 --rows 6 --scores 1,2,3 --seed 42
  python render_random_grid_save_default.py --no-display --out custom.png --scale 10
"""
import argparse
from pathlib import Path
import random
import numpy as np
from PIL import Image
import matplotlib.pyplot as plt
import time

# default base should match your generator output
DEFAULT_BASE = Path("./glyphs/5x5_from_db")


def collect_bitstrings(base_dir: Path, accepted_scores):
	"""Collect (score, bitstring) pairs from requested score buckets."""
	bs_list = []
	for score in accepted_scores:
		images_dir = base_dir / f"score_{score}" / "images"
		if not images_dir.exists():
			continue
		for p in images_dir.iterdir():
			if p.suffix.lower() == ".png":
				bs_list.append((score, p.stem))
	return bs_list


def bitstring_to_grid(bs: str):
	"""Convert 25-char bitstring to 5x5 numpy array (0/1)."""
	if len(bs) != 25:
		raise ValueError("bitstring length must be 25")
	arr = np.frombuffer(bs.encode('ascii'), dtype=np.uint8) - ord('0')
	return arr.reshape((5, 5))


def compose_canvas_cells(bitstrings, cols, rows):
	"""
	Compose a canvas in cell-units.
	- Outer border = 1 cell
	- Gap between glyphs = 1 cell
	- Glyph area = 5x5 cells
	Dimensions:
	  width_cells  = (5 + 1) * cols + 1
	  height_cells = (5 + 1) * rows + 1
	Returns a numpy uint8 array (H, W) with 255 = white, 0 = black.
	"""
	width_cells = (5 + 1) * cols + 1
	height_cells = (5 + 1) * rows + 1
	canvas = np.ones((height_cells, width_cells), dtype=np.uint8) * 255  # white background
	idx = 0
	for ry in range(rows):
		for rx in range(cols):
			bs = bitstrings[idx]
			grid = bitstring_to_grid(bs)
			# top-left cell coordinate of the glyph
			x0 = 1 + rx * (5 + 1)  # 1 + rx*6
			y0 = 1 + ry * (5 + 1)
			for r in range(5):
				for c in range(5):
					if grid[r, c]:
						canvas[y0 + r, x0 + c] = 0
			idx += 1
	return canvas


def parse_scores_argument(s: str):
	"""Parse comma-separated scores like '1,2,3' into a list of ints. Empty -> default [1..6]."""
	if s is None or s.strip() == "":
		return [1, 2, 3, 4, 5, 6]
	parts = [p.strip() for p in s.split(",") if p.strip() != ""]
	out = []
	for p in parts:
		try:
			v = int(p)
		except:
			raise argparse.ArgumentTypeError(f"Invalid score value: {p}")
		if v < 0 or v > 6:
			raise argparse.ArgumentTypeError(f"Score out of range 0..6: {v}")
		out.append(v)
	return sorted(set(out))


def ensure_renders_dir(base_dir: Path):
	renders = base_dir / "renders"
	renders.mkdir(parents=True, exist_ok=True)
	return renders


def make_filename(cols, rows, scores_list, seed):
	ts = int(time.time())
	scores_part = "-".join(str(s) for s in scores_list)
	seed_part = "none" if seed is None else str(seed)
	return f"render_{cols}x{rows}_scores{scores_part}_seed{seed_part}_{ts}.png"


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--base", type=str, default=str(DEFAULT_BASE),
					help="base folder with score_* buckets (default: ./glyphs/5x5_filled_corners)")
	ap.add_argument("--cols", type=int, default=20, help="number of glyphs across (X)")
	ap.add_argument("--rows", type=int, default=10, help="number of glyphs down (Y)")
	ap.add_argument("--scores", type=str, default="1,2,3,4,5,6",
					help="comma-separated accepted symmetry scores (e.g. '1,2,3') ; default 1..6")
	ap.add_argument("--seed", type=int, default=None, help="random seed")
	ap.add_argument("--scale", type=int, default=20, help="px per cell for saved upscaled image (default 20)")
	ap.add_argument("--allow_replacement", action="store_true", help="allow sampling with replacement")
	ap.add_argument("--no-display", action="store_true", help="do not pop up the image display")
	ap.add_argument("--out", type=str, default=None, help="optional custom output path (overrides default naming)")
	ap.add_argument("--save1x", action="store_true", help="also save the raw 1x cell PNG (exact pixels)")
	args = ap.parse_args()

	base = Path(args.base)
	if not base.exists():
		raise SystemExit(f"Base folder not found: {base.resolve()}")

	accepted_scores = parse_scores_argument(args.scores)
	pool = collect_bitstrings(base, accepted_scores)
	if not pool:
		raise SystemExit(f"No glyphs found in the requested score buckets: {accepted_scores}")

	bitpool = [bs for score, bs in pool]

	total_needed = args.cols * args.rows
	rng = random.Random(args.seed)

	if args.allow_replacement or len(bitpool) < total_needed:
		sampled = [rng.choice(bitpool) for _ in range(total_needed)]
	else:
		sampled = rng.sample(bitpool, total_needed)

	# compose the 1x (cell) canvas
	canvas_cells = compose_canvas_cells(sampled, args.cols, args.rows)  # dtype uint8

	# prepare renders output dir
	base_dir = Path(args.base)
	renders_dir = ensure_renders_dir(base_dir)

	# determine filename and paths
	if args.out:
		out_path = Path(args.out)
	else:
		fname = make_filename(args.cols, args.rows, accepted_scores, args.seed)
		out_path = renders_dir / fname

	# save 1x raw if requested
	if args.save1x:
		raw1x = out_path.with_name(out_path.stem + ".1x.png")
		Image.fromarray(canvas_cells).convert("L").save(raw1x)
		print("Saved 1x canvas to:", raw1x)

	# create upscaled preview (each cell -> scale x scale px)
	scale = int(args.scale)
	if scale <= 0:
		raise SystemExit("scale must be positive integer")
	pil_img = Image.fromarray(canvas_cells)  # mode 'L'
	up_w = canvas_cells.shape[1] * scale
	up_h = canvas_cells.shape[0] * scale
	up = pil_img.resize((up_w, up_h), resample=Image.NEAREST)

	# save upscaled preview (default behavior: save)
	up.convert("L").save(out_path)
	print("Saved upscaled render to:", out_path)

	# optionally display (nearest-neighbor)
	if not args.no_display:
		# display with axes filling the figure, no margins
		fig = plt.figure(figsize=(up_w / 100, up_h / 100), dpi=100, facecolor="black")
		ax = fig.add_axes([0, 0, 1, 1])
		ax.imshow(np.asarray(up), cmap="gray", vmin=0, vmax=255, interpolation="nearest")
		ax.set_axis_off()
		plt.show()


if __name__ == "__main__":
	main()
