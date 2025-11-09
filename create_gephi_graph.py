#!/usr/bin/env python3
"""
export_glyph_graph.py

Export a graph (nodes + edges) from a glyph SQLite DB for Gephi.

Outputs (in out_dir):
 - nodes.csv    (columns: Id,Label,filled,components,assigned_bt,predicted_bt)
 - edges.csv    (columns: Source,Target,Weight)  -- undirected edges (each appears once)

Nodes are identified by their bitstring (row-major). Edges connect nodes that differ by exactly
one bit (Hamming distance == 1).
"""

import sqlite3
from pathlib import Path
import csv
from tqdm import tqdm

# ---------- Config ----------
DB_PATH = Path("dbs") / "glyphs_5_5.db"  # change if necessary
OUT_DIR = Path("graph_exports")  # directory to write nodes.csv and edges.csv
# ----------------------------

if not DB_PATH.exists():
	raise SystemExit(f"DB not found: {DB_PATH}")

OUT_DIR.mkdir(parents=True, exist_ok=True)
edges_path = OUT_DIR / "edges.csv"
nodes_path = OUT_DIR / "nodes.csv"

# Connect and load nodes (id, bitstring, filled, components, assigned_bt, predicted_bt)
conn = sqlite3.connect(str(DB_PATH))
cur = conn.cursor()

# try to determine schema presence
cur.execute("PRAGMA table_info(glyphs)")
cols_info = cur.fetchall()
col_names = [ci[1] for ci in cols_info]
has_assigned = "assigned_bt" in col_names
has_pred = "predicted_bt" in col_names

select_cols = "bitstring, filled, components"
if has_assigned:
	select_cols += ", assigned_bt"
else:
	select_cols += ", NULL as assigned_bt"
if has_pred:
	select_cols += ", predicted_bt"
else:
	select_cols += ", NULL as predicted_bt"

query = f"SELECT id, {select_cols} FROM glyphs ORDER BY id"
cur.execute(query)
rows = cur.fetchall()
if not rows:
	raise SystemExit("No glyphs found in DB (table may be empty).")

# Build mapping bitstring -> attributes
bit_to_attrs = {}
bitstrings = []
for db_id, *rest in rows:
	# rest: bitstring, filled, components, assigned_bt, predicted_bt
	bitstring = rest[0]
	filled = rest[1]
	components = rest[2]
	assigned_bt = rest[3]
	predicted_bt = rest[4]
	bit_to_attrs[bitstring] = {
		"db_id": db_id,
		"filled": filled,
		"components": components,
		"assigned_bt": assigned_bt,
		"predicted_bt": predicted_bt
	}
	bitstrings.append(bitstring)

nbits = len(bitstrings[0])
print(f"Loaded {len(bitstrings)} nodes from DB (bit-length = {nbits}).")

# Write nodes.csv
with open(nodes_path, "w", newline="") as nf:
	nw = csv.writer(nf)
	nw.writerow(["Id", "Label", "filled", "components", "assigned_bt", "predicted_bt"])
	for bs in bitstrings:
		a = bit_to_attrs[bs]
		nw.writerow([bs, bs, a["filled"], a["components"], a["assigned_bt"], a["predicted_bt"]])

# Prepare fast membership test
bitset = set(bitstrings)

# Open edges.csv for streaming write
with open(edges_path, "w", newline="") as ef:
	ew = csv.writer(ef)
	ew.writerow(["Source", "Target", "Weight"])
	# iterate nodes and generate neighbors
	for bs in tqdm(bitstrings, desc="Generating edges", unit="node"):
		bs_list = list(bs)
		for i in range(nbits):
			orig = bs_list[i]
			bs_list[i] = "0" if orig == "1" else "1"
			neighbor = "".join(bs_list)
			bs_list[i] = orig
			if neighbor in bitset and bs < neighbor:
				ew.writerow([bs, neighbor, 1])

print("Done.")
print(f"Nodes written: {nodes_path}")
print(f"Edges written: {edges_path}")
conn.close()
