# Motores de resumen intercambiables (Pi / Ollama / nube)

Rama `feature/summary-engines` sobre `main` (0f663e7). El resumen con IA deja
de depender de Pi: un backend con detección automática y botones que solo se
habilitan si hay algún motor disponible.

## Specs

- **S1**: Detección en orden: Pi instalado → Ollama en `127.0.0.1:11434` con
  el modelo configurado instalado → API compatible OpenAI con key configurada
  → ninguno. Sin motor, los botones de resumen quedan deshabilitados.
- **S2**: `summarizer.summarize()` despacha al motor detectado y mantiene el
  contrato actual: mismo prompt con secciones (`## Resumen`, `## Acciones`,
  ...), streaming de deltas, cancelable, timeout.
- **S3**: Ollama: `POST /api/chat` stream JSONL; modelo configurable
  (`ollama_model`, default `llama3.1:8b`); 100% local.
- **S4**: Nube: endpoint compatible OpenAI (`cloud_base_url`, `cloud_model`,
  `cloud_api_key` en `transcriptor_config.json`, gitignoreado); streaming
  SSE; el transcript viaja al proveedor (documentado en README como
  decisión de privacidad del usuario).
- **S5**: La web muestra en un chip qué motor va a correr y habilita los
  botones según `summaryAvailable`.
- **S6**: README profesional y conciso: qué hace, requisitos, instalación,
  uso, motores de resumen, arquitectura mínima, desarrollo/tests.

## Tasks

- T1 (S1-S4): `summarizer.py` + settings genéricos. Commit: pendiente.
- T2 (S5): integración web. Commit: pendiente.
- T3 (S6): README + suite + e2e + push. Commit: pendiente.

## Log

- L1 (usuario): "dale, el readme que sea profesional bro, sin tanto palabreo
  con lo importante" — aprueba el backend intercambiable y pide README
  conciso.
