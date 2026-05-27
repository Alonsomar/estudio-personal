"""Librería de retrieval desde cero para la masterclass 02-retrieval.

Contiene implementaciones propias (sin frameworks de búsqueda) de:
- Tokenización para español jurídico-técnico chileno (preserva referencias
  normativas tipo "21.210" y montos tipo "1,694").
- TF-IDF con similitud coseno.
- BM25 (Okapi) con saturación de term-frequency y normalización por longitud.

Diseñada para crecer: en secciones posteriores se agregan fusión (RRF),
rerankers y utilidades de chunking. Se importa desde los scripts demo:

    from retrieval_lib import BM25Retriever, TfidfRetriever, tokenize

Se ejecuta vía `uv run python 02-retrieval/code/01-ir-clasico.py` (el directorio
del script queda en sys.path, así que el import funciona sin instalación).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

# Stopwords del español. Lista deliberadamente conservadora: en dominio legal,
# palabras como "no", "sin" o "menor" cambian el sentido, así que NO se filtran.
# Solo se eliminan conectores de altísima frecuencia y bajo poder discriminante.
STOPWORDS_ES: frozenset[str] = frozenset(
    """
    a al algo algunas algunos ante antes como con contra cual cuando de del desde
    donde durante e el ella ellas ellos en entre era erais eran eras es esa esas
    ese eso esos esta estas este esto estos fue fueron ha han hasta hay la las le
    les lo los mas me mi mis mucho muy nos o os otra otras otro otros para pero por
    porque que quien se sea ser si sin so sobre solo son su sus tan te tu tus un una
    uno unos y ya
    """.split()
)


def strip_accents(text: str) -> str:
    """Quita diacríticos: 'artículo' -> 'articulo', 'Nº' -> 'n'.

    Normaliza la brecha entre cómo escribe el usuario una query y cómo aparece
    el término en la norma. En español jurídico ('artículo', 'período') la
    tilde rara vez distingue significado, así que normalizar mejora el matching.

    Detalle sutil: los indicadores ordinales 'º'/'ª' (como en 'Nº' o
    'Artículo 1º') se descomponen a 'o'/'a' bajo NFKD, lo que haría que 'Nº'
    colisione con la palabra 'no' (negación, que en texto legal SÍ importa).
    Los eliminamos antes de normalizar: 'Nº 21.210' -> 'n 21.210', no 'no 21.210'.
    """
    text = text.replace("º", "").replace("ª", "")  # º, ª (ordinales)
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


# Captura palabras y números legales con punto/coma internos: "21.210", "1,694".
# El separador interno solo se conserva si está rodeado de dígitos/letras, de modo
# que "Ley Nº 21.210." no arrastra el punto final.
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.,][a-z0-9]+)*")


def tokenize(text: str, *, drop_stopwords: bool = True) -> list[str]:
    """Tokeniza texto a una lista de términos normalizados.

    Pasos: minúsculas -> sin acentos -> extracción de tokens alfanuméricos
    (preservando refs tipo "21.210") -> filtrado opcional de stopwords.
    """
    norm = strip_accents(text.lower())
    tokens = _TOKEN_RE.findall(norm)
    # Descartamos tokens de un solo carácter: residuos de ordinales ('Nº'->'n'),
    # marcadores de lista ('a)', 'b)') y dígitos sueltos no aportan señal de ranking.
    tokens = [t for t in tokens if len(t) > 1]
    if drop_stopwords:
        tokens = [t for t in tokens if t not in STOPWORDS_ES]
    return tokens


@dataclass
class ScoredDoc:
    """Resultado de retrieval: índice del doc, score y referencia al chunk."""

    index: int
    score: float
    chunk: "Chunk | None" = None


@dataclass
class Chunk:
    """Fragmento indexable con metadata mínima de procedencia."""

    chunk_id: str
    doc_id: str
    text: str
    meta: dict = field(default_factory=dict)


class TfidfRetriever:
    """Retriever TF-IDF con similitud coseno, implementado desde cero.

    Esquema:
      tf  = frecuencia bruta del término en el doc (luego se normaliza el vector)
      idf = log(N / df) + 1   (idf suavizado estilo scikit-learn)
      peso(t, d) = tf(t, d) * idf(t)
    Los vectores se normalizan a norma L2, de modo que el score coseno es el
    producto punto entre el vector de la query y el del documento.
    """

    def __init__(self) -> None:
        self.vocab: dict[str, int] = {}
        self.idf: list[float] = []
        self.doc_vectors: list[dict[int, float]] = []  # disperso: {term_id: peso}
        self.chunks: list[Chunk] = []

    def fit(self, chunks: list[Chunk]) -> "TfidfRetriever":
        self.chunks = chunks
        tokenized = [tokenize(c.text) for c in chunks]
        n_docs = len(chunks)

        # Vocabulario y document frequency.
        df: Counter[str] = Counter()
        for toks in tokenized:
            for term in set(toks):
                df[term] += 1
        self.vocab = {term: i for i, term in enumerate(sorted(df))}

        # IDF suavizado: log(N / df) + 1. El +1 evita que un término presente
        # en todos los docs (idf=0) anule por completo su contribución.
        self.idf = [0.0] * len(self.vocab)
        for term, idx in self.vocab.items():
            self.idf[idx] = math.log(n_docs / df[term]) + 1.0

        # Vectores tf-idf normalizados (L2).
        self.doc_vectors = [self._vectorize(toks) for toks in tokenized]
        return self

    def _vectorize(self, tokens: list[str]) -> dict[int, float]:
        tf = Counter(tokens)
        vec: dict[int, float] = {}
        for term, freq in tf.items():
            idx = self.vocab.get(term)
            if idx is None:
                continue
            vec[idx] = freq * self.idf[idx]
        norm = math.sqrt(sum(w * w for w in vec.values()))
        if norm > 0:
            for idx in vec:
                vec[idx] /= norm
        return vec

    def search(self, query: str, k: int = 5) -> list[ScoredDoc]:
        qvec = self._vectorize(tokenize(query))
        scores: list[ScoredDoc] = []
        for i, dvec in enumerate(self.doc_vectors):
            # Producto punto sobre el vocabulario más pequeño de los dos.
            small, large = (qvec, dvec) if len(qvec) < len(dvec) else (dvec, qvec)
            dot = sum(w * large.get(idx, 0.0) for idx, w in small.items())
            if dot > 0:
                scores.append(ScoredDoc(index=i, score=dot, chunk=self.chunks[i]))
        scores.sort(key=lambda s: s.score, reverse=True)
        return scores[:k]


class BM25Retriever:
    """BM25 (Okapi) implementado desde cero.

    score(q, d) = Σ_t idf(t) · ( f(t,d)·(k1+1) ) / ( f(t,d) + k1·(1 - b + b·|d|/avgdl) )

    Dos ideas que TF-IDF no tiene y que explican por qué BM25 sigue siendo
    el baseline a batir en 2026:
      1. Saturación (k1): repetir un término 10 veces no vale 10x. La ganancia
         marginal decrece, igual que la utilidad marginal en economía.
      2. Normalización por longitud (b): un documento largo no gana ventaja
         solo por contener más palabras.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.chunks: list[Chunk] = []
        self.doc_tf: list[Counter[str]] = []
        self.doc_len: list[int] = []
        self.avgdl: float = 0.0
        self.idf: dict[str, float] = {}

    def fit(self, chunks: list[Chunk]) -> "BM25Retriever":
        self.chunks = chunks
        self.doc_tf = [Counter(tokenize(c.text)) for c in chunks]
        self.doc_len = [sum(tf.values()) for tf in self.doc_tf]
        n_docs = len(chunks)
        self.avgdl = sum(self.doc_len) / n_docs if n_docs else 0.0

        df: Counter[str] = Counter()
        for tf in self.doc_tf:
            for term in tf:
                df[term] += 1

        # IDF de BM25 con suavizado de Robertson. El +0.5/+0.5 y el +1 dentro del
        # log mantienen el idf positivo incluso para términos muy frecuentes.
        self.idf = {
            term: math.log(1 + (n_docs - d + 0.5) / (d + 0.5)) for term, d in df.items()
        }
        return self

    def _score_doc(self, q_terms: list[str], i: int) -> float:
        tf = self.doc_tf[i]
        dl = self.doc_len[i]
        denom_norm = self.k1 * (1 - self.b + self.b * dl / self.avgdl)
        score = 0.0
        for term in q_terms:
            f = tf.get(term, 0)
            if f == 0:
                continue
            idf = self.idf.get(term, 0.0)
            score += idf * (f * (self.k1 + 1)) / (f + denom_norm)
        return score

    def search(self, query: str, k: int = 5) -> list[ScoredDoc]:
        q_terms = tokenize(query)
        scores: list[ScoredDoc] = []
        for i in range(len(self.chunks)):
            s = self._score_doc(q_terms, i)
            if s > 0:
                scores.append(ScoredDoc(index=i, score=s, chunk=self.chunks[i]))
        scores.sort(key=lambda s: s.score, reverse=True)
        return scores[:k]

    def explain(self, query: str, doc_index: int) -> list[tuple[str, float]]:
        """Devuelve la contribución por término al score de un doc (para didáctica)."""
        tf = self.doc_tf[doc_index]
        dl = self.doc_len[doc_index]
        denom_norm = self.k1 * (1 - self.b + self.b * dl / self.avgdl)
        contribs: list[tuple[str, float]] = []
        for term in tokenize(query):
            f = tf.get(term, 0)
            if f == 0:
                continue
            idf = self.idf.get(term, 0.0)
            contribs.append((term, idf * (f * (self.k1 + 1)) / (f + denom_norm)))
        contribs.sort(key=lambda x: x[1], reverse=True)
        return contribs
