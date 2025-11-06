# prepare_glyph_dataloader.py
# Minimal, ready-to-run script that:
#  - reads glyphs/glyphs_no_blank2x2/metadata.csv (expects column "bitstring25")
#  - converts bitstring25 -> torch.FloatTensor shape [N,25]
#  - saves glyphs_tensor.pt beside the CSV for fast reload
#  - provides a DataLoader over the tensor (batches of [B,25])
#
# Usage:
#   python prepare_glyph_dataloader.py
# The script hardcodes the CSV path (you asked for that). If you want to change it,
# edit METADATA_CSV_PATH below.
import copy
import csv
from pathlib import Path
import torch
from torch.utils.data import Dataset, DataLoader, TensorDataset

METADATA_CSV_PATH = Path("glyphs/glyphs_no_blank2x2/metadata.csv")  # <-- hardcoded path you requested
OUT_TENSOR_PATH = METADATA_CSV_PATH.parent / "glyphs_tensor.pt"  # saved tensor location
BATCH_SIZE = 128
SHUFFLE = True
NUM_WORKERS = 0  # change if you want multiprocessing dataloading


def load_bitstrings_tensor(csv_path: Path, bitcol: str = "bitstring25") -> torch.FloatTensor:
	"""
	Read CSV and return a FloatTensor of shape [N,25] with 0.0/1.0 values.
	Raises if CSV missing or any bitstring length != 25.
	"""
	if not csv_path.exists():
		raise FileNotFoundError(f"CSV not found: {csv_path.resolve()}")

	rows = []
	with csv_path.open(newline="") as cf:
		reader = csv.DictReader(cf)
		if bitcol not in reader.fieldnames:
			raise ValueError(f"CSV is missing required column '{bitcol}'. Found columns: {reader.fieldnames}")
		for r in reader:
			b = r[bitcol].strip()
			if len(b) != 25:
				raise ValueError(f"Bad bitstring length {len(b)} for row: {r}")
			rows.append(b)

	N = len(rows)
	if N == 0:
		raise ValueError("CSV contained no rows.")
	# numpy could be used, but direct torch construction is simple and fast for this size
	tensor = torch.empty((N, 25), dtype=torch.float32)
	for i, b in enumerate(rows):
		# faster than list comprehension for small fixed length
		vals = [1.0 if ch == "1" else 0.0 for ch in b]
		tensor[i, :] = torch.tensor(vals, dtype=torch.float32)
	return tensor


def get_dataloader_from_tensor(tensor: torch.FloatTensor, batch_size: int = BATCH_SIZE,
							   shuffle: bool = SHUFFLE, num_workers: int = NUM_WORKERS) -> DataLoader:
	"""
	Create a PyTorch DataLoader that yields batches shaped [B,25] (FloatTensor).
	"""
	ds = TensorDataset(tensor)  # yields tuples (tensor[i],) — we'll unpack in training
	dl = DataLoader(ds, batch_size=batch_size, shuffle=shuffle, num_workers=num_workers, drop_last=False)
	return dl


print("Loading bitstrings from:", METADATA_CSV_PATH)
tensor = load_bitstrings_tensor(METADATA_CSV_PATH)
print("Loaded tensor shape:", tuple(tensor.shape))  # (N,25)

# Save tensor for fast future loads
print("Saving tensor to:", OUT_TENSOR_PATH)
torch.save(tensor, OUT_TENSOR_PATH)

# # Build DataLoader and show one example batch
dataloader = get_dataloader_from_tensor(tensor)
# print("Created DataLoader. Example batch (first):")
# for batch in dataloader:
# 	# batch is a tuple because we used TensorDataset; extract the single tensor
# 	x = batch[0]  # shape [B,25]
# 	print(" - batch tensor shape:", tuple(x.shape))
# 	print(" - dtype:", x.dtype)
# 	# print a small sample
# 	print(" - first row (25 values):", x[0].tolist())
# 	break

from torch import nn
from tqdm import tqdm
import matplotlib.pyplot as plt

torch.manual_seed(0)
torch.cuda.manual_seed_all(0)
device = "cuda" if torch.cuda.is_available() else "cpu"

hidden_features = 256
dropout_prob = 0.0
vector_size = 5

encoder = nn.Sequential(
	nn.Linear(25, hidden_features),
	nn.SiLU(),
	nn.Dropout(dropout_prob),
	nn.Linear(hidden_features, hidden_features),
	nn.SiLU(),
	nn.Dropout(dropout_prob),
	nn.Linear(hidden_features, hidden_features),
	nn.SiLU(),
	nn.Dropout(dropout_prob),
	nn.Linear(hidden_features, vector_size),
	nn.LayerNorm(vector_size)
).to(device)

decoder = nn.Sequential(
	nn.Linear(vector_size, hidden_features),
	nn.SiLU(),
	nn.Dropout(dropout_prob),
	nn.Linear(hidden_features, hidden_features),
	nn.SiLU(),
	nn.Dropout(dropout_prob),
	nn.Linear(hidden_features, hidden_features),
	nn.SiLU(),
	nn.Dropout(dropout_prob),
	nn.Linear(hidden_features, 25),
	nn.Sigmoid()
).to(device)

import copy

ema_encoder = copy.deepcopy(encoder)
ema_decoder = copy.deepcopy(decoder)
ema_encoder.eval()
ema_decoder.eval()


@torch.no_grad()
def update_ema_model(model, ema_model, decay=0.995):
	for param, ema_param in zip(model.parameters(), ema_model.parameters()):
		ema_param.data.mul_(decay).add_(param.data, alpha=1 - decay)


encoder_optim = torch.optim.AdamW(encoder.parameters(), 1e-3)
decoder_optim = torch.optim.AdamW(decoder.parameters(), 1e-3)

num_epochs = 50
raw_losses = []
ema_losses = []

for epoch in tqdm(range(num_epochs), total=num_epochs, desc="TRAIN"):
	encoder.train()
	decoder.train()
	for batch in dataloader:
		glyph = batch[0].to(device)
		b, f = glyph.shape
		if b != BATCH_SIZE:
			continue

		encoder.zero_grad()
		decoder.zero_grad()

		glyph_vector = encoder(glyph)
		reconstructed = decoder(glyph_vector)

		loss = nn.functional.mse_loss(reconstructed, glyph, reduction="sum")
		raw_losses.append(loss.item())
		loss.backward()
		encoder_optim.step()
		decoder_optim.step()

		update_ema_model(encoder, ema_encoder)
		update_ema_model(decoder, ema_decoder)

		with torch.no_grad():
			glyph_vector = ema_encoder(glyph)
			reconstructed = ema_decoder(glyph_vector)
			loss = nn.functional.mse_loss(reconstructed, glyph, reduction="sum")
			ema_losses.append(loss.item())

	plt.title(f"Loss - E{epoch}")
	plt.plot(raw_losses, label="Raw loss")
	plt.plot(ema_losses, label="EMA loss")
	plt.legend()
	plt.show()

# loss_ema = 0.0
# loss_decay = 0.99
# for loss in ema_losses:
# 	loss_ema = loss_ema * loss_decay + loss * (1 - loss_decay)
# print(f"Final Loss: {loss_ema}")


import torch
from torch import nn
import matplotlib.pyplot as plt


def visualize_recon_batch(
		glyph: torch.Tensor,
		reconstructed_glyph: torch.Tensor,
		max_images: int = 8,
		cmap: str = "gray_r",
		show: bool = True,
		save_prefix: str | None = None,
):
	"""
	Render up to `max_images` individual comparison plots (original vs reconstruction).

	Args:
		glyph: [b, 25] tensor of originals
		reconstructed_glyph: [b, 25] tensor of reconstructions
		max_images: maximum number of samples to render
		cmap: matplotlib colormap
		show: whether to call plt.show()
		save_prefix: if not None, saves figures as f"{save_prefix}_{i}.png"
	"""
	if glyph.dim() != 2 or glyph.size(1) != 25:
		raise ValueError("glyph must be shape [b,25]")
	if reconstructed_glyph.shape != glyph.shape:
		raise ValueError("reconstructed_glyph must have same shape as glyph")

	# move to CPU and detach
	g = glyph.detach().cpu()
	r = reconstructed_glyph.detach().cpu()

	b = g.size(0)
	n = min(b, max_images)

	with torch.no_grad():
		diffs = nn.functional.mse_loss(r, g, reduction="none")  # [b,25]
		mse_per_elem = diffs.sum(dim=1)  # [b]

	for i in range(n):
		orig = g[i].reshape(5, 5).numpy()
		recon = r[i].reshape(5, 5).numpy()
		mse_val = float(mse_per_elem[i].item())

		fig, axes = plt.subplots(1, 2, figsize=(5, 2.5))

		# original
		axes[0].imshow(orig, cmap=cmap, interpolation="nearest", vmin=0.0, vmax=1.0)
		axes[0].set_title("Original")
		axes[0].axis("off")

		# reconstruction
		axes[1].imshow(recon, cmap=cmap, interpolation="nearest", vmin=0.0, vmax=1.0)
		axes[1].set_title(f"Recon (MSE={mse_val:.4f})")
		axes[1].axis("off")

		plt.tight_layout()

		if save_prefix is not None:
			plt.savefig(f"{save_prefix}_{i}.png", dpi=150)
			plt.close(fig)
		elif show:
			plt.show()
		else:
			plt.close(fig)


with torch.no_grad():
	batch = next(iter(dataloader))
	glyph = batch[0].to(device)
	created_vector = ema_encoder(glyph)
	reconstructed_glyph = ema_decoder(created_vector)

visualize_recon_batch(glyph, reconstructed_glyph)

# ======================================================================================================================

import os
import csv
from pathlib import Path
import numpy as np
from tqdm import tqdm
import shutil
from PIL import Image

# ---------------------- CONFIG ----------------------
OUT_DIR = Path("glyphs/glyphs_no_blank2x2")  # folder containing metadata.csv, images/, etc.
EMBEDDINGS_NPY = OUT_DIR / "embeddings.npy"  # normalized embeddings (N, D) float32
NODES_CSV = OUT_DIR / "nodes.csv"  # optional nodes csv (with node_id column)
METADATA_CSV = OUT_DIR / "metadata.csv"  # fallback metadata
TENSOR_PATH = OUT_DIR / "glyphs_tensor.pt"  # optional: tensor to compute embeddings if embeddings.npy missing
IMAGES_DIR = OUT_DIR / "images"  # relative images folder referenced in metadata

THRESHOLD = 0.95  # cosine similarity threshold (set manually)
BATCH_SIZE_SIM = 1024  # batch size when computing similarities (tune for memory)
NUM_SAMPLES = int(2 ** vector_size)  # number of random vectors to sample and match to nearest actual embedding
SAMPLE_OUTPUT_DIR = OUT_DIR / f"sampled_nearest_{NUM_SAMPLES}"
EDGES_CSV = OUT_DIR / f"edges_threshold_{THRESHOLD:.3f}.csv"


# ----------------------------------------------------

# ---------------------- Helpers ----------------------
def ensure_outdir():
	if not OUT_DIR.exists():
		raise FileNotFoundError(f"OUT_DIR not found: {OUT_DIR.resolve()} - run glyph generation first")


def load_embeddings_or_compute():
	"""
	Returns emb_all: numpy array shape (N, D) with l2-normalized rows (float32).
	If EMBEDDINGS_NPY exists, loads it. Otherwise, attempts to compute by running 'ema_encoder'
	over glyphs tensor at TENSOR_PATH (so this must be run in the same session where ema_encoder exists).
	"""
	if EMBEDDINGS_NPY.exists():
		emb = np.load(EMBEDDINGS_NPY)
		if emb.ndim != 2:
			raise ValueError("embeddings.npy must be 2D (N,D)")
		# ensure float32 and normalized rows
		emb = emb.astype(np.float32)
		norms = np.linalg.norm(emb, axis=1, keepdims=True)
		norms[norms == 0] = 1.0
		emb = emb / norms
		print(f"Loaded embeddings.npy: shape={emb.shape}")
		return emb

	# else try to compute from ema_encoder + glyphs_tensor.pt
	print("embeddings.npy not found -> attempting to compute embeddings using ema_encoder and glyphs_tensor.pt")
	if not TENSOR_PATH.exists():
		raise FileNotFoundError(f"embeddings.npy missing and glyph tensor not found at {TENSOR_PATH}")

	# require ema_encoder in globals
	if 'ema_encoder' not in globals():
		raise RuntimeError(
			"embeddings.npy missing and 'ema_encoder' not found in globals. Run this script in same session as training or precompute embeddings.npy.")

	# Load glyph tensor and run through ema_encoder in batches
	import torch
	glyph_tensor = torch.load(str(TENSOR_PATH), map_location="cpu")  # [N,25]
	N = glyph_tensor.shape[0]
	device = next(ema_encoder.parameters()).device if any(
		p.requires_grad for p in ema_encoder.parameters()) else torch.device("cpu")
	ema_encoder.eval()
	batch_size = 2048 if torch.cuda.is_available() else 512
	emb_list = []
	from torch.utils.data import TensorDataset, DataLoader
	dl = DataLoader(TensorDataset(glyph_tensor), batch_size=batch_size, shuffle=False, num_workers=0)
	with torch.no_grad():
		for batch in tqdm(dl, desc="computing embeddings"):
			x = batch[0].to(device).float()
			e = ema_encoder(x)  # assume output shape [B, D]
			emb_list.append(e.cpu().numpy())
	emb_all = np.vstack(emb_list).astype(np.float32)
	# normalize rows
	norms = np.linalg.norm(emb_all, axis=1, keepdims=True)
	norms[norms == 0] = 1.0
	emb_all = emb_all / norms
	# save
	np.save(EMBEDDINGS_NPY, emb_all.astype(np.float32))
	print("Saved computed normalized embeddings to:", EMBEDDINGS_NPY)
	return emb_all


def load_nodes_metadata():
	"""
	Returns list of rows (ordered) where each row is a dict with at least a 'node_id' and 'filename' (or 'file').
	If nodes.csv exists, use it (sorted by node_id). Otherwise read metadata.csv and create node_id sequentially.
	"""
	if NODES_CSV.exists():
		rows = []
		with NODES_CSV.open(newline='') as cf:
			reader = csv.DictReader(cf)
			for r in reader:
				rows.append(r)
		# ensure sort by node_id
		if rows and 'node_id' in rows[0]:
			rows = sorted(rows, key=lambda x: int(x['node_id']))
		# ensure filenames exist as expected
		return rows
	elif METADATA_CSV.exists():
		rows = []
		with METADATA_CSV.open(newline='') as cf:
			reader = csv.DictReader(cf)
			for r in reader:
				rows.append(r)
		# annotate node_id sequentially if missing
		for i, r in enumerate(rows):
			r.setdefault('node_id', str(i))
		return rows
	else:
		raise FileNotFoundError(f"Neither {NODES_CSV} nor {METADATA_CSV} exist. Cannot map nodes to filenames.")


def build_threshold_edges(emb_all: np.ndarray, threshold: float, out_csv: Path, batch_size: int = 1024):
	"""
	Writes all pairs (source, target, weight) where cosine similarity >= threshold (excluding self).
	Batch over source rows to avoid NxN in memory.
	"""
	N, D = emb_all.shape
	print(f"Building edges with threshold {threshold}: N={N}, D={D}, batch_size={batch_size}")
	with out_csv.open("w", newline='') as of:
		writer = csv.writer(of)
		writer.writerow(["source", "target", "weight"])
		for start in tqdm(range(0, N, batch_size), desc="threshold-edge-batches"):
			end = min(N, start + batch_size)
			batch = emb_all[start:end]  # shape [m, D]
			sims = np.dot(batch, emb_all.T)  # shape [m, N]
			# iterate rows to find hits
			for i_row in range(sims.shape[0]):
				src = start + i_row
				row = sims[i_row]  # vector length N
				# find indices where sim >= threshold
				hits = np.nonzero(row >= threshold)[0]
				for tgt in hits:
					if tgt == src:
						continue
					writer.writerow([int(src), int(tgt), float(row[tgt])])
	print("Edges CSV written to:", out_csv)


def sample_random_nearest(emb_all: np.ndarray, num_samples: int, out_dir: Path, nodes_rows, mode: str | None = None):
	"""
	Generate sample vectors and find nearest real embeddings, copying corresponding images.

	Modes:
	  - 'hypercube' : generate ALL binary vectors in {0,1}^D (count = 2**D). This is used automatically
					  when num_samples == 2**D (and D is small enough).
	  - 'onehot'    : generate D one-hot vectors (1 in one dimension, 0 otherwise). Used automatically
					  when num_samples == D.
	  - 'random'    : generate random Gaussian vectors normalized to unit length (old behaviour).
	  - None        : auto-select based on num_samples (hypercube if matches 2**D, onehot if matches D,
					  otherwise random).

	Notes:
	  - emb_all must be L2-normalized rows (shape [N, D]).
	  - The function normalizes sample vectors to unit length before matching (cosine similarity).
	  - For hypercube: WARNING — 2**D grows fast. The function will raise if D>=18 unless you explicitly set
		mode='hypercube' and accept the risk. (Change the threshold if you know what you do.)
	"""
	N, D = emb_all.shape
	out_dir = Path(out_dir)
	out_dir.mkdir(parents=True, exist_ok=True)

	# Auto-mode selection
	if mode is None:
		if num_samples == (1 << D):
			selected_mode = 'hypercube'
		elif num_samples == D:
			selected_mode = 'onehot'
		else:
			selected_mode = 'random'
	else:
		selected_mode = mode

	# Safety check for hypercube explosion
	if selected_mode == 'hypercube':
		total = 1 << D
		if num_samples != total:
			raise ValueError(f"hypercube mode requires num_samples == 2**D ({total}), got {num_samples}")
		if D >= 18:  # conservative safeguard (2^18 = 262144)
			raise RuntimeError(f"Refusing to instantiate hypercube with D={D} (2**D too large). "
							   "If you really want this, call with mode='hypercube' and set D smaller.")
		# create all binary vectors in {0,1}^D as float32
		# efficient approach: generate integers 0..2^D-1 then unpack bits
		ints = np.arange(total, dtype=np.uint32)
		# create bit matrix: shape (total, D)
		# unpackbits works with bytes; for D <= 32 we can use bit ops
		bin_matrix = ((ints[:, None] >> np.arange(D - 1, -1, -1)) & 1).astype(np.float32)  # big-endian bit order
		sample_vecs = bin_matrix  # shape [total, D], values in {0,1}
	elif selected_mode == 'onehot':
		if num_samples != D:
			raise ValueError(f"onehot mode requires num_samples == D ({D}), got {num_samples}")
		sample_vecs = np.eye(D, dtype=np.float32)  # shape [D, D]
	elif selected_mode == 'random':
		rng = np.random.default_rng(seed=0)
		sample_vecs = rng.standard_normal(size=(num_samples, D)).astype(np.float32)
	else:
		raise ValueError(f"Unknown mode: {selected_mode}")

	# Normalize sample vectors to unit length (L2). Avoid zero vectors.
	norms = np.linalg.norm(sample_vecs, axis=1, keepdims=True)
	norms[norms == 0] = 1.0
	sample_unit = (sample_vecs / norms).astype(np.float32)  # shape [M, D]

	# Find nearest embedding (cosine) for each sample vector.
	# Compute dot = sample_unit @ emb_all.T -> shape [M, N]
	# Do this in batches to avoid memory blowups.
	M = sample_unit.shape[0]
	batch_size = 256  # tuneable
	nearest_indices = []
	nearest_similarities = []
	for s in range(0, M, batch_size):
		e = sample_unit[s: s + batch_size]  # [m, D]
		sims = np.dot(e, emb_all.T)  # [m, N]
		idxs = np.argmax(sims, axis=1)  # [m]
		vals = sims[np.arange(sims.shape[0]), idxs]  # [m]
		nearest_indices.extend(idxs.tolist())
		nearest_similarities.extend(vals.tolist())

	# Group by unique node index (so we copy each image once)
	unique_map = {}
	for s_id, (idx, sim) in enumerate(zip(nearest_indices, nearest_similarities)):
		if idx not in unique_map:
			unique_map[idx] = {'sample_ids': [s_id], 'similarities': [sim]}
		else:
			unique_map[idx]['sample_ids'].append(s_id)
			unique_map[idx]['similarities'].append(sim)

	# Copy image files and produce CSV
	sample_csv_rows = []
	for out_i, (node_idx, info) in enumerate(unique_map.items()):
		node_idx = int(node_idx)
		row = nodes_rows[node_idx]
		filename_rel = row.get("filename") or row.get("file") or row.get("image")
		if filename_rel is None:
			raise ValueError(f"No filename column found for node {node_idx} in metadata")
		src_path = (OUT_DIR / filename_rel)
		if not src_path.exists():
			src_path = (IMAGES_DIR / Path(filename_rel).name)
			if not src_path.exists():
				raise FileNotFoundError(f"Image for node {node_idx} not found at {src_path}")
		out_fname = out_dir / f"sample_node{node_idx:05d}.png"
		shutil.copyfile(src_path, out_fname)
		sample_csv_rows.append({
			"sample_id": out_i,
			"node_id": node_idx,
			"orig_filename": filename_rel,
			"copied_filename": str(out_fname.relative_to(OUT_DIR)),
			"representative_sample_ids": ";".join(map(str, info['sample_ids'])),
			"max_similarity_among_samples": max(info['similarities'])
		})

	# Write CSV
	with (out_dir / "sampled_nearest.csv").open("w", newline='') as sf:
		fieldnames = ["sample_id", "node_id", "orig_filename", "copied_filename",
					  "representative_sample_ids", "max_similarity_among_samples"]
		writer = csv.DictWriter(sf, fieldnames=fieldnames)
		writer.writeheader()
		for r in sample_csv_rows:
			writer.writerow(r)

	print(
		f"Mode used: {selected_mode}. Generated {M} sample vectors, copied {len(sample_csv_rows)} unique nearest images to: {out_dir}")
	return unique_map  # returns mapping node_idx -> info (sample_ids, similarities)


# -------------------- Main --------------------
def main():
	ensure_outdir()
	# Step 1: load or compute embeddings
	emb_all = load_embeddings_or_compute()  # numpy (N,D), normalized rows
	N, D = emb_all.shape
	print(f"Embeddings shape: N={N}, D={D}")

	# Step 2: load metadata rows to map indices -> filenames
	nodes_rows = load_nodes_metadata()
	if len(nodes_rows) != N:
		print("Warning: metadata rows count does not equal embeddings count.")
		print(f" metadata rows = {len(nodes_rows)}, embeddings N = {N}")
	# proceed anyway — mapping expects the same order as embeddings

	# Step 3: build edges by threshold (writes EDGES_CSV)
	print("Writing edges CSV (threshold mode). This may take time depending on threshold and N.")
	build_threshold_edges(emb_all, threshold=THRESHOLD, out_csv=EDGES_CSV, batch_size=BATCH_SIZE_SIM)

	# Step 4: sample random unit vectors and copy nearest actual glyph images
	SAMPLE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
	sample_random_nearest(emb_all, num_samples=NUM_SAMPLES, out_dir=SAMPLE_OUTPUT_DIR, nodes_rows=nodes_rows)

	print("Done.")
	print("Outputs folder:", OUT_DIR)
	print(" - edges CSV:", EDGES_CSV)
	print(" - sampled images folder:", SAMPLE_OUTPUT_DIR)


if __name__ == "__main__":
	main()
