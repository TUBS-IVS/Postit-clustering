"""Embed the cleaned notes with SBERT, project them to 3D and cluster them.

Reads data/processed/postits_clean.json (`text_clean`) and writes, per model:
  data/processed/clusters_<model>.csv   row, cluster, x, y, z and the note text
  data/processed/clusters_<model>.html  interactive 3D page with source details (see src/plot_html.py)
  data/processed/clusters_<model>_simple.html  the same page with a short hover

Usage:
    python src/embed_cluster.py
    python src/embed_cluster.py --model all-MiniLM-L6-v2 --min-cluster-size 4
"""

import argparse
import json
from pathlib import Path

import hdbscan
import pandas as pd
import umap
from sentence_transformers import SentenceTransformer

from plot_html import write_pages

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
    parser.add_argument("--min-samples", type=int, default=2,
                        help="HDBSCAN: how crowded an area must be to count as a cluster (higher = more noise; "
                             "unset it means min-cluster-size, which gave 99 noise notes vs 50 with 2)")
    parser.add_argument("--neighbors", type=int, default=15, help="UMAP: local (small) vs global (large) structure")
    args = parser.parse_args()

    df = pd.DataFrame(json.load(open(SOURCE, encoding="utf-8")))
    texts = df["text_clean"].tolist()

    # Normalized vectors, so Euclidean distance on them ranks like cosine similarity.
    vectors = SentenceTransformer(args.model).encode(texts, normalize_embeddings=True, show_progress_bar=True)

    # One 3-D map for both clustering and plotting, so the plot shows exactly what was clustered.
    xyz = umap.UMAP(n_components=3, n_neighbors=args.neighbors, min_dist=0.0, metric="cosine",
                    random_state=SEED).fit_transform(vectors)
    labels = hdbscan.HDBSCAN(min_cluster_size=args.min_cluster_size, min_samples=args.min_samples).fit_predict(xyz)

    out = df[["row", "bild", "adresse", "text_clean"]].copy()
    out["cluster"] = labels  # -1 = noise, a note that fits no cluster
    out[["x", "y", "z"]] = xyz
    name = Path(args.model).name.replace("/", "_")

    themes = cluster_themes(df["text_tokens"], labels)
    out["theme"] = [themes.get(c, "noise (fits no cluster)") for c in labels]
    out.to_csv(OUT_DIR / f"clusters_{name}.csv", index=False, encoding="utf-8-sig")

    write_pages(out, f"Post-it clusters · {args.model}", OUT_DIR / f"clusters_{name}.html")

    n_clusters = len(set(labels) - {-1})
    print(f"{len(out)} notes, {n_clusters} clusters, {(labels == -1).sum()} noise -> clusters_{name}.csv/.html")
    for c in sorted(set(labels) - {-1}):
        print(f"  cluster {c} ({(labels == c).sum()} notes): {themes[c]}")


if __name__ == "__main__":
    main()
