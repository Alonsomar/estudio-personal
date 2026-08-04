# 01 — Qué es una ontología y por qué ya construiste varias

## Del clasificador al grafo

El clasificador presupuestario chileno —Partida, Capítulo, Programa,
Subtítulo, Ítem y Asignación— ya define entidades, jerarquía y reglas de
contención. Convertirlo a un grafo no inventa estructura: hace consultable la
que un analista fiscal ya usa.

Conviene separar cuatro conceptos:

| Concepto | Aporte | Ejemplo |
|---|---|---|
| Taxonomía | jerarquía `is-a` | clasificación UNSPSC |
| Tesauro | sinónimos y términos relacionados | DIPRES / Dirección de Presupuestos |
| Ontología | entidades y relaciones tipadas | Norma `MODIFICA` Norma |
| Grafo de conocimiento | ontología instanciada | clasificador 2024 parseado |

## Resultado reproducido

Los cinco documentos `glosa-*.txt` producen:

```
Nodos: 68  ·  Aristas CONTIENE: 63

       partida:   5
      capitulo:  11
      programa:  12
     subtitulo:  13
          item:   9
    asignacion:  18
```

`NodoClasificador` admite `monto_miles` en cualquier nivel y
`monto_reportado_miles` para agregados declarados. `monto_total()` suma hojas
monetarias: si un padre y sus hijos tienen monto, no los duplica.

## Competency question presupuestaria

| Partida | Asignaciones | Monto total (miles $) |
|---|---:|---:|
| 16 Salud | 4 | 5.275.327.880 |
| 09 Educación | 6 | 11.940.570.500 |
| 15 Trabajo | 3 | 228.508.350 |
| 12 Obras Públicas | 2 | 834.605.363 |
| 05 Interior | 3 | 1.379.671.450 |

La Partida 09 combina cinco filas de tabla con una Asignación lineal. El
Programa 20 suma $10.928.003.100 miles y reconcilia exactamente con su total
reportado. La Partida 12 incluye $34.219.008 miles declarados directamente en
Subtítulo, aunque no exista una Asignación hija.

![Clasificador como grafo](../diagrams/clasificador-como-grafo.png)

## Límite

El parser cubre los dos formatos presentes —líneas y tabla de ancho fijo—. Una
convención nueva requiere otra regla o extracción semántica. Ese límite no
justifica omitir formatos ya conocidos de las cifras publicadas.

## Conexiones

- `§2` amplía `CONTIENE` a relaciones normativas tipadas.
- `§4` generaliza el uso de llaves canónicas.
- `§5` mide extracción semántica sobre texto normativo.
