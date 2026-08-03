"""econ_lib — aritmética de la economía de inferencia para la masterclass 04.

Núcleo reutilizable que acumula lo que las secciones introducen:

  §1  Aritmética de un modelo transformer denso: parámetros, bytes por token
      generado, tamaño del KV cache, techos de prefill y decode.
  §2  Batching: throughput agregado, latencia por secuencia, costo por millón
      de tokens y la cola M/M/1 que explica por qué el p95 explota cerca de
      la saturación.

Método (ver `theory/00-plan.md`): esto es un MODELO ANALÍTICO, no un benchmark.
Predice órdenes de magnitud y puntos de equilibrio. No modela el overhead real
de servir (scheduler, fragmentación de memoria, cold starts), que siempre empuja
en la misma dirección: la realidad rinde peor que el papel.

Todas las constantes de hardware vienen de fichas públicas del fabricante y
están marcadas con su fuente. Corre offline y determinista: sin GPU, sin red.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Constantes de hardware. Fuente: fichas técnicas públicas de NVIDIA.
# El ancho de banda es el número que gobierna el decode (§1); los TFLOPs
# gobiernan el prefill. Se listan ambos para poder razonar sobre las dos fases.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GPU:
    """Especificación de una GPU. `bf16_tflops` es el pico teórico denso."""

    name: str
    memory_gb: float
    bandwidth_tb_s: float      # TB/s de ancho de banda de HBM
    bf16_tflops: float         # TFLOP/s densos en bf16 (pico teórico)
    usd_per_hour: float        # precio de alquiler on-demand [dato estimado]

    @property
    def bandwidth_gb_s(self) -> float:
        return self.bandwidth_tb_s * 1000


# Specs de catálogo. El precio/hora es lo más volátil y se marca como estimado:
# varía por proveedor, región y compromiso de plazo (ver §5 sobre deriva).
GPUS: dict[str, GPU] = {
    # NVIDIA A100 80GB SXM: 80 GB HBM2e, ~2.0 TB/s, ~312 TFLOP/s bf16.
    "A100-80": GPU("A100 80GB", 80, 2.039, 312, usd_per_hour=1.80),
    # NVIDIA H100 80GB SXM: 80 GB HBM3, ~3.35 TB/s, ~990 TFLOP/s bf16 (con sparsity
    # el marketing dice 1979; usamos el número denso, que es el honesto).
    "H100-80": GPU("H100 80GB", 80, 3.35, 990, usd_per_hour=2.90),
}

BYTES_PER_DTYPE: dict[str, float] = {
    "fp32": 4.0,
    "bf16": 2.0,
    "fp8": 1.0,
    "int8": 1.0,
    "int4": 0.5,
}


@dataclass(frozen=True)
class ModelSpec:
    """Un transformer denso, descrito por lo mínimo que la aritmética necesita.

    Se usan modelos ABIERTOS de tamaño publicado: los tamaños de los modelos
    propietarios no son públicos y no se inventan. Las conclusiones son de forma
    funcional (cómo escala), no afirmaciones sobre un modelo comercial concreto.
    """

    name: str
    n_params_b: float          # parámetros, en miles de millones
    n_layers: int
    d_model: int
    n_heads: int
    n_kv_heads: int            # < n_heads si usa GQA (grouped-query attention)

    @property
    def head_dim(self) -> int:
        return self.d_model // self.n_heads

    def weights_gb(self, dtype: str = "bf16") -> float:
        """Memoria que ocupan los pesos. Es el piso: sin esto no arranca."""
        return self.n_params_b * 1e9 * BYTES_PER_DTYPE[dtype] / 1e9

    def kv_bytes_per_token(self, dtype: str = "bf16") -> float:
        """Bytes de KV cache por token de contexto, por secuencia.

        Cada capa guarda una clave y un valor por cabeza de KV:
            2 (K y V) · n_layers · n_kv_heads · head_dim · bytes_por_valor

        GQA es exactamente la optimización de este número: compartir cabezas de
        KV entre cabezas de atención divide el KV cache sin tocar la calidad de
        forma apreciable. Por eso todos los modelos modernos lo usan.
        """
        return (
            2 * self.n_layers * self.n_kv_heads * self.head_dim
            * BYTES_PER_DTYPE[dtype]
        )

    def kv_cache_gb(self, context_tokens: int, batch: int = 1, dtype: str = "bf16") -> float:
        """KV cache total para `batch` secuencias de `context_tokens` de largo."""
        return self.kv_bytes_per_token(dtype) * context_tokens * batch / 1e9


# Modelos abiertos de tamaño publicado, usados como referencia de forma funcional.
MODELS: dict[str, ModelSpec] = {
    # Llama 3.1 8B: 32 capas, d_model 4096, 32 cabezas, 8 cabezas KV (GQA 4:1).
    "8B": ModelSpec("clase 8B", 8.0, n_layers=32, d_model=4096, n_heads=32, n_kv_heads=8),
    # Llama 3.1 70B: 80 capas, d_model 8192, 64 cabezas, 8 cabezas KV (GQA 8:1).
    "70B": ModelSpec("clase 70B", 70.0, n_layers=80, d_model=8192, n_heads=64, n_kv_heads=8),
}


# --------------------------------------------------------------------------- #
# §1 Las dos fases. Prefill procesa TODO el prompt en paralelo (limitado por
# cómputo); decode genera de a un token (limitado por ancho de banda, porque
# hay que releer todos los pesos para producir cada token).
# --------------------------------------------------------------------------- #
def prefill_flops(model: ModelSpec, prompt_tokens: int) -> float:
    """FLOPs del prefill. Regla estándar: ~2 FLOPs por parámetro por token.

    (Multiplicar-acumular = 2 operaciones; cada parámetro participa una vez por
    token. Ignora la atención cuadrática, despreciable a contextos cortos.)
    """
    return 2 * model.n_params_b * 1e9 * prompt_tokens


def prefill_seconds(model: ModelSpec, prompt_tokens: int, gpu: GPU, mfu: float = 0.4) -> float:
    """Tiempo de prefill. `mfu` = Model FLOPs Utilization: qué fracción del pico
    teórico se logra en la práctica. 0.3-0.5 es el rango realista en servido;
    usamos 0.4. [dato estimado]"""
    return prefill_flops(model, prompt_tokens) / (gpu.bf16_tflops * 1e12 * mfu)


def decode_bytes_per_token(model: ModelSpec, dtype: str = "bf16") -> float:
    """Bytes que hay que MOVER desde HBM para generar UN token.

    Esta es la línea clave de toda la masterclass: para producir un solo token
    hay que releer los pesos completos del modelo. No importa cuán rápida sea la
    GPU calculando: el cuello es traer los pesos desde memoria.
    """
    return model.n_params_b * 1e9 * BYTES_PER_DTYPE[dtype]


def decode_tokens_per_second(
    model: ModelSpec, gpu: GPU, dtype: str = "bf16", efficiency: float = 0.7, batch: int = 1
) -> float:
    """Techo de generación, limitado por ancho de banda.

        tokens/s = ancho_de_banda / bytes_por_token · eficiencia · batch

    El `batch` multiplica porque los pesos se leen UNA vez y sirven a todas las
    secuencias del batch: es la economía de escala de §2. `efficiency` descuenta
    lo que el pico teórico no entrega (0.6-0.8 realista). [dato estimado]
    """
    per_token = decode_bytes_per_token(model, dtype)
    return (gpu.bandwidth_gb_s * 1e9 / per_token) * efficiency * batch


def arithmetic_intensity_ratio(model: ModelSpec, prompt_tokens: int, output_tokens: int) -> float:
    """Cuántas veces más trabajo por token hace el decode que el prefill.

    Prefill amortiza una lectura de pesos entre `prompt_tokens` tokens; decode
    paga una lectura completa por CADA token. De ahí sale, en el modelo, la
    asimetría de precio input/output que cobran los proveedores.
    """
    del output_tokens  # la asimetría no depende de cuántos se generen
    return float(prompt_tokens)


# --------------------------------------------------------------------------- #
# §2 Batching. Los pesos se leen UNA vez por paso y sirven a todas las
# secuencias del batch: el costo fijo de mover pesos se reparte. Es una economía
# de escala literal, y explica por qué la inferencia se vende y no se regala.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BatchPoint:
    """Un punto de la curva throughput-latencia para un tamaño de batch."""

    batch: int
    tokens_per_s_per_seq: float
    tokens_per_s_total: float
    ms_per_token_per_seq: float
    usd_per_m_tokens: float


def batch_curve(
    model: ModelSpec,
    gpu: GPU,
    batch_sizes: tuple[int, ...],
    dtype: str = "bf16",
    efficiency: float = 0.7,
) -> list[BatchPoint]:
    """Curva throughput-latencia al variar el tamaño de batch.

    Modelo simplificado del régimen memory-bound: mientras el batch no sature el
    cómputo, el throughput AGREGADO crece ~linealmente con el batch y la latencia
    POR SECUENCIA se mantiene. Es el régimen donde vive el servido real de LLMs.

    El costo por millón de tokens cae con el batch porque la hora de GPU se
    reparte entre más tokens producidos: costo_fijo / volumen.
    """
    base_tps = decode_tokens_per_second(model, gpu, dtype, efficiency, batch=1)
    puntos = []
    for b in batch_sizes:
        total = base_tps * b
        # Rendimientos decrecientes: al crecer el batch, el cómputo por token
        # (atención sobre el KV cache, que NO se comparte) empieza a pesar.
        # Penalización suave calibrada para que el efecto aparezca a batch alto.
        penal = 1.0 / (1.0 + b / 512.0)
        total *= (0.5 + 0.5 * penal) if b > 64 else 1.0
        per_seq = total / b
        usd_h = gpu.usd_per_hour
        tokens_h = total * 3600
        puntos.append(
            BatchPoint(
                batch=b,
                tokens_per_s_per_seq=per_seq,
                tokens_per_s_total=total,
                ms_per_token_per_seq=1000.0 / per_seq,
                usd_per_m_tokens=usd_h / (tokens_h / 1e6) if tokens_h else float("inf"),
            )
        )
    return puntos


def queue_wait_ms(service_ms: float, utilization: float) -> float:
    """Espera en cola de un M/M/1 en función de la utilización.

        W_espera = servicio · ρ / (1 − ρ)

    El resultado no depende de LLMs: es teoría de colas. Su relevancia acá es
    que explica el hecho que todo operador observa y pocos anticipan — la
    latencia no se degrada linealmente con la carga, explota cerca de ρ=1.
    A 50% de utilización esperás lo mismo que tardás; a 95%, diecinueve veces más.
    """
    if utilization >= 1.0:
        return float("inf")
    return service_ms * utilization / (1.0 - utilization)


def max_concurrent_sequences(
    model: ModelSpec, gpu: GPU, context_tokens: int, dtype: str = "bf16",
    kv_dtype: str = "bf16", overhead_gb: float = 2.0,
) -> int:
    """Cuántas secuencias caben a la vez: el KV cache es el recurso escaso.

    Memoria libre = memoria total − pesos − overhead (activaciones, framework).
    Cada secuencia consume KV proporcional a su contexto. Este número es el que
    determina cuánta gente podés atender en paralelo, no los TFLOPs.
    """
    free_gb = gpu.memory_gb - model.weights_gb(dtype) - overhead_gb
    if free_gb <= 0:
        return 0
    per_seq_gb = model.kv_cache_gb(context_tokens, batch=1, dtype=kv_dtype)
    return int(free_gb / per_seq_gb) if per_seq_gb > 0 else 0
