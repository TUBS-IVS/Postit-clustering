"""Embed the cleaned notes with SBERT, project them to 3D and cluster them.

Reads data/processed/postits_clean.json (`text_clean`) and writes, per model:
  data/processed/clusters_<model>.csv   row, cluster, x, y, z and the note text
  data/processed/clusters_<model>.html  interactive 3D plot (hover shows the note)

Usage:
    python src/embed_cluster.py
    python src/embed_cluster.py --model all-MiniLM-L6-v2 --min-cluster-size 4
    python src/embed_cluster.py --layout supervised

Layouts:
  same        cluster on the 3-D map that is plotted (what you see is what was clustered)
  supervised  cluster on a 10-D map (keeps more detail), then lay out 3-D with the cluster
              labels as a guide, so each cluster sits together in the plot
"""

import argparse
import json
from pathlib import Path

import hdbscan
import pandas as pd
import plotly.express as px
import umap
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "processed" / "postits_clean.json"
OUT_DIR = ROOT / "data" / "processed"
SEED = 42


def cluster_themes(tokens, labels, n_words=4):
    """Theme per cluster: the words most typical of it compared with the other clusters (class-based TF-IDF)."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    docs = pd.Series(list(tokens)).groupby(labels).apply(" ".join).drop(-1, errors="ignore")
    tfidf = TfidfVectorizer(stop_words="english", token_pattern=r"[a-z][a-z-]+").fit(docs)
    matrix, words = tfidf.transform(docs), tfidf.get_feature_names_out()
    return {c: ", ".join(words[i] for i in matrix[k].toarray()[0].argsort()[::-1][:n_words])
            for k, c in enumerate(docs.index)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="all-mpnet-base-v2", help="sentence-transformers model name or local folder")
    parser.add_argument("--min-cluster-size", type=int, default=5, help="HDBSCAN: smallest group that counts as a cluster")
    parser.add_argument("--neighbors", type=int, default=15, help="UMAP: local (small) vs global (large) structure")
    parser.add_argument("--layout", choices=["same", "supervised"], default="same", help="see Layouts above")
    args = parser.parse_args()

    df = pd.DataFrame(json.load(open(SOURCE, encoding="utf-8")))
    texts = df["text_clean"].tolist()

    # Normalized vectors, so Euclidean distance on them ranks like cosine similarity.
    vectors = SentenceTransformer(args.model).encode(texts, normalize_embeddings=True, show_progress_bar=True)

    if args.layout == "same":
        # One 3-D map for both clustering and plotting, so the plot shows exactly what was clustered.
        xyz = umap.UMAP(n_components=3, n_neighbors=args.neighbors, min_dist=0.0, metric="cosine",
                        random_state=SEED).fit_transform(vectors)
        labels = hdbscan.HDBSCAN(min_cluster_size=args.min_cluster_size).fit_predict(xyz)
    else:
        coords = umap.UMAP(n_components=10, n_neighbors=args.neighbors, min_dist=0.0, metric="cosine",
                           random_state=SEED).fit_transform(vectors)
        labels = hdbscan.HDBSCAN(min_cluster_size=args.min_cluster_size).fit_predict(coords)
        # UMAP reads label -1 as "unknown", so noise is placed by text similarity alone.
        xyz = umap.UMAP(n_components=3, n_neighbors=args.neighbors, min_dist=0.1, metric="cosine",
                        target_weight=0.5, random_state=SEED).fit_transform(vectors, y=labels)

    out = df[["row", "bild", "adresse", "text_clean"]].copy()
    out["cluster"] = labels  # -1 = noise, a note that fits no cluster
    out[["x", "y", "z"]] = xyz
    name = Path(args.model).name.replace("/", "_") + ("_supervised" if args.layout == "supervised" else "")

    themes = cluster_themes(df["text_tokens"], labels)
    out["theme"] = [themes.get(c, "noise (fits no cluster)") for c in labels]
    out.to_csv(OUT_DIR / f"clusters_{name}.csv", index=False, encoding="utf-8-sig")

    plot = out.assign(group=[f"{c}: {themes[c]} ({(labels == c).sum()})" if c != -1 else f"noise ({(labels == -1).sum()})"
                             for c in labels])
    order = sorted(plot["group"].unique(), key=lambda g: (g.startswith("noise"), int(g.split(":")[0]) if ":" in g else 0))
    fig = px.scatter_3d(plot, x="x", y="y", z="z", color="group", category_orders={"group": order},
                        hover_data={"row": True, "adresse": True, "text_clean": True, "x": False, "y": False, "z": False},
                        title=f"Post-it clusters ({args.model}, {args.layout} layout) - click a legend entry to hide or show it")
    fig.update_traces(marker_size=4)
    for trace in fig.data:
        if trace.name.startswith("noise"):
            trace.marker.update(color="lightgrey", size=3, opacity=0.5)
    noise = [t.name.startswith("noise") for t in fig.data]
    fig.update_layout(legend_title_text="cluster: top words (notes)", updatemenus=[dict(
        type="buttons", direction="right", x=0, y=1.08, xanchor="left",
        buttons=[dict(label="Show noise", method="restyle", args=[{"visible": True}]),
                 dict(label="Hide noise", method="restyle", args=[{"visible": [not n for n in noise]}]),
                 dict(label="Only noise", method="restyle", args=[{"visible": noise}])])])
    fig.write_html(OUT_DIR / f"clusters_{name}.html")

    n_clusters = len(set(labels) - {-1})
    print(f"{len(out)} notes, {n_clusters} clusters, {(labels == -1).sum()} noise -> clusters_{name}.csv/.html")
    for c in sorted(set(labels) - {-1}):
        print(f"  cluster {c} ({(labels == c).sum()} notes): {themes[c]}")


if __name__ == "__main__":
    main()
