"""econ_lib — aritmética de la economía de inferencia para la masterclass 04.

Núcleo reutilizable que acumula lo que las secciones introducen:

  §1  Aritmética de un modelo transformer denso: parámetros, bytes por token
      generado, tamaño del KV cache, techos de prefill y decode.
  §2  Batching: throughput agregado, latencia por secuencia, costo por millón
      de tokens y la cola M/M/1 que explica por qué el p95 explota cerca de
      la saturación.
  §3  Cuantización: perfil de memoria/velocidad/costo por dtype, y el análisis
      de potencia que dice cuántas queries de golden hacen falta para detectar
      una degradación de calidad dada.
  §4  Self-hosting vs API: costo mensual de cada opción, punto de equilibrio
      en volumen, y el efecto de la utilización sobre el costo unitario.
  §5  Deriva de precios: decaimiento exponencial de tarifas, crecimiento del
      consumo por query, y el gasto total resultante (efecto Jevons).

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


# --------------------------------------------------------------------------- #
# §3 Cuantización. Menos bits por peso = menos bytes que mover por token (§1)
# = más rápido y más barato, además de liberar memoria para KV cache (más
# concurrencia). Lo que cuesta es calidad, y eso NO se modela: se mide.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class QuantProfile:
    """Efecto de un dtype sobre memoria, velocidad, concurrencia y costo.

    Todo esto es derivable de la aritmética de §1-§2. La calidad NO aparece
    acá a propósito: no es derivable, hay que medirla en tu golden.
    """

    dtype: str
    weights_gb: float
    fits_in_gpu: bool
    tokens_per_s: float
    max_seqs_4k: int
    batch_efectivo: int
    usd_per_m_tokens: float


def quant_profile(
    model: ModelSpec, gpu: GPU, dtype: str, batch_objetivo: int = 32, context: int = 4_000
) -> QuantProfile:
    """Perfil completo de un modelo servido con pesos en `dtype`.

    El batch efectivo está limitado por la memoria: de nada sirve querer batch
    32 si en el KV cache solo entran 6 secuencias. Ese acoplamiento entre
    cuantización y batch alcanzable (§2) es justamente lo que hace que el efecto
    de cuantizar sea más que proporcional.
    """
    w = model.weights_gb(dtype)
    fits = w < gpu.memory_gb - 2.0
    seqs = max_concurrent_sequences(model, gpu, context, dtype=dtype) if fits else 0
    batch_ef = min(batch_objetivo, seqs)
    tps_total = (
        decode_tokens_per_second(model, gpu, dtype, batch=batch_ef) if batch_ef else 0.0
    )
    tokens_h = tps_total * 3600
    return QuantProfile(
        dtype=dtype,
        weights_gb=w,
        fits_in_gpu=fits,
        tokens_per_s=decode_tokens_per_second(model, gpu, dtype) if fits else 0.0,
        max_seqs_4k=seqs,
        batch_efectivo=batch_ef,
        usd_per_m_tokens=(gpu.usd_per_hour / (tokens_h / 1e6) if tokens_h else float("inf")),
    )


# --------------------------------------------------------------------------- #
# §4 Hacer vs comprar. La API es costo puramente variable; el self-hosting es
# costo fijo por hora de GPU (esté ocupada o no) más operación. El punto de
# equilibrio depende críticamente de la UTILIZACIÓN, que es la variable que
# los cálculos ingenuos omiten.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class HostingScenario:
    """Costo mensual de servir un volumen dado, por las dos vías."""

    tokens_out_month: float
    api_usd: float
    selfhost_gpu_usd: float
    selfhost_ops_usd: float
    selfhost_total_usd: float
    utilization: float
    gpus_needed: int


HOURS_PER_MONTH = 730.0


def hosting_cost(
    tokens_out_month: float,
    api_usd_per_m_out: float,
    gpu: GPU,
    sustained_tokens_per_s: float,
    ops_usd_month: float = 0.0,
    target_utilization: float = 0.7,
    n_gpus: int | None = None,
) -> HostingScenario:
    """Compara el costo mensual de API vs. self-hosting para un volumen dado.

    `sustained_tokens_per_s` es el throughput por GPU al batch efectivo (§2/§3).
    `target_utilization` reconoce lo que §2 demostró: operar cerca del 100% hace
    explotar la latencia, así que la capacidad se dimensiona con holgura — y esa
    holgura ES parte del costo, no un desperdicio a eliminar.

    `ops_usd_month` es el costo de operación (guardia, actualizaciones, tiempo
    propio). Ponerlo en 0 es el error clásico de la comparación ingenua.
    """
    api = tokens_out_month / 1e6 * api_usd_per_m_out

    # Capacidad necesaria: el volumen tiene que caber en la fracción de tiempo
    # que la utilización objetivo permite usar.
    capacity_per_gpu = sustained_tokens_per_s * 3600 * HOURS_PER_MONTH * target_utilization
    needed = n_gpus if n_gpus is not None else max(1, _ceil(tokens_out_month / capacity_per_gpu))

    gpu_cost = needed * gpu.usd_per_hour * HOURS_PER_MONTH
    total_capacity = needed * sustained_tokens_per_s * 3600 * HOURS_PER_MONTH
    util = tokens_out_month / total_capacity if total_capacity else 0.0

    return HostingScenario(
        tokens_out_month=tokens_out_month,
        api_usd=api,
        selfhost_gpu_usd=gpu_cost,
        selfhost_ops_usd=ops_usd_month,
        selfhost_total_usd=gpu_cost + ops_usd_month,
        utilization=util,
        gpus_needed=needed,
    )


def _ceil(x: float) -> int:
    return int(x) + (1 if x > int(x) else 0)


def breakeven_tokens(
    api_usd_per_m_out: float,
    gpu: GPU,
    sustained_tokens_per_s: float,
    ops_usd_month: float = 0.0,
    n_gpus: int = 1,
) -> float:
    """Volumen mensual de tokens de salida donde self-hosting iguala a la API.

    Con una GPU fija, el costo de self-hosting NO depende del volumen (es fijo)
    y el de la API sí (es variable). El punto de equilibrio es simplemente:

        tokens = costo_fijo_mensual / precio_por_token_de_la_API

    Devuelve infinito si el volumen de equilibrio excede la capacidad física de
    las GPUs: ahí el self-hosting nunca alcanza a la API, por más que crezcas.
    """
    fixed = n_gpus * gpu.usd_per_hour * HOURS_PER_MONTH + ops_usd_month
    tokens = fixed / api_usd_per_m_out * 1e6
    capacity = n_gpus * sustained_tokens_per_s * 3600 * HOURS_PER_MONTH
    return tokens if tokens <= capacity else float("inf")


# --------------------------------------------------------------------------- #
# §5 Deriva. Las tarifas caen; el consumo por query sube. El gasto total es el
# producto de las dos, y ahí es donde vive la sorpresa.
# --------------------------------------------------------------------------- #
def price_after(precio_hoy: float, caida_anual: float, anios: float) -> float:
    """Tarifa proyectada suponiendo caída proporcional constante.

        p(t) = p0 · (1 − caída_anual)^t

    `caida_anual` = 0.6 significa "el precio cae 60% cada año" (o sea, queda el
    40%). La forma exponencial es el supuesto más simple que captura el hecho
    observado; no es una predicción, es un escenario parametrizado.
    """
    return precio_hoy * (1.0 - caida_anual) ** anios


def spend_trajectory(
    tokens_hoy_month: float,
    precio_hoy_per_m: float,
    caida_precio_anual: float,
    crecimiento_consumo_anual: float,
    anios: int = 5,
) -> list[tuple[int, float, float, float]]:
    """Trayectoria de (año, tarifa, tokens/mes, gasto/mes).

    El punto de la sección: aunque la tarifa caiga, el gasto puede subir si el
    consumo por query crece más rápido. Es la paradoja de Jevons — abaratar un
    insumo aumenta su consumo total, y a veces el gasto total con él.

    `crecimiento_consumo_anual` = 1.0 significa que el consumo se duplica cada
    año (más contexto, más pasos agénticos, más usuarios).
    """
    filas = []
    for t in range(anios + 1):
        precio = price_after(precio_hoy_per_m, caida_precio_anual, t)
        tokens = tokens_hoy_month * (1.0 + crecimiento_consumo_anual) ** t
        filas.append((t, precio, tokens, tokens / 1e6 * precio))
    return filas


def min_golden_size(
    baseline_rate: float, delta: float, alpha: float = 0.05, power: float = 0.80
) -> int:
    """Cuántas queries de golden hacen falta para detectar una caída `delta`.

    Comparación de dos proporciones pareadas, aproximación normal:

        n ≈ (z_{α/2} + z_β)² · [p₁(1−p₁) + p₂(1−p₂)] / δ²

    Es la pregunta que hay que hacerse ANTES de cuantizar, no después: si tu
    golden tiene 30 queries, no vas a poder distinguir una degradación de 3
    puntos de ruido de muestreo. Sin esto, "cuantizamos y no se notó" no
    significa "no degradó" — significa "no teníamos con qué verlo".

    Usa la aproximación normal (no exacta) y supone independencia entre
    queries; es una cota inferior optimista del tamaño necesario.
    """
    z_alpha = 1.959963985  # z para α=0.05 a dos colas
    z_beta = {0.80: 0.8416212336, 0.90: 1.281551566, 0.95: 1.644853627}.get(power, 0.8416212336)
    p1 = baseline_rate
    p2 = max(min(baseline_rate - delta, 1.0), 0.0)
    var = p1 * (1 - p1) + p2 * (1 - p2)
    if delta <= 0:
        return 0
    return int(((z_alpha + z_beta) ** 2 * var / delta**2) + 0.999)


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
