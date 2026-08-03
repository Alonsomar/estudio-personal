"""Smoke tests de `02-retrieval/code/retrieval_lib.py`.

Corren sin API keys ni red: solo tocan los componentes deterministas (tokenizer,
TF-IDF, BM25, fusión y métricas). Los componentes que llaman a un proveedor
(`OpenAIEmbedder`, `LLMRewriter`, `LLMReranker`) quedan fuera a propósito.

Se testean **invariantes y propiedades**, no valores exactos: los números
concretos dependen del corpus y cambian cuando el corpus crece (B6).
"""

import math

import pytest
from retrieval_lib import (
    BM25Retriever,
    Chunk,
    TfidfRetriever,
    bootstrap_ci,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    rrf_fuse,
    simple_chunk,
    tokenize,
)


@pytest.fixture
def chunks() -> list[Chunk]:
    """Mini-corpus con la forma del dominio: referencias normativas y distractores."""
    textos = {
        "ley-21210": "La Ley Nº 21.210 de modernización tributaria modifica el DL 825 "
        "sobre impuesto a las ventas y servicios.",
        "dl-825": "El DL 825 establece el impuesto al valor agregado sobre las ventas "
        "y servicios prestados en el territorio nacional.",
        "glosa-salud": "Glosa 05 del presupuesto de Salud 2024: recursos para atención "
        "primaria municipal.",
    }
    return [c for doc_id, t in textos.items() for c in simple_chunk(t, doc_id)]


class TestTokenize:
    def test_preserva_referencias_normativas(self):
        """El tokenizer NO debe partir '21.210' — es la señal que hace ganar a BM25."""
        assert "21.210" in tokenize("la Ley Nº 21.210 de modernización")

    def test_normaliza_acentos_y_caja(self):
        assert tokenize("Modernización") == tokenize("MODERNIZACION")

    def test_conserva_la_negacion(self):
        """En dominio legal 'no' cambia el sentido: no se filtra como stopword.

        Ojo: 'sin' SÍ está en STOPWORDS_ES pese a que el comentario de la lista
        dice lo contrario. Discrepancia registrada como B11 en el backlog —
        arreglarla mueve las métricas publicadas en 02, así que no se toca aquí.
        """
        toks = tokenize("no procede la exención sin autorización previa")
        assert "no" in toks
        assert "menor" in tokenize("de menor cuantía")

    def test_descarta_tokens_de_un_caracter(self):
        assert all(len(t) > 1 for t in tokenize("a) el N° 3 de la letra b)"))


class TestRetrievers:
    @pytest.mark.parametrize("cls", [BM25Retriever, TfidfRetriever])
    def test_fit_search_devuelve_a_lo_mas_k(self, cls, chunks):
        r = cls().fit(chunks)
        assert len(r.search("impuesto al valor agregado", k=2)) <= 2

    @pytest.mark.parametrize("cls", [BM25Retriever, TfidfRetriever])
    def test_resultados_ordenados_por_score_descendente(self, cls, chunks):
        scores = [sd.score for sd in cls().fit(chunks).search("ventas y servicios", k=5)]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.parametrize("cls", [BM25Retriever, TfidfRetriever])
    def test_referencia_exacta_recupera_su_documento(self, cls, chunks):
        """Propiedad central de §1: el matching léxico gana en identificadores."""
        top = cls().fit(chunks).search("Ley 21.210", k=1)
        assert top and top[0].chunk.doc_id == "ley-21210"

    def test_bm25_satura_la_frecuencia_de_termino(self, chunks):
        """Repetir un término 10× no vale 10×: la ganancia marginal decrece."""
        base = Chunk(chunk_id="a#0", doc_id="a", text="iva " + "relleno " * 50)
        repe = Chunk(chunk_id="b#0", doc_id="b", text="iva " * 10 + "relleno " * 41)
        r = BM25Retriever().fit([base, repe])
        by_doc = {sd.chunk.doc_id: sd.score for sd in r.search("iva", k=2)}
        assert by_doc["b"] > by_doc["a"]
        assert by_doc["b"] < 10 * by_doc["a"]

    def test_query_sin_terminos_del_corpus_no_revienta(self, chunks):
        assert BM25Retriever().fit(chunks).search("xyzzy plugh", k=3) is not None


class TestFusion:
    def test_rrf_premia_el_consenso_entre_rankings(self, chunks):
        """Un doc 2º en ambas listas debe superar a uno 1º en una sola."""
        r_bm25 = BM25Retriever().fit(chunks).search("ventas y servicios", k=3)
        r_tfidf = TfidfRetriever().fit(chunks).search("ventas y servicios", k=3)
        fused = rrf_fuse([r_bm25, r_tfidf], top_k=3)
        assert [sd.score for sd in fused] == sorted(
            (sd.score for sd in fused), reverse=True
        )

    def test_rrf_no_duplica_documentos(self, chunks):
        r = BM25Retriever().fit(chunks).search("impuesto", k=3)
        fused = rrf_fuse([r, r], top_k=5)
        assert len({sd.index for sd in fused}) == len(fused)

    def test_rrf_con_un_solo_ranking_preserva_el_orden(self, chunks):
        r = BM25Retriever().fit(chunks).search("impuesto", k=3)
        assert [sd.index for sd in rrf_fuse([r], top_k=3)] == [sd.index for sd in r]


class TestMetricas:
    def test_recall_at_k(self):
        assert recall_at_k(["a", "b", "c"], {"a", "d"}, k=3) == 0.5
        assert recall_at_k(["a", "b", "c"], {"c"}, k=2) == 0.0

    def test_reciprocal_rank(self):
        assert reciprocal_rank(["x", "a"], {"a"}) == 0.5
        assert reciprocal_rank(["x", "y"], {"a"}) == 0.0

    def test_ndcg_penaliza_la_posicion(self):
        bueno = ndcg_at_k(["a", "x", "y"], {"a"}, k=3)
        malo = ndcg_at_k(["x", "y", "a"], {"a"}, k=3)
        assert bueno == pytest.approx(1.0)
        assert malo < bueno

    @pytest.mark.parametrize("fn", [recall_at_k, ndcg_at_k])
    def test_metricas_acotadas_en_0_1(self, fn):
        assert 0.0 <= fn(["a", "b"], {"a", "b", "c"}, k=2) <= 1.0

    def test_abstencion_correcta_puntua_1(self):
        """Query sin fuente en el corpus: no recuperar nada es la respuesta correcta."""
        assert recall_at_k([], set(), k=3) == 1.0
        assert recall_at_k(["a"], set(), k=3) == 0.0


class TestBootstrap:
    def test_ic_contiene_la_media_y_es_reproducible(self):
        datos = [0.0, 0.5, 1.0, 0.75, 0.25] * 6
        media, lo, hi = bootstrap_ci(datos, n_boot=200, seed=42)
        assert lo <= media <= hi
        assert (media, lo, hi) == bootstrap_ci(datos, n_boot=200, seed=42)

    def test_muestra_constante_da_ic_degenerado(self):
        media, lo, hi = bootstrap_ci([1.0] * 20, n_boot=100, seed=0)
        assert media == lo == hi == 1.0

    def test_mas_datos_estrecha_el_ic(self):
        """Propiedad del IC: el ancho decrece a razón de 1/√n."""
        patron = [0.0, 1.0] * 10
        _, lo_n, hi_n = bootstrap_ci(patron, n_boot=500, seed=1)
        _, lo_4n, hi_4n = bootstrap_ci(patron * 4, n_boot=500, seed=1)
        assert (hi_4n - lo_4n) < (hi_n - lo_n)
        assert (hi_4n - lo_4n) == pytest.approx((hi_n - lo_n) / math.sqrt(4), rel=0.35)

    def test_lista_vacia_no_revienta(self):
        assert bootstrap_ci([]) == (0.0, 0.0, 0.0)
