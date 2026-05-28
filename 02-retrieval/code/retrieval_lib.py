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

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

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


# --------------------------------------------------------------------------- #
# Carga y chunking del corpus (compartido entre demos desde la sección 2).
# --------------------------------------------------------------------------- #
def simple_chunk(text: str, doc_id: str) -> list[Chunk]:
    """Chunking por bloques separados por línea en blanco.

    Deliberadamente ingenuo: el chunking serio es la sección 4. Sirve como
    unidad indexable uniforme para comparar retrievers en las secciones 1-3.
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    return [
        Chunk(chunk_id=f"{doc_id}#{i}", doc_id=doc_id, text=block)
        for i, block in enumerate(blocks)
    ]


def load_corpus_chunks(
    corpus_dir: Path,
    filenames: list[str] | None = None,
    chunker=simple_chunk,
) -> list[Chunk]:
    """Carga el corpus y lo chunkea con la estrategia indicada (por defecto, simple)."""
    files = (
        sorted(corpus_dir.glob("*.txt"))
        if filenames is None
        else [corpus_dir / f for f in filenames]
    )
    chunks: list[Chunk] = []
    for path in files:
        chunks.extend(chunker(path.read_text(encoding="utf-8"), doc_id=path.name))
    return chunks


def fixed_chunk(
    text: str, doc_id: str, size: int = 400, overlap: int = 50
) -> list[Chunk]:
    """Chunking de tamaño fijo en caracteres, con ventana deslizante.

    El más ingenuo. Útil como baseline y cuando no hay estructura confiable. Las
    fronteras caen donde caigan: a media frase, a medio artículo, a media tabla.
    """
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("size > 0 y 0 <= overlap < size")
    step = size - overlap
    out: list[Chunk] = []
    i = 0
    j = 0
    n = len(text)
    while i < n:
        block = text[i : i + size].strip()
        if block:
            out.append(Chunk(chunk_id=f"{doc_id}#f{j}", doc_id=doc_id, text=block))
            j += 1
        i += step
    return out


# Encabezados típicos del español jurídico chileno: artículos, glosas, títulos,
# párrafos, capítulos romanos, y numerales tipo "I.", "II.", "III.", "IV.".
_LEGAL_HEADER = re.compile(
    r"(?m)^(?:"
    r"Artículo\s+\d+[ºo]?\.?-?|"
    r"Glosa\s+\d+:|"
    r"TÍTULO\s+[IVXLCDM]+|"
    r"CAPÍTULO\s+\d+|"
    r"PÁRRAFO\s+\d+[ºo]?|"
    r"PROGRAMA\s+\d+|"
    r"[IVX]+\.\s+[A-ZÁÉÍÓÚÑ]"
    r")"
)


def structural_chunk(text: str, doc_id: str) -> list[Chunk]:
    """Chunking por encabezados del dominio (Artículo, Glosa, Título, etc.).

    Respeta las unidades semánticas del documento legal: un artículo entero queda
    en un solo chunk, una glosa entera también. Resultados: chunks más largos y
    menos fragmentados que la división por línea en blanco.
    """
    matches = list(_LEGAL_HEADER.finditer(text))
    if not matches:
        return simple_chunk(text, doc_id)
    boundaries = [m.start() for m in matches] + [len(text)]
    out: list[Chunk] = []
    # Preámbulo antes del primer encabezado, si existe.
    pre = text[: boundaries[0]].strip()
    if pre:
        out.append(Chunk(chunk_id=f"{doc_id}#st0", doc_id=doc_id, text=pre))
    for k in range(len(matches)):
        block = text[boundaries[k] : boundaries[k + 1]].strip()
        if block:
            out.append(
                Chunk(chunk_id=f"{doc_id}#st{k + 1}", doc_id=doc_id, text=block)
            )
    return out


# Segmentador de oraciones razonable para español jurídico: corta tras '.', '?',
# '!' seguidos de espacio + mayúscula o salto de línea. Evita partir referencias
# numéricas tipo "21.210" porque exige espacio antes de la mayúscula.
_SENT_END = re.compile(r"(?<=[\.\?\!])\s+(?=[A-ZÁÉÍÓÚÑ])|\n+")


def sentence_split(text: str) -> list[str]:
    return [s.strip() for s in _SENT_END.split(text) if s.strip()]


def semantic_chunk(
    text: str,
    doc_id: str,
    embedder: "OpenAIEmbedder",
    threshold: float = 0.55,
) -> list[Chunk]:
    """Chunking semántico: corta donde la similitud entre oraciones consecutivas cae.

    Cada oración se embebe; al recorrer en orden, se inicia un nuevo chunk
    cuando cos(oración_i, oración_i-1) < threshold. Idea: mantener juntas las
    oraciones que hablan de lo mismo, separar cambios de tema.
    """
    sents = sentence_split(text)
    if len(sents) <= 1:
        return [Chunk(chunk_id=f"{doc_id}#sm0", doc_id=doc_id, text=text.strip())]
    vecs = _l2_normalize(embedder.embed(sents))
    out: list[Chunk] = []
    current: list[str] = [sents[0]]
    cidx = 0
    for i in range(1, len(sents)):
        sim = float(vecs[i] @ vecs[i - 1])
        if sim < threshold and current:
            out.append(
                Chunk(
                    chunk_id=f"{doc_id}#sm{cidx}",
                    doc_id=doc_id,
                    text=" ".join(current),
                )
            )
            cidx += 1
            current = [sents[i]]
        else:
            current.append(sents[i])
    if current:
        out.append(
            Chunk(chunk_id=f"{doc_id}#sm{cidx}", doc_id=doc_id, text=" ".join(current))
        )
    return out


def hierarchical_chunk(text: str, doc_id: str) -> list[Chunk]:
    """Chunking jerárquico parent/child para retrieval con expansión a contexto.

    Padre = bloque estructural (Artículo, Glosa, sección). Hijo = una oración del
    padre. Los hijos son lo que se indexa/recupera; el campo meta['parent_text']
    se entrega al generador. Combinas precisión de recuperación (chunks chicos)
    con contexto suficiente (devolver el padre).
    """
    parents = structural_chunk(text, doc_id)
    children: list[Chunk] = []
    for p_idx, parent in enumerate(parents):
        sents = sentence_split(parent.text)
        if not sents:
            sents = [parent.text]
        for c_idx, sent in enumerate(sents):
            children.append(
                Chunk(
                    chunk_id=f"{doc_id}#h{p_idx}.{c_idx}",
                    doc_id=doc_id,
                    text=sent,
                    meta={"parent_id": parent.chunk_id, "parent_text": parent.text},
                )
            )
    return children


# Mapa doc_id -> contexto breve, para la aproximación a "late chunking" (ver §4).
# La verdadera late chunking exige acceso a embeddings token-level (no expuestos
# por la API de OpenAI); aquí simulamos el espíritu prependiendo contexto del
# documento a cada chunk antes de embeberlo (aka contextual chunking).
DOC_CONTEXT: dict[str, str] = {
    "circular-01-sii-iva-digital.txt": "Circular SII 42/2020 sobre IVA a servicios digitales extranjeros (Ley 21.210).",
    "circular-02-sii-renta-propyme.txt": "Circular SII 62/2020 sobre el Régimen Pro Pyme de Impuesto a la Renta.",
    "circular-03-sii-ppm-honorarios.txt": "Circular SII 50/2022 sobre retención y PPM de boletas de honorarios.",
    "circular-04-sii-iva-exenciones.txt": "Circular SII 17/2021 sobre exenciones de IVA en salud y educación.",
    "decreto-01-subvencion-escolar.txt": "Decreto Exento 1.423 reglamenta la Ley 20.248 de Subvención Escolar Preferencial.",
    "decreto-02-reglamento-ley-lobby.txt": "Decreto Supremo 71/2014, reglamento de la Ley 20.730 de Lobby.",
    "do-01-extracto-decreto-aranceles.txt": "Diario Oficial: aduanas, valor de la USE 2024 y toma de razón.",
    "glosa-01-presupuesto-salud.txt": "Ley de Presupuestos 2024 Partida 16 Ministerio de Salud (inmunizaciones, PRAIS, FONASA).",
    "glosa-02-presupuesto-educacion.txt": "Ley de Presupuestos 2024 Partida 09 Ministerio de Educación (SEP, JUNAEB).",
    "glosa-03-presupuesto-trabajo.txt": "Ley de Presupuestos 2024 Partida 15 Trabajo (Subsidios al Empleo Joven y de la Mujer).",
    "ley-01-dl-825-iva-base.txt": "DL 825 Ley sobre Impuesto a las Ventas y Servicios, texto previo a la Ley 21.210.",
    "ley-02-ley-21210-modernizacion.txt": "Ley 21.210 de Modernización Tributaria (IVA digital, Pro Pyme, boleta electrónica).",
    "norma-01-ley-lobby.txt": "Ley 20.730 que regula el Lobby y las gestiones de intereses particulares.",
    "norma-02-ley-20880-probidad.txt": "Ley 20.880 sobre Probidad y declaración de intereses y patrimonio.",
    "oficio-01-contraloria-subvenciones.txt": "Dictamen Contraloría 8.452/2023 sobre rendición de la Subvención Escolar Preferencial.",
    "tabla-01-valores-tributarios-2024.txt": "Tabla de valores UTM, UTA y UF mensuales para el año 2024.",
}


def contextual_chunk(text: str, doc_id: str, base_chunker=structural_chunk) -> list[Chunk]:
    """Aproximación a 'late chunking': cada chunk lleva al inicio una breve
    descripción del documento, para que su embedding 'sepa' de qué doc viene.

    No es late chunking real (eso exige token embeddings del doc completo, no
    disponibles en la API de OpenAI). Es la técnica de 'contextual chunking'
    (Anthropic, 2024), que aborda el mismo problema (chunks descontextualizados)
    desde otro ángulo y se puede aplicar sobre cualquier embedder.
    """
    base = base_chunker(text, doc_id)
    ctx = DOC_CONTEXT.get(doc_id, "")
    if not ctx:
        return base
    return [
        Chunk(
            chunk_id=f"{c.chunk_id}#ctx",
            doc_id=c.doc_id,
            text=f"[{ctx}] {c.text}",
            meta=c.meta,
        )
        for c in base
    ]


# --------------------------------------------------------------------------- #
# Retrieval denso: embeddings vía OpenAI con caché en disco + similitud coseno.
# --------------------------------------------------------------------------- #
class OpenAIEmbedder:
    """Embeddings de OpenAI con caché en disco (.npz).

    La caché hace que las corridas sean gratis y reproducibles tras la primera:
    cada texto se indexa por hash(modelo + texto), así que cambiar el corpus solo
    re-embeddea lo nuevo. Si la caché se versiona, el repo corre sin API key.
    """

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        cache_path: Path | None = None,
        batch_size: int = 256,
    ) -> None:
        self.model = model
        self.cache_path = Path(cache_path) if cache_path else None
        self.batch_size = batch_size
        self._keys: dict[str, int] = {}
        self._matrix: np.ndarray | None = None
        self.api_calls = 0  # para reportar cuántas llamadas reales se hicieron
        self._load()

    def _key(self, text: str) -> str:
        return hashlib.sha1(f"{self.model}\n{text}".encode("utf-8")).hexdigest()

    def _load(self) -> None:
        if self.cache_path and self.cache_path.exists():
            data = np.load(self.cache_path, allow_pickle=True)
            self._matrix = data["matrix"]
            self._keys = {k: i for i, k in enumerate(data["keys"].tolist())}

    def _save(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        keys = [""] * len(self._keys)
        for k, i in self._keys.items():
            keys[i] = k
        np.savez_compressed(
            self.cache_path, keys=np.array(keys, dtype=object), matrix=self._matrix
        )

    def embed(self, texts: list[str]) -> np.ndarray:
        """Devuelve una matriz (len(texts), dim) alineada al orden de entrada."""
        missing = list(dict.fromkeys(t for t in texts if self._key(t) not in self._keys))
        if missing:
            from dotenv import load_dotenv

            load_dotenv()  # toma OPENAI_API_KEY del .env del proyecto
            from openai import OpenAI

            client = OpenAI()
            vecs: list[list[float]] = []
            for i in range(0, len(missing), self.batch_size):
                batch = missing[i : i + self.batch_size]
                resp = client.embeddings.create(model=self.model, input=batch)
                self.api_calls += 1
                vecs.extend(d.embedding for d in resp.data)
            new = np.array(vecs, dtype=np.float32)
            start = 0 if self._matrix is None else self._matrix.shape[0]
            self._matrix = new if self._matrix is None else np.vstack([self._matrix, new])
            for j, t in enumerate(missing):
                self._keys[self._key(t)] = start + j
            self._save()
        idx = [self._keys[self._key(t)] for t in texts]
        return self._matrix[idx]


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return matrix / (norms + 1e-12)


class DenseRetriever:
    """Retriever denso: coseno entre el embedding de la query y los del corpus."""

    def __init__(self, embedder: OpenAIEmbedder) -> None:
        self.embedder = embedder
        self.chunks: list[Chunk] = []
        self.matrix: np.ndarray | None = None  # (n, dim), normalizada L2

    def fit(self, chunks: list[Chunk]) -> "DenseRetriever":
        self.chunks = chunks
        self.matrix = _l2_normalize(self.embedder.embed([c.text for c in chunks]))
        return self

    def search(self, query: str, k: int = 5) -> list[ScoredDoc]:
        q = _l2_normalize(self.embedder.embed([query]))[0]
        sims = self.matrix @ q  # coseno (vectores normalizados)
        order = np.argsort(-sims)[:k]
        return [
            ScoredDoc(index=int(i), score=float(sims[i]), chunk=self.chunks[int(i)])
            for i in order
        ]


def pca_2d(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Proyección PCA a 2D desde cero, vía SVD.

    Devuelve (proyección (n, 2), varianza explicada de las 2 componentes).
    PCA = centrar los datos y quedarse con las direcciones de máxima varianza,
    que son los primeros vectores singulares por la derecha (Vt) de X centrada.
    """
    X = matrix - matrix.mean(axis=0, keepdims=True)
    _, S, Vt = np.linalg.svd(X, full_matrices=False)
    proj = X @ Vt[:2].T
    explained = (S**2) / (S**2).sum()
    return proj, explained[:2]


# --------------------------------------------------------------------------- #
# Hybrid search: fusión de rankings sparse + dense (RRF y ponderada).
# --------------------------------------------------------------------------- #
def rrf_fuse(
    rankings: list[list[ScoredDoc]], k: int = 60, top_k: int = 10
) -> list[ScoredDoc]:
    """Reciprocal Rank Fusion, desde cero.

    score_RRF(d) = Σ_r 1 / (k + rank_r(d))

    Fusiona *rankings*, no scores: solo importa la posición de cada doc en cada
    lista, no su score crudo (que entre BM25 y coseno es incomparable). La
    constante k amortigua el peso de las primeras posiciones; el estándar es 60.
    Un doc ausente de un ranking simplemente no suma por esa vía.
    """
    scores: dict[int, float] = {}
    ref: dict[int, ScoredDoc] = {}
    for ranking in rankings:
        for rank, sd in enumerate(ranking, start=1):
            scores[sd.index] = scores.get(sd.index, 0.0) + 1.0 / (k + rank)
            ref[sd.index] = sd
    fused = [ScoredDoc(index=i, score=s, chunk=ref[i].chunk) for i, s in scores.items()]
    fused.sort(key=lambda s: s.score, reverse=True)
    return fused[:top_k]


def weighted_fuse(
    rankings: list[list[ScoredDoc]], weights: list[float], top_k: int = 10
) -> list[ScoredDoc]:
    """Fusión por combinación convexa de scores normalizados (min-max) a [0,1].

    score(d) = Σ_r w_r · normalizado_r(score_r(d))

    A diferencia de RRF, sí usa los scores, así que exige normalizarlos a una
    escala común. Su talón de Aquiles: el min-max hace que el PEOR resultado de
    cada lista valga 0 sin importar su calidad absoluta, y requiere elegir los
    pesos (hiperparámetro). Por eso RRF suele ser el default robusto.
    """
    refs: dict[int, ScoredDoc] = {}
    combined: dict[int, float] = {}
    for w, ranking in zip(weights, rankings):
        if not ranking:
            continue
        ss = [sd.score for sd in ranking]
        lo, hi = min(ss), max(ss)
        rng = (hi - lo) or 1.0
        for sd in ranking:
            refs[sd.index] = sd
            combined[sd.index] = combined.get(sd.index, 0.0) + w * (sd.score - lo) / rng
    fused = [ScoredDoc(index=i, score=s, chunk=refs[i].chunk) for i, s in combined.items()]
    fused.sort(key=lambda s: s.score, reverse=True)
    return fused[:top_k]


class HybridRetriever:
    """Combina varios retrievers fusionando sus rankings (RRF o ponderada).

    Recupera un pool de cada retriever base y fusiona. `method="rrf"` no tiene
    hiperparámetros relevantes; `method="weighted"` requiere `weights`.
    """

    def __init__(
        self,
        retrievers: list,
        method: str = "rrf",
        weights: list[float] | None = None,
        rrf_k: int = 60,
        pool: int = 20,
    ) -> None:
        self.retrievers = retrievers
        self.method = method
        self.weights = weights
        self.rrf_k = rrf_k
        self.pool = pool

    def search(self, query: str, k: int = 5) -> list[ScoredDoc]:
        rankings = [r.search(query, k=self.pool) for r in self.retrievers]
        if self.method == "rrf":
            return rrf_fuse(rankings, k=self.rrf_k, top_k=k)
        if self.method == "weighted":
            if self.weights is None:
                raise ValueError("method='weighted' requiere weights")
            return weighted_fuse(rankings, self.weights, top_k=k)
        raise ValueError(f"método de fusión desconocido: {self.method}")


# --------------------------------------------------------------------------- #
# Query rewriting: HyDE, multi-query, decomposition, step-back vía LLM.
# --------------------------------------------------------------------------- #
class LLMRewriter:
    """Cuatro estrategias de reescritura de queries con caché en disco.

    La caché (JSON) hace que tras la primera corrida no haya más llamadas al
    LLM: las reescrituras quedan fijas y reproducibles sin API key. Cambiar el
    modelo o el prompt cambia la clave de caché y vuelve a llamar.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        cache_path: Path | None = None,
        temperature: float = 0.0,
    ) -> None:
        self.model = model
        self.cache_path = Path(cache_path) if cache_path else None
        self.temperature = temperature
        self._cache: dict[str, str] = {}
        self.api_calls = 0
        self._load()

    def _load(self) -> None:
        if self.cache_path and self.cache_path.exists():
            self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _key(self, prompt: str) -> str:
        return hashlib.sha1(
            f"{self.model}\n{self.temperature}\n{prompt}".encode("utf-8")
        ).hexdigest()

    def _call(self, prompt: str) -> str:
        k = self._key(prompt)
        if k in self._cache:
            return self._cache[k]
        from dotenv import load_dotenv

        load_dotenv()
        from openai import OpenAI

        client = OpenAI()
        resp = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.temperature,
        )
        text = (resp.choices[0].message.content or "").strip()
        self._cache[k] = text
        self.api_calls += 1
        self._save()
        return text

    def hyde(self, query: str) -> list[str]:
        """HyDE (Gao et al., 2022): el LLM genera un párrafo que *respondería*
        la query como si fuera texto real de una norma. Se embebe ESE párrafo y
        se busca con él, cerrando la brecha de vocabulario query↔documento."""
        prompt = (
            "Eres un asistente especializado en normativa fiscal y "
            "regulatoria chilena. Escribe un párrafo de 2-4 frases en "
            "español jurídico-técnico que respondería la siguiente "
            "pregunta, como si fuera un fragmento real de un decreto, "
            "circular del SII, ley o glosa presupuestaria. No agregues "
            "introducción, citas ni comillas; solo el párrafo.\n\n"
            f"Pregunta: {query}\n\nPárrafo:"
        )
        return [self._call(prompt)]

    def multi_query(self, query: str, n: int = 4) -> list[str]:
        """Multi-query: el LLM produce n reformulaciones; se buscan todas y
        se fusionan con RRF. Cubre la vecindad parafrástica del original."""
        prompt = (
            f"Reformula la siguiente pregunta sobre normativa fiscal o "
            f"regulatoria chilena de {n} formas distintas, manteniendo el "
            "sentido pero variando vocabulario y estructura. Devuelve solo "
            "las reformulaciones, una por línea, sin numerar ni viñetas.\n\n"
            f"Pregunta original: {query}"
        )
        text = self._call(prompt)
        variants = [line.strip(" -•\t") for line in text.splitlines() if line.strip()]
        # Incluimos el original para no perderlo si las reformulaciones se desvían.
        return [query] + variants[:n]

    def decompose(self, query: str) -> list[str]:
        """Decomposition: si la query exige consultar varias fuentes (multi-hop),
        el LLM la parte en sub-preguntas más simples."""
        prompt = (
            "Si la siguiente pregunta sobre normativa chilena requiere "
            "consultar varias fuentes distintas (multi-hop), descomponla "
            "en sub-preguntas simples, una por aspecto o norma. Si la "
            "pregunta NO requiere descomposición, devuelve solo la "
            "pregunta original. Una por línea, sin numerar ni viñetas.\n\n"
            f"Pregunta: {query}"
        )
        text = self._call(prompt)
        parts = [line.strip(" -•\t") for line in text.splitlines() if line.strip()]
        return parts or [query]

    def step_back(self, query: str) -> list[str]:
        """Step-back (Zheng et al., 2023): el LLM genera una pregunta MÁS
        general/abstracta para anclar el contexto antes de la concreta."""
        prompt = (
            "Genera una pregunta más general y abstracta sobre el régimen "
            "normativo o el área de política pública al que pertenece la "
            "siguiente pregunta concreta. Devuelve solo la pregunta "
            "general, una sola línea sin numerar.\n\n"
            f"Pregunta concreta: {query}"
        )
        general = (self._call(prompt).splitlines() or [""])[0].strip(" -•\t")
        return [query, general] if general else [query]


class RewrittenRetriever:
    """Envuelve un retriever base con una estrategia de reescritura.

    Llama a `rewrite_fn(query)` para obtener una o más queries, busca cada una
    en el base con profundidad `pool`, y fusiona los rankings con RRF.
    """

    def __init__(self, base, rewrite_fn, pool: int = 20, rrf_k: int = 60) -> None:
        self.base = base
        self.rewrite_fn = rewrite_fn
        self.pool = pool
        self.rrf_k = rrf_k

    def search(self, query: str, k: int = 5) -> list[ScoredDoc]:
        queries = self.rewrite_fn(query)
        rankings = [self.base.search(q, k=self.pool) for q in queries]
        return rrf_fuse(rankings, k=self.rrf_k, top_k=k)
