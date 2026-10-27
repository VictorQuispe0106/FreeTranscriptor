# Mejoras de flujo: resumen automático al Parar + búsqueda global

Rama `feature/mejoras-flujo` sobre `main` (post-merge de las tres ramas).
Dos mejoras de uso diario aprobadas por el usuario sobre el menú propuesto.

## Specs

- **S1**: Toggle persistente "Resumen al parar" (default OFF), guardado en
  `transcriptor_config.json` (gitignored), compartido por tkinter y web.
- **S2**: Con el toggle ON, al presionar Parar (tkinter) o Stop (web) con una
  sesión terminada y Pi disponible, el resumen con Pi arranca solo sobre el
  transcript de la sesión recién cerrada. Con OFF el comportamiento actual
  no cambia.
- **S3**: Búsqueda global del historial: busca una frase, case-insensitive,
  en todas las sesiones `*_live-captions.md` de `transcripts\` (nunca en
  `.limpio`, `.resumen`, `.txt` ni snapshots); devuelve sesión, `[hh:mm:ss]`
  y frase; resultados limitados (200), sesión más nueva primero.
- **S4**: UI de búsqueda: en la web, buscar desde el filtro (Enter) y mostrar
  resultados con sesión + stamp + frase; click en un resultado abre ese
  transcript. En tkinter, botón "Buscar" con diálogo (Entry + resultados,
  doble click abre).
- **S5**: README documentando ambas mejoras.

## Tasks

- T1 (S1, S2): resumen automático al Parar — settings store + tkinter + web + tests. Commit: pendiente.
- T2 (S3, S4): búsqueda global — backend compartido + web + tkinter + tests. Commit: pendiente.
- T3 (S5): README + suite completa + e2e. Commit: pendiente.

## Log

- L1: "que mejoras podriamos hacer bro?" — pedido de propuestas.
- L2: "dale mejoralo bro" — aprueba la recomendación: merge de ramas a main,
  luego resumen automático al Parar y búsqueda global en el historial.
- L3: Merge hecho: `main` fast-forward a `38bdeea` (incluye las 3 ramas y el
  fix del render Markdown del resumen); suite 75 tests OK en main.
