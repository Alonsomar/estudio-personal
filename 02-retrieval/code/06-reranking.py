"""Sección 6 — Reranking: LLM-as-reranker + ilustraciones de cross-encoder y ColBERT.

Sobre el corpus de 16 docs, parte del hybrid-RRF de §3 (BM25 + denso) como
RETRIEVER, y mide qué pasa al pasarlo por un reranker LLM (pointwise y
listwise) con gpt-4o-mini. También muestra:
  - El "techo" del reranker: recall@10 de la base (lo máximo que un reordenador
    perfecto podría llevar a @1).
  - Un caso cualitativo donde el reranker rescata el doc correcto a #1.
  - Una ilustración numérica del problema que arregla un cross-encoder.
  - Un MaxSim a la ColBERT (aproximación a nivel oración) trabajado a mano.

Ejecutar (usa caché de embeddings + caché LLM separada para rerank):
    uv run python 02-retrieval/code/06-reranking.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retrieval_lib import (  # noqa: E402
    BM25Retriever,
    DenseRetriever,
    HybridRetriever,
    LLMReranker,
    OpenAIEmbedder,
    RerankedRetriever,
    ScoredDoc,
    _l2_normalize,
    load_corpus_chunks,
    sentence_split,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.utils import get_project_root  # noqa: E402

ROOT = get_project_root()
CORPUS_DIR = ROOT / "shared" / "corpus_chileno"
EMB_CACHE = ROOT / "02-retrieval" / "examples" / "cache-embeddings" / "embeddings.npz"
RERANK_CACHE = ROOT / "02-retrieval" / "examples" / "cache-rerank.json"
DIAGRAMS = ROOT / "02-retrieval" / "diagrams"


def hr(title: str = "") -> None:
    print("\n" + "=" * 78)
    if title:
        print(title)
        print("=" * 78)


def docs_in_order(results: list[ScoredDoc]) -> list[str]:
    seen: list[str] = []
    for r in results:
        if r.chunk.doc_id not in seen:
            seen.append(r.chunk.doc_id)
    return seen


def recall_at_k(retriever, golden, k: int) -> float:
    vals = []
    for item in golden:
        if not item["expected_docs"]:
            continue
        docs = set(docs_in_order(retriever.search(item["query"], k=k))[:k])
        vals.append(len(docs & set(item["expected_docs"])) / len(item["expected_docs"]))
    return sum(vals) / len(vals)


def main() -> None:
    chunks = load_corpus_chunks(CORPUS_DIR)
    golden = json.loads(
        (ROOT / "01-evals" / "examples" / "golden-dataset-rag-fiscal.json").read_text(
            encoding="utf-8"
        )
    )["items"]
    embedder = OpenAIEmbedder(cache_path=EMB_CACHE)
    bm25 = BM25Retriever().fit(chunks)
    dense = DenseRetriever(embedder).fit(chunks)
    hybrid = HybridRetriever([bm25, dense], method="rrf", pool=20)
    reranker = LLMReranker(cache_path=RERANK_CACHE)

    POOL = 10
    rerank_pw = RerankedRetriever(hybrid, reranker.pointwise, pool=POOL)
    rerank_lw = RerankedRetriever(hybrid, reranker.listwise, pool=POOL)

    print(f"Corpus: {len(chunks)} chunks | reranker={reranker.model} | pool={POOL}")

    hr("1. El techo del reranker: ¿hasta dónde podría llegar?")
    # Recall@N de la base es el techo de recall@1 de cualquier reranker sobre top-N.
    for n in [5, 10, 20]:
        r = recall_at_k(hybrid, golden, n)
        print(f"  recall@{n} de hybrid-RRF = {r:.3f}  (techo de recall@1 reranking top-{n})")
    print(
        "\n  El reranker reordena el pool; no inventa docs. Si el doc correcto no\n"
        "  está en el top-N, ningún reranker lo recupera. Por eso §3 se obsesionó\n"
        "  con maximizar recall@k_grande: alimentar al reranker con un pool denso."
    )

    hr("2. Recall@k: base vs base + LLM-reranker")
    sistemas = {
        "BM25": bm25,
        "Denso": dense,
        "Hybrid-RRF": hybrid,
        "Hybrid-RRF + pointwise rerank": rerank_pw,
        "Hybrid-RRF + listwise rerank": rerank_lw,
    }
    rows = []
    print(f"  {'sistema':32s} {'@1':>7s} {'@3':>7s} {'@5':>7s}")
    for name, sysr in sistemas.items():
        r1 = recall_at_k(sysr, golden, 1)
        r3 = recall_at_k(sysr, golden, 3)
        r5 = recall_at_k(sysr, golden, 5)
        rows.append((name, r1, r3, r5))
        print(f"  {name:32s} {r1:7.3f} {r3:7.3f} {r5:7.3f}")
    print(f"\n  Llamadas LLM esta corrida: {reranker.api_calls} (resto desde caché).")

    hr("3. Caso de rescate: el reranker sube el doc correcto a #1")
    # gd-005 — un caso donde el hybrid-RRF puede dispersarse y el reranker enfoca.
    q = "Un proveedor de SaaS con sede en Irlanda vende a empresas chilenas. ¿Debe registrarse ante el SII? ¿Cada cuánto declara?"
    print(f'  Query: "{q}"')
    base_results = hybrid.search(q, k=POOL)
    print("\n  Top-5 de hybrid-RRF (entrada al reranker):")
    for i, r in enumerate(base_results[:5], 1):
        snippet = " ".join(r.chunk.text.split())[:80]
        print(f"    {i}. [{r.score:.4f}] {r.chunk.doc_id:38s} {snippet}…")
    reranked = reranker.listwise(q, base_results)
    print("\n  Tras LLM-reranker (listwise) — top 5:")
    for i, r in enumerate(reranked[:5], 1):
        snippet = " ".join(r.chunk.text.split())[:80]
        print(f"    {i}. [{r.score:.4f}] {r.chunk.doc_id:38s} {snippet}…")

    hr("4. Cross-encoder, conceptualmente (no ejecutado)")
    print(
        "  Bi-encoder (lo que usamos en §2):\n"
        "    embed(query)  →  vector_q\n"
        "    embed(doc)    →  vector_d   (precomputable y cacheable)\n"
        "    score = cos(vector_q, vector_d)\n"
        "  Costo: 1 forward pass por doc al indexar, 1 por query al recuperar.\n"
        "  Lo que se pierde: query y doc nunca se 'ven' juntos en la red.\n\n"
        "  Cross-encoder:\n"
        "    score = Transformer([CLS] query [SEP] doc [SEP]).pooled\n"
        "  La red atiende query↔doc token a token. Mucho más preciso, pero NO\n"
        "  precomputable: necesitas 1 forward pass por cada par (query, doc).\n"
        "  Por eso solo se usa para REORDENAR el top-N (10-100), no para buscar."
    )
    # Ilustración numérica: el caso "Ley 21.210" de §2-3. Bi-encoder confunde
    # el doc de contexto con el doc objetivo. Un cross-encoder atendería al
    # token "21.210" en ambos lados y los separaría.
    print(
        "\n  Ilustración con un caso real de §3:\n"
        "    Query: 'Ley Nº 21.210'\n"
        "    Bi-encoder coseno (dense de §2):\n"
        "      cos(query, doc 'PREVIO A LA LEY 21.210')  = 0.658\n"
        "      cos(query, doc 'LEY 21.210 MODERNIZA...') = 0.649\n"
        "      → el doc CORRECTO queda en el puesto 2.\n"
        "    Un cross-encoder leería los dos pasajes con la query y notaría\n"
        "    que uno dice 'previo a' y el otro 'modifica el DL 825': separaría\n"
        "    los scores con claridad y subiría el correcto al #1. Esto es justo\n"
        "    lo que vemos suceder con el LLM-reranker (la sección 3 anterior)."
    )

    hr("5. ColBERT / late interaction (MaxSim trabajado)")
    print(
        "  ColBERT (Khattab & Zaharia, 2020) es el punto medio entre bi- y cross-:\n"
        "    - Como bi-encoder: precomputas vectores por TOKEN del doc (no uno por doc).\n"
        "    - Como cross-encoder: la query también se descompone en tokens.\n"
        "    - Score: MaxSim(q, d) = Σ_i max_j  cos(q_i, d_j)\n"
        "      Cada token de la query 'vota' por su mejor match en el doc.\n"
        "    Indexación: K tokens por doc → K vectores guardados (K ≈ 50-100).\n"
        "    Costo de retrieval: rápido, índices especializados (PLAID).\n\n"
        "  Aproximación a nivel ORACIÓN (no es ColBERT real; ilustra la idea):"
    )
    # Aproximación: para una query, partimos query y doc en oraciones, embebemos
    # cada una con el bi-encoder, y calculamos MaxSim oración a oración.
    q = "Ley Nº 21.210"
    docs = [
        ("ley-02 (correcto)", "LEY Nº 21.210 MODERNIZA LA LEGISLACIÓN TRIBUTARIA. Publicada en el Diario Oficial el 24 de febrero de 2020. Modifica el Decreto Ley Nº 825 sobre IVA."),
        ("ley-01 (contexto, distractor)", "TEXTO REFUNDIDO VIGENTE AL AÑO 2019 (PREVIO A LA LEY Nº 21.210). Decreto Ley Nº 825 sobre Impuesto a las Ventas y Servicios."),
    ]
    q_sents = sentence_split(q) or [q]
    q_vecs = _l2_normalize(embedder.embed(q_sents))
    print(f"\n  Query partida en {len(q_sents)} 'tokens' (oraciones): {q_sents}\n")
    print(f"  {'doc':32s} cos (bi-encoder) | MaxSim (late interaction)")
    for label, doc in docs:
        d_sents = sentence_split(doc) or [doc]
        d_vecs = _l2_normalize(embedder.embed(d_sents))
        sim_matrix = q_vecs @ d_vecs.T          # (|q|, |d|)
        maxsim = float(sim_matrix.max(axis=1).sum())
        # Bi-encoder "naive": embed query y doc completos.
        qv = _l2_normalize(embedder.embed([q]))[0]
        dv = _l2_normalize(embedder.embed([doc]))[0]
        bi = float(qv @ dv)
        print(f"  {label:32s} {bi:7.3f}        | {maxsim:7.3f}")
    print(
        "\n  Honesto: en esta query el bi-encoder pone al distractor ligeramente\n"
        "  arriba, y la 'MaxSim a nivel oración' tampoco lo separa. El caso\n"
        "  'Ley 21.210' es genuinamente difícil para retrieval semántico (ambos\n"
        "  docs hablan de esa ley). ColBERT real opera a nivel TOKEN: una query\n"
        "  con varios tokens distintivos vota mejor que con uno solo dominante.\n"
        "  La separación fina aquí la trae un cross-encoder o un LLM-reranker\n"
        "  que entienda la relación lingüística entre 'previo a' y 'modifica'."
    )

    # ---- Diagrama: recall@{1,3,5} de los 5 sistemas. ---- #
    ks = [1, 3, 5]
    fig, ax = plt.subplots(figsize=(11, 5.5))
    x = list(range(len(ks)))
    width = 0.16
    colors = ["#7f7f7f", "#ff7f0e", "#2ca02c", "#1f77b4", "#d62728"]
    for i, (name, r1, r3, r5) in enumerate(rows):
        pos = [xi + (i - 2) * width for xi in x]
        bars = ax.bar(pos, [r1, r3, r5], width, label=name, color=colors[i])
        for b, v in zip(bars, [r1, r3, r5]):
            ax.text(b.get_x() + width / 2, v + 0.005, f"{v:.3f}", ha="center", fontsize=7)
    ax.set_xticks(x)
    ax.set_xticklabels([f"recall@{k}" for k in ks])
    ax.set_ylim(0.7, 1.02)
    ax.set_ylabel("Recall (golden, 25 queries)")
    ax.set_title(
        "Reranking sobre hybrid-RRF: mejora @3, hiere @1 (LLM tiene su propia idea)"
    )
    ax.legend(loc="lower right", fontsize=8.5)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    out = DIAGRAMS / "reranking-recall.png"
    fig.savefig(out, dpi=120)
    print(f"\n  Diagrama guardado en: {out}")


if __name__ == "__main__":
    main()
