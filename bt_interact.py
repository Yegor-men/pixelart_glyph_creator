#!/usr/bin/env python3
"""
bt_interact.py

Interactive Bradley–Terry data collection (console-driven) + occasional MLP training.

Behavior:
 - For each comparison the script creates a NEW matplotlib figure showing the pair
   and the training loss history, pauses briefly so the window appears, then waits
   for console input:
     1 -> left wins
     2 -> right wins
     ENTER or 't' -> tie
     a <val> -> set left to BT value (e.g. `a 0.9`)
     b <val> -> set right to BT value (e.g. `b 0.1`)
     r -> skip this pair
     x -> finish, run final full-DB predictions (if model trained) and exit
     q -> quit immediately (no final predictions)
 - After input the figure is closed and the next one is created.

Notes:
 - Torch is optional. If torch is present the script trains a small MLP every TRAIN_INTERVAL comparisons.
 - The script will create missing DB columns (assigned_bt, assigned_logit, predicted_bt) if they don't exist.
"""

import sqlite3
from pathlib import Path
import random
import math
import datetime
import time
from collections import deque

import numpy as np
import matplotlib.pyplot as plt

# Optional PyTorch
try:
	import torch
	import torch.nn as nn
	import torch.optim as optim

	TORCH = True
except Exception:
	TORCH = False

# -----------------------
# CONFIG
# -----------------------
DB_PATH = Path("dbs") / "glyphs_5_5.db"
POOL_SIZE = 200  # how many glyphs to sample into the interactive pool
TRAIN_INTERVAL = 10  # retrain MLP every N comparisons
EPOCHS = 8
BATCH_SIZE = 32
LR = 1e-3
K_UPDATE = 0.7  # Bradley–Terry logit learning rate factor
DEVICE = torch.device("cuda" if TORCH and torch.cuda.is_available() else "cpu")

# Pairing mixture settings
PAIR_EPSILON_RANDOM = 0.12
PAIR_MIX_PROPORTION = {"closest": 0.6, "diverse": 0.25, "top_vs_rand": 0.15}
DIVERSE_TOP_PCT = 0.10
CANDIDATE_PAIR_TRIES = 400
# -----------------------

if not DB_PATH.exists():
	raise SystemExit(f"DB not found: {DB_PATH}")


def sigmoid(x):
	return 1.0 / (1.0 + math.exp(-x))


def logit(p):
	p = min(max(p, 1e-12), 1.0 - 1e-12)
	return math.log(p / (1.0 - p))


class SmallMLP(nn.Module):
	def __init__(self, nbits, hidden=128):
		super().__init__()
		self.net = nn.Sequential(
			nn.Linear(nbits, hidden),
			nn.SiLU(),
			nn.Linear(hidden, hidden),
			nn.SiLU(),
			nn.Linear(hidden, 1),
			nn.Sigmoid()
		)

	def forward(self, x):
		return self.net(x).squeeze(-1)


# Ensure DB has columns used by this script (safe ALTERs)
def ensure_db_columns(conn):
	cur = conn.cursor()
	cur.execute("PRAGMA table_info(glyphs)")
	cols = [r[1] for r in cur.fetchall()]
	to_add = []
	if "assigned_bt" not in cols:
		to_add.append(("assigned_bt", "REAL"))
	if "assigned_logit" not in cols:
		to_add.append(("assigned_logit", "REAL"))
	if "predicted_bt" not in cols:
		to_add.append(("predicted_bt", "REAL"))
	for name, typ in to_add:
		try:
			cur.execute(f"ALTER TABLE glyphs ADD COLUMN {name} {typ}")
		except Exception:
			# ignore if it fails for some reason
			pass
	conn.commit()


def ensure_comparisons_table(conn):
	cur = conn.cursor()
	cur.execute("""
                CREATE TABLE IF NOT EXISTS comparisons
                (
                    id
                    INTEGER
                    PRIMARY
                    KEY
                    AUTOINCREMENT,
                    a_bitstring
                    TEXT,
                    b_bitstring
                    TEXT,
                    outcome
                    REAL,
                    ts
                    TEXT
                )
				""")
	conn.commit()


def store_comparison(conn, a_bs, b_bs, outcome):
	ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
	cur = conn.cursor()
	cur.execute("INSERT INTO comparisons (a_bitstring, b_bitstring, outcome, ts) VALUES (?,?,?,?)",
				(a_bs, b_bs, float(outcome), ts))
	conn.commit()


def write_assigned(conn, bitstring, logit_val):
	bt = sigmoid(logit_val)
	cur = conn.cursor()
	cur.execute("UPDATE glyphs SET assigned_bt=?, assigned_logit=? WHERE bitstring=?",
				(float(bt), float(logit_val), bitstring))
	conn.commit()


def load_pool(conn, pool_size):
	cur = conn.cursor()
	cur.execute("SELECT bitstring, assigned_bt, assigned_logit FROM glyphs")
	rows = cur.fetchall()
	if not rows:
		raise SystemExit("DB glyphs table empty.")
	sampled = random.sample(rows, min(pool_size, len(rows)))
	pool = []
	for bs, assigned_bt, assigned_logit in sampled:
		if assigned_bt is None:
			assigned_bt = 0.5
		if assigned_logit is None:
			assigned_logit = logit(assigned_bt)
		pool.append({"bitstring": bs, "assigned_bt": float(assigned_bt), "logit": float(assigned_logit)})
	return pool


# Mixture pair chooser (random / closest / diverse / top_vs_rand)
def choose_pair_mixture(pool, logits):
	n = len(pool)
	if n < 2:
		raise RuntimeError("Pool too small")

	# random-explore step
	if random.random() < PAIR_EPSILON_RANDOM:
		i, j = random.sample(range(n), 2)
		s_i, s_j = logits[i], logits[j]
		p = 1.0 / (1.0 + math.exp(-(s_i - s_j)))
		return i, j, p

	# pick strategy by weights
	choices = list(PAIR_MIX_PROPORTION.keys())
	weights = list(PAIR_MIX_PROPORTION.values())
	strat = random.choices(choices, weights=weights, k=1)[0]

	if strat == "closest":
		best = None
		best_score = 1.0
		tries = min(CANDIDATE_PAIR_TRIES, max(100, n * 4))
		for _ in range(tries):
			a, b = random.sample(range(n), 2)
			s_a, s_b = logits[a], logits[b]
			p = 1.0 / (1.0 + math.exp(-(s_a - s_b)))
			score = abs(p - 0.5)
			if score < best_score:
				best_score = score
				best = (a, b, p)
				if best_score < 0.01:
					break
		if best:
			return best

	if strat == "diverse":
		k = max(1, int(n * DIVERSE_TOP_PCT))
		sorted_idx = sorted(range(n), key=lambda ii: logits[ii], reverse=True)
		top = sorted_idx[:k]
		bot = sorted_idx[-k:]
		a = random.choice(top)
		b = random.choice(bot)
		s_a, s_b = logits[a], logits[b]
		p = 1.0 / (1.0 + math.exp(-(s_a - s_b)))
		return a, b, p

	if strat == "top_vs_rand":
		k = max(1, int(n * 0.2))
		sorted_idx = sorted(range(n), key=lambda ii: logits[ii], reverse=True)
		a = random.choice(sorted_idx[:k])
		b = random.choice([x for x in range(n) if x != a])
		s_a, s_b = logits[a], logits[b]
		p = 1.0 / (1.0 + math.exp(-(s_a - s_b)))
		return a, b, p

	# fallback
	i, j = random.sample(range(n), 2)
	s_i, s_j = logits[i], logits[j]
	p = 1.0 / (1.0 + math.exp(-(s_i - s_j)))
	return i, j, p


def glyph_to_image_arr(bitstring, w, h, scale=14, margin=1):
	bits = [1 if ch == "1" else 0 for ch in bitstring]
	H_px = (h + 2 * margin) * scale
	W_px = (w + 2 * margin) * scale
	arr = np.ones((H_px, W_px), dtype=np.uint8) * 255
	for r in range(h):
		for c in range(w):
			if bits[r * w + c]:
				y0 = (r + margin) * scale
				x0 = (c + margin) * scale
				arr[y0:y0 + scale, x0:x0 + scale] = 0
	return arr


def train_mlp(pool_bitstrings, pool_labels, nbits, epochs=8, batch_size=32, lr=1e-3):
	if not TORCH:
		return None, None
	X = np.array([[1.0 if ch == '1' else 0.0 for ch in bs] for bs in pool_bitstrings], dtype=np.float32)
	y = np.array(pool_labels, dtype=np.float32)
	N = len(X)
	idx = np.arange(N)
	np.random.shuffle(idx)
	split = int(N * 0.8)
	train_idx, val_idx = idx[:split], idx[split:]
	X_train = torch.tensor(X[train_idx]).to(DEVICE)
	y_train = torch.tensor(y[train_idx]).to(DEVICE)
	X_val = torch.tensor(X[val_idx]).to(DEVICE)
	y_val = torch.tensor(y[val_idx]).to(DEVICE)

	model = SmallMLP(nbits).to(DEVICE)
	opt = optim.Adam(model.parameters(), lr=lr)
	loss_fn = nn.MSELoss()
	val_losses = []
	for ep in range(epochs):
		model.train()
		perm = np.random.permutation(len(train_idx))
		for i in range(0, len(perm), batch_size):
			batch_idx = perm[i:i + batch_size]
			xb = X_train[batch_idx]
			yb = y_train[batch_idx]
			pred = model(xb)
			loss = loss_fn(pred, yb)
			opt.zero_grad()
			loss.backward()
			opt.step()
		model.eval()
		with torch.no_grad():
			vpred = model(X_val)
			vloss = float(loss_fn(vpred, y_val).item())
			val_losses.append(vloss)
	return model, val_losses


def compute_and_write_predictions_full(conn, model, nbits, batch_size=256):
	if not TORCH or model is None:
		raise RuntimeError("Torch missing or model None.")
	cur = conn.cursor()
	cur.execute("SELECT bitstring FROM glyphs ORDER BY id")
	rows = cur.fetchall()
	all_bits = [r[0] for r in rows]
	N = len(all_bits)
	for i in range(0, N, batch_size):
		chunk = all_bits[i:i + batch_size]
		X = np.array([[1.0 if ch == '1' else 0.0 for ch in bs] for bs in chunk], dtype=np.float32)
		Xt = torch.tensor(X).to(DEVICE)
		with torch.no_grad():
			pred = model(Xt).cpu().numpy()
		cur.executemany("UPDATE glyphs SET predicted_bt=? WHERE bitstring=?",
						[(float(p), bs) for p, bs in zip(pred, chunk)])
		conn.commit()


def read_meta_size(conn):
	cur = conn.cursor()
	try:
		cur.execute("SELECT v FROM meta WHERE k='w'")
		w = int(cur.fetchone()[0])
		cur.execute("SELECT v FROM meta WHERE k='h'")
		h = int(cur.fetchone()[0])
	except Exception:
		cur.execute("SELECT bitstring FROM glyphs LIMIT 1")
		row = cur.fetchone()
		if not row:
			raise RuntimeError("DB empty or corrupt")
		L = len(row[0])
		side = int(math.sqrt(L))
		w = h = side
	return w, h


def apply_manual_assign(conn, pool, logits, idx_side, target_bt):
	"""
	Assign target_bt to pool[idx_side] both in DB and in-memory.
	"""
	target_bt = float(target_bt)
	target_bt = max(min(target_bt, 0.999999), 1e-6)
	target_logit = logit(target_bt)
	bitstring = pool[idx_side]['bitstring']
	write_assigned(conn, bitstring, target_logit)
	pool[idx_side]['logit'] = float(target_logit)
	pool[idx_side]['assigned_bt'] = float(target_bt)
	logits[idx_side] = float(target_logit)
	return target_logit


def main():
	conn = sqlite3.connect(str(DB_PATH))
	ensure_db_columns(conn)
	ensure_comparisons_table(conn)

	w, h = read_meta_size(conn)
	print(f"DB {DB_PATH} loaded. Glyph size: {w}x{h}")

	pool = load_pool(conn, POOL_SIZE)
	nbits = w * h
	logits = [p["logit"] for p in pool]

	comparisons_done = 0
	trained_model = None
	val_loss_history = []

	try:
		while True:
			i, j, p_est = choose_pair_mixture(pool, logits)
			a = pool[i]
			b = pool[j]
			a_bs = a["bitstring"]
			b_bs = b["bitstring"]

			# create a NEW figure each loop
			fig = plt.figure(figsize=(9, 6))
			gs = fig.add_gridspec(2, 2, height_ratios=[2, 1])
			axL = fig.add_subplot(gs[0, 0])
			axR = fig.add_subplot(gs[0, 1])
			axLoss = fig.add_subplot(gs[1, :])

			left_img = glyph_to_image_arr(a_bs, w, h, scale=14, margin=1)
			right_img = glyph_to_image_arr(b_bs, w, h, scale=14, margin=1)
			axL.imshow(left_img, cmap='gray', vmin=0, vmax=255)
			axL.set_title(f"Left (idx={i}) bt={sigmoid(logits[i]):.3f}")
			axL.axis('off')
			axR.imshow(right_img, cmap='gray', vmin=0, vmax=255)
			axR.set_title(f"Right (idx={j}) bt={sigmoid(logits[j]):.3f}")
			axR.axis('off')

			if val_loss_history:
				axLoss.plot(val_loss_history, marker='o')
				axLoss.set_ylabel("val MSE")
				axLoss.set_xlabel("retrain step")
			axLoss.set_title(
				f"Comparisons: {comparisons_done} (input: 1/2/ENTER=tie, a/b <val>=assign, r=skip, x finish, q quit)")

			# force draw and give backend a moment so window appears
			fig.canvas.draw()
			plt.pause(0.2)  # <<< important to allow the window to render before input()

			# console input (blocking) — user asked for console-driven workflow
			user = input(
				"Which do you prefer? (1=left, 2=right, ENTER=tie, a 0.9, b 0.1, r skip, x finish, q quit): ").strip()
			# close the figure immediately (we make a fresh one next loop)
			plt.close(fig)

			# quick parse for manual assign
			if user:
				parts = user.split()
			else:
				parts = []

			if user == 'q':
				print("Quit requested. Exiting without final predictions.")
				break
			if user == 'x':
				print("Finish requested. Running final full-DB prediction (if model present)...")
				if trained_model is not None and TORCH:
					compute_and_write_predictions_full(conn, trained_model, nbits)
					print("Predicted BT values written to DB (predicted_bt).")
				else:
					print("No trained model available (or torch missing) — skipped final predictions.")
				break

			# manual assigns: 'a 0.9' or 'b 0.1'
			if len(parts) == 2 and parts[0].lower() in ('a', 'b'):
				side = parts[0].lower()
				try:
					val = float(parts[1])
					if side == 'a':
						apply_manual_assign(conn, pool, logits, i, val)
						print(f"Assigned LEFT idx={i} to BT={val:.3f}")
					else:
						apply_manual_assign(conn, pool, logits, j, val)
						print(f"Assigned RIGHT idx={j} to BT={val:.3f}")
				except Exception:
					print("Bad manual assign syntax; use e.g. 'a 0.9' or 'b 0.1'")
				# skip storing a comparison; continue
				continue

			# skip command
			if user.lower() in ('r', 'rand', 'skip'):
				print("Skipped this pair.")
				continue

			# interpret choices (default empty -> tie)
			if user == '1':
				outcome = 1.0
				desc = "Left wins"
			elif user == '2':
				outcome = 0.0
				desc = "Right wins"
			else:
				outcome = 0.5
				desc = "Tie"

			# store & update
			store_comparison(conn, a_bs, b_bs, outcome)
			s_i = logits[i]
			s_j = logits[j]
			expected = 1.0 / (1.0 + math.exp(-(s_i - s_j)))
			delta = K_UPDATE * (outcome - expected)
			s_i_new = s_i + delta
			s_j_new = s_j - delta
			logits[i] = s_i_new
			logits[j] = s_j_new
			write_assigned(conn, a_bs, s_i_new)
			write_assigned(conn, b_bs, s_j_new)

			comparisons_done += 1
			print(f"Recorded: {desc} — left={sigmoid(s_i_new):.3f}, right={sigmoid(s_j_new):.3f}")

			# retrain occasionally
			if TORCH and (comparisons_done % TRAIN_INTERVAL == 0):
				pool_bitstrings = [p["bitstring"] for p in pool]
				pool_labels = [sigmoid(x) for x in logits]
				model, val_losses = train_mlp(pool_bitstrings, pool_labels, nbits,
											  epochs=EPOCHS, batch_size=BATCH_SIZE, lr=LR)
				if model is not None:
					trained_model = model
					if val_losses:
						val_loss_history.append(val_losses[-1])
						print(f"Retrained model — val MSE {val_losses[-1]:.6g}")
					else:
						print("Retrained model (no val loss returned).")
				else:
					print("Torch not available — skipped training.")

	except KeyboardInterrupt:
		print("\nInterrupted by user.")
	finally:
		conn.close()
		print("Session ended. Comparisons recorded:", comparisons_done)


if __name__ == "__main__":
	main()
