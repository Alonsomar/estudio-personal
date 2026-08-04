"""§8 — Evaluar un sistema con ontología.

Produce los números de `theory/08-evaluar-con-ontologia.md`. El grafo se
somete al mismo tribunal que cualquier técnica nueva de este repo:
`golden-retrieval.json` de `02`, SIN modificar, con el aparato de `01 §8`
(bootstrap, IC). Si el grafo no gana, se publica igual.

Además: tres métricas que la ontología misma necesita y que retrieval no
mide — cobertura de entidades, consistencia y precisión de entity linking
(ya medida en §4/§5, se reporta acá como parte del tribunal completo).

    uv run python 05-ontologias/code/08-evaluar-con-ontologia.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "02-retrieval" / "code"))

from ontology_lib import Norma, RelacionNormativa, TipoRelacion, build_grafo_normativo  # noqa: E402
from retrieval_lib import (  # noqa: E402
    BM25Retriever,
    ScoredDoc,
    bootstrap_ci,
    evaluate_retriever,
    load_corpus_chunks,
)

from shared.utils import get_logger, get_project_root  # noqa: E402

log = get_logger(__name__)
CORPUS_DIR = get_project_root() / "shared" / "corpus_chileno"
DATA_PATH = Path(__file__).resolve().parent.parent / "examples" / "relaciones-manual.json"
GOLDEN_DOC = get_project_root() / "01-evals" / "examples" / "golden-dataset-rag-fiscal.json"
GOLDEN_CHUNK = get_project_root() / "02-retrieval" / "examples" / "golden-retrieval.json"


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


class GraphExpandedRetriever:
    """Envuelve un retriever base y PROMUEVE, dentro del ranking existente,
    a los vecinos de 1 salto de las semillas top-N, siguiendo SOLO
    relaciones "fuertes" (por defecto MODIFICA/REGLAMENTA/INTERPRETA — las
    que 02 §9 y §2 mostraron que cambian qué texto rige, no CITA, que
    conecta casi todo con casi todo).

    Diseño (segunda versión, tras detectar que anexar al final del pool
    nunca dejaba lugar dentro de recall@3/@5 — ver theory/08): se pide un
    ranking base PROFUNDO (k grande, cubre todo el corpus), y los vecinos
    de grafo de las semillas se reordenan justo después de ellas. No se
    inyecta ningún documento que el retriever base no haya encontrado por
    su cuenta en algún lugar del ranking — el grafo solo cambia el ORDEN,
    dándole a los vecinos legítimos la oportunidad real de entrar al top-k
    que la primera versión les negaba por diseño."""

    def __init__(self, base, grafo, tipos_expansion, n_semillas=3, profundidad=None):
        self.base = base
        self.grafo = grafo
        self.tipos_expansion = set(tipos_expansion)
        self.n_semillas = n_semillas
        self.profundidad = profundidad  # None = todo el corpus (ver search())

    def _vecinos_fuertes(self, doc_id: str) -> set[str]:
        if doc_id not in self.grafo:
            return set()
        vecinos: set[str] = set()
        for _, v, d in self.grafo.out_edges(doc_id, data=True):
            if d["tipo"] in self.tipos_expansion:
                vecinos.add(v)
        for u, _, d in self.grafo.in_edges(doc_id, data=True):
            if d["tipo"] in self.tipos_expansion:
                vecinos.add(u)
        return vecinos

    def search(self, query: str, k: int = 5) -> list[ScoredDoc]:
        profundidad = self.profundidad or 200  # >> tamaño del corpus: ranking completo
        base_results = self.base.search(query, k=profundidad)

        por_doc: dict[str, ScoredDoc] = {}
        orden: list[str] = []
        for r in base_results:
            if r.chunk.doc_id not in por_doc:
                por_doc[r.chunk.doc_id] = r
                orden.append(r.chunk.doc_id)

        semillas = orden[: self.n_semillas]
        vecinos_promovidos: list[str] = []
        for s in semillas:
            for v in self._vecinos_fuertes(s):
                if v in por_doc and v not in semillas and v not in vecinos_promovidos:
                    vecinos_promovidos.append(v)

        resto = [d for d in orden if d not in semillas and d not in vecinos_promovidos]
        nuevo_orden = semillas + vecinos_promovidos + resto
        return [por_doc[d] for d in nuevo_orden[:k]]


def cargar_grafo():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    normas = [Norma(**n) for n in data["normas"]]
    relaciones = [RelacionNormativa(**r) for r in data["relaciones"]]
    return build_grafo_normativo(normas, relaciones), normas, relaciones


def demo_cobertura_y_consistencia(normas, relaciones) -> None:
    """Métricas que la ontología misma necesita, no el retrieval."""
    seccion("1. Cobertura y consistencia de la ontología")

    corpus_total = {p.name for p in CORPUS_DIR.glob("*.txt")}
    normas_ids = {n.id for n in normas}
    cobertura = len(normas_ids & corpus_total) / len(corpus_total)
    fuera = sorted(corpus_total - normas_ids)
    print(f"Cobertura: {len(normas_ids & corpus_total)}/{len(corpus_total)} documentos = {cobertura:.1%}")
    print(f"Fuera de la ontología: {fuera}")
    print(
        "\nLos tres documentos fuera son los distractores que B6 diseñó a propósito\n"
        "(glosa-02, glosa-03, tabla-01 — sin relación normativa directa con los\n"
        "clusters modelados). Cobertura del 92.5% sobre documentos RELEVANTES es\n"
        "100%: nada quedó fuera que debiera estar dentro."
    )

    import networkx as nx

    mod_edges = [(r.origen, r.destino) for r in relaciones if r.tipo == TipoRelacion.MODIFICA]
    mg = nx.DiGraph(mod_edges)
    ciclos = list(nx.simple_cycles(mg))
    print(f"\nConsistencia: ciclos en relaciones MODIFICA (lógicamente imposibles "
          f"sin fecha): {len(ciclos)}")
    print("(Una norma no puede modificar a otra que la modifica a ella, sin más "
          "contexto temporal — sería circular.)" if not ciclos else f"  {ciclos}")

    print(
        "\nPrecisión de entity linking: medida en §4 (organismos) y §5\n"
        "(identificadores de norma) — no se repite acá. Resumen: 38% de precisión\n"
        "en match exacto contra el ground truth curado, con el matiz ya\n"
        "documentado de que ese número es una COTA INFERIOR (algunos 'falsos\n"
        "positivos' eran hallazgos correctos que la curación manual no anotó)."
    )


def demo_benchmark_retrieval(grafo):
    """El tribunal real: golden-retrieval.json, aparato de 01 §8."""
    seccion("2. El tribunal: BM25 solo vs. BM25 + expansión de grafo")

    chunks = load_corpus_chunks(CORPUS_DIR)
    golden_doc = json.loads(GOLDEN_DOC.read_text(encoding="utf-8"))["items"]
    golden_chunk = json.loads(GOLDEN_CHUNK.read_text(encoding="utf-8"))["items"]
    queries_by_id = {it["id"]: it["query"] for it in golden_doc}
    type_by_id = {it["id"]: it.get("query_type") for it in golden_doc}

    bm25 = BM25Retriever().fit(chunks)
    fuertes = [TipoRelacion.MODIFICA, TipoRelacion.REGLAMENTA, TipoRelacion.INTERPRETA]
    todas = list(TipoRelacion)

    sistemas = {
        "1. BM25 solo (baseline, 02 §1)": bm25,
        "2. BM25 + grafo (relaciones fuertes)": GraphExpandedRetriever(bm25, grafo, fuertes),
        "3. BM25 + grafo (todas las relaciones, incl. CITA)": GraphExpandedRetriever(bm25, grafo, todas),
    }

    resultados = {}
    for nombre, retr in sistemas.items():
        resultados[nombre] = evaluate_retriever(
            retr, golden_chunk, queries_by_id, granularity="doc", k_values=(1, 3, 5)
        )

    print(f"{'sistema':>48} | {'recall@3':>20} | {'recall@5':>20} | {'MRR':>20}")
    print("-" * 118)
    for nombre, res in resultados.items():
        s = res["summary"]

        def fmt(m):
            return f"{s[m]['mean']:.3f} [{s[m]['lo']:.3f},{s[m]['hi']:.3f}]"

        print(f"{nombre:>48} | {fmt('recall@3'):>20} | {fmt('recall@5'):>20} | {fmt('mrr'):>20}")

    return resultados, type_by_id


def demo_estratificado_multidoc(resultados: dict, type_by_id: dict) -> None:
    """El caso donde uno esperaría que el grafo ganara: queries multi-doc."""
    seccion("3. El caso más favorable al grafo: queries multi-doc")

    base_key = "1. BM25 solo (baseline, 02 §1)"
    graph_key = "2. BM25 + grafo (relaciones fuertes)"

    for qtype in ("multi-doc", "factual", "numerico", "entidad", "scope"):
        ids = {i for i, t in type_by_id.items() if t == qtype}
        b = [r["recall@3"] for r in resultados[base_key]["per_query"] if r["id"] in ids]
        e = [r["recall@3"] for r in resultados[graph_key]["per_query"] if r["id"] in ids]
        if not b:
            continue
        print(f"  {qtype:>10} (n={len(b)}): base recall@3={sum(b) / len(b):.3f}  "
              f"grafo recall@3={sum(e) / len(e):.3f}")

    print(
        "\nLas queries 'multi-doc' del golden (gd-017, gd-023, gd-024, gd-028) son las\n"
        "que, en teoría, más se beneficiarían de un grafo: piden síntesis entre dos\n"
        "documentos. Y son EXACTAMENTE donde el grafo no cambia nada. La razón,\n"
        "verificada: 'multi-doc' en este golden significa que la PREGUNTA combina dos\n"
        "temas (ej. gd-024 pregunta por servicios digitales Y por inmunizaciones de\n"
        "Salud a la vez) — no que los documentos estén conectados por una cita. \n"
        "circular-01 (IVA digital) y glosa-01 (presupuesto Salud) no tienen NINGUNA\n"
        "arista entre sí en el grafo normativo — están en comunidades distintas (§7).\n"
        "\n'Multi-doc' (la pregunta necesita dos fuentes) y 'multi-hop' (las fuentes\n"
        "están conectadas por una cadena de citas) NO son lo mismo. El grafo de este\n"
        "módulo ayuda con el segundo caso (§2, P4); este golden mide el primero."
    )


def demo_delta_con_ic(resultados: dict) -> None:
    """El delta de cada variante contra el baseline, con IC — no solo la
    media. La pregunta real: ¿la diferencia es significativa al 5%?"""
    seccion("4. Delta contra el baseline, con IC bootstrap (01 §8)")

    base_key = "1. BM25 solo (baseline, 02 §1)"
    base_records = {r["id"]: r for r in resultados[base_key]["per_query"]}

    for nombre, res in resultados.items():
        if nombre == base_key:
            continue
        print(f"\n{nombre}:")
        for metrica in ("recall@3", "recall@5", "mrr"):
            deltas = [
                r[metrica] - base_records[r["id"]][metrica] for r in res["per_query"]
            ]
            mean, lo, hi = bootstrap_ci(deltas)
            sig = "SÍ" if (lo > 0 or hi < 0) else "no"
            print(f"  Δ{metrica:>10}: {mean:+.3f}  IC95% [{lo:+.3f}, {hi:+.3f}]  ¿significativo? {sig}")

    n = len(resultados[base_key]["per_query"])
    print(
        f"\nCon n={n} queries (el mismo golden de doc-level, 01-evals), la regla ya\n"
        "establecida en 01 §8 aplica sin excepción: diferencias chicas rara vez dan\n"
        "significativas. Leer el resultado exacto abajo en la teoría — se publica\n"
        "tal como salió, gane o no gane el grafo."
    )


def grafico_estratificado(resultados: dict, type_by_id: dict) -> None:
    """Recall@3 por tipo de query, base vs. grafo — visualiza que ni
    siquiera en 'multi-doc' hay diferencia."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    base_key = "1. BM25 solo (baseline, 02 §1)"
    graph_key = "2. BM25 + grafo (relaciones fuertes)"
    tipos = ["multi-doc", "factual", "numerico", "entidad", "scope"]

    base_vals, graph_vals = [], []
    for qtype in tipos:
        ids = {i for i, t in type_by_id.items() if t == qtype}
        b = [r["recall@3"] for r in resultados[base_key]["per_query"] if r["id"] in ids]
        e = [r["recall@3"] for r in resultados[graph_key]["per_query"] if r["id"] in ids]
        base_vals.append(sum(b) / len(b) if b else 0.0)
        graph_vals.append(sum(e) / len(e) if e else 0.0)

    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    x = range(len(tipos))
    w = 0.35
    ax.bar([i - w / 2 for i in x], base_vals, width=w, label="BM25 solo", color="#3498db")
    ax.bar([i + w / 2 for i in x], graph_vals, width=w, label="BM25 + grafo", color="#e67e22")
    ax.set_xticks(list(x))
    ax.set_xticklabels(tipos)
    ax.set_ylabel("recall@3")
    ax.set_ylim(0, 1.15)
    ax.set_title(
        "Ni siquiera en 'multi-doc' —el caso más favorable al grafo—\nhay diferencia",
        fontsize=11,
    )
    ax.legend(fontsize=9)

    fig.tight_layout()
    out = Path(__file__).resolve().parent.parent / "diagrams" / "evaluacion-estratificada.png"
    fig.savefig(out, dpi=130)
    print(f"\n[diagrama] {out.relative_to(ROOT)}")


if __name__ == "__main__":
    log.info("Sometiendo el grafo al mismo tribunal que cualquier técnica de este repo.")
    grafo, normas, relaciones = cargar_grafo()
    demo_cobertura_y_consistencia(normas, relaciones)
    resultados, type_by_id = demo_benchmark_retrieval(grafo)
    demo_estratificado_multidoc(resultados, type_by_id)
    demo_delta_con_ic(resultados)
    grafico_estratificado(resultados, type_by_id)
    print()
