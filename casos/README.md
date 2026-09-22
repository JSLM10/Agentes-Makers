# Casos para demostrar en vivo

Cuatro escenarios en orden de presentación.

La interfaz (`python web.py`) los ofrece como botones encima del manifiesto: se
elige uno y queda cargado, sin pegar nada a mano. `casos.json` es el índice que
el servidor expone en `/api/casos`; los `.txt` son la única fuente del texto.

Para agregar un caso: poner el `.txt` en esta carpeta y añadir su entrada en
`casos.json`. No hay que tocar código.

También se corren por terminal:

```bash
python demo.py --archivo casos/1-normal.txt
python demo.py --archivo casos/1-normal.txt --offline   # sin red
```

| Archivo | Qué demuestra | Resultado esperado |
|---|---|---|
| `1-normal.txt` | El camino feliz: compara tres proveedores y entrega el ganador con su enlace real | Recomienda y da URL de AliExpress |
| `2-fraude.txt` | Un proveedor barato con reseñas de estafa queda excluido por calificación | Gate 2 deja 1/2 candidatos; recomienda al caro |
| `3-injection.txt` | Prompt injection: el modelo nunca se llama | G1, G2 y G3 bloquean; costo cero de LLM |
| `4-sin-candidatos.txt` | Ningún proveedor califica (rating bajo o stock en cero) | Sin recomendación, revisión humana |

Los casos 3 y 4 cortocircuitan antes del LLM, así que responden igual de rápido
con o sin red. Sirven para demostrar aunque no haya conectividad.
