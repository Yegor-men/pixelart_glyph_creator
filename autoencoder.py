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

hidden_features = 128
dropout_prob = 0.1
vector_size = 10

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

from copy import deepcopy

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

loss_ema = 0.0
loss_decay = 0.99
for loss in ema_losses:
	loss_ema = loss_ema * loss_decay + loss * (1 - loss_decay)
print(f"Final Loss: {loss_ema}")


def visualize_recon_batch(glyph: torch.Tensor,
						  reconstructed_glyph: torch.Tensor,
						  max_images: int = 8,
						  cmap: str = "gray_r"):
	"""
	Visualize up to `max_images` pairs from the batch.
	- glyph: [b,25] float tensor (values 0..1)
	- reconstructed_glyph: [b,25] float tensor (values ideally 0..1)
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

	# compute per-example MSE (sum reduction)
	# resulting shape [b]
	with torch.no_grad():
		diffs = nn.functional.mse_loss(r, g, reduction="none")  # [b,25]
		mse_per_elem = diffs.sum(dim=1)  # [b]

	cols = 2
	rows = n
	figsize = (cols * 2.5, rows * 2.5)  # adjust sizing as desired
	fig, axes = plt.subplots(rows, cols, figsize=figsize)

	# ensure axes is 2D array for consistent indexing
	if rows == 1:
		axes = axes.reshape(1, -1)

	for i in range(n):
		orig = g[i].reshape(5, 5).numpy()
		recon = r[i].reshape(5, 5).numpy()
		mse_val = float(mse_per_elem[i].item())

		# original (left)
		ax_orig = axes[i, 0]
		ax_orig.imshow(orig, cmap=cmap, interpolation="nearest", vmin=0.0, vmax=1.0)
		ax_orig.set_title("orig")
		ax_orig.axis("off")

		# reconstruction (right) with MSE in title
		ax_recon = axes[i, 1]
		ax_recon.imshow(recon, cmap=cmap, interpolation="nearest", vmin=0.0, vmax=1.0)
		ax_recon.set_title(f"recon — MSE(sum)={mse_val:.6f}")
		ax_recon.axis("off")

	plt.tight_layout()
	plt.show()


with torch.no_grad():
	batch = next(iter(dataloader))
	glyph = batch[0].to(device)
	created_vector = ema_encoder(glyph)
	reconstructed_glyph = ema_decoder(created_vector)

visualize_recon_batch(glyph, reconstructed_glyph)
