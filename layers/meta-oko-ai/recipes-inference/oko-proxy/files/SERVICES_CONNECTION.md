# OKO AI Services Connection Schematic

## Architecture Overview

```
                    ┌─────────────────────────────────────────────────────────────────┐
                    │                        EXTERNAL CLIENT                          │
                    │                    (OpenAI API compatible)                      │
                    └──────────────────────────────┬──────────────────────────────────┘
                                                   │
                                                   │ HTTP
                                                   │
                    ┌──────────────────────────────▼──────────────────────────────────┐
                    │              oko-ai-proxy-openai                                 │
                    │              Listens: 0.0.0.0:8000                               │
                    │              Role: OpenAI API compatibility layer                │
                    └──────────────────────────────┬──────────────────────────────────┘
                                                   │
                                                   │ → 127.0.0.1:11434
                                                   │
                    ┌──────────────────────────────▼──────────────────────────────────┐
                    │              oko-ai-proxy-stream                                 │
                    │              Listens: 0.0.0.0:11434                              │
                    │              Role: Stream conversion, Ollama API entry           │
                    └──────────────────────────────┬──────────────────────────────────┘
                                                   │
                                                   │ → 127.0.0.1:11436
                                                   │
                    ┌──────────────────────────────▼──────────────────────────────────┐
                    │              oko-ai-proxy-router                                 │
                    │              Listens: 0.0.0.0:11436                              │
                    │              Role: Routes to main/embed backends                 │
                    └──────────────┬───────────────────────────────┬──────────────────┘
                                   │                               │
                                   │ → 127.0.0.1:11435             │ → 127.0.0.1:12435
                                   │                               │
                    ┌──────────────▼────────────────────┐ ┌────────▼──────────────────────┐
                    │     oko-ai-proxy                  │ │     oko-ai-proxy-embed         │
                    │     Listens: 127.0.0.1:11435      │ │     Listens: 127.0.0.1:12435   │
                    │     Role: Main model proxy        │ │     Role: Embedding model proxy│
                    │     Config: oko-ai.yaml           │ │     Config: oko-ai-embed.yaml  │
                    └──────────────┬────────────────────┘ └────────┬──────────────────────┘
                                   │                               │
                                   │ spawns                        │ spawns
                                   │                               │
                    ┌──────────────▼────────────────────┐ ┌────────▼──────────────────────┐
                    │     llama-server                  │ │     llama-server               │
                    │     Port: 8080                    │ │     Port: 8081                 │
                    │     (main model inference)        │ │     (embedding inference)      │
                    └───────────────────────────────────┘ └───────────────────────────────┘
```

## Service Details

| Service | Listens | Forwards to | Role |
|---------|---------|-------------|------|
| `oko-ai-proxy-openai` | 0.0.0.0:8000 | 127.0.0.1:11434 | OpenAI API compatibility layer |
| `oko-ai-proxy-stream` | 0.0.0.0:11434 | 127.0.0.1:11436 | Stream conversion, Ollama API entry |
| `oko-ai-proxy-router` | 0.0.0.0:11436 | 127.0.0.1:11435, 127.0.0.1:12435 | Routes to main/embed backends |
| `oko-ai-proxy` | 127.0.0.1:11435 | spawns llama-server (port 8080) | Main model proxy (config: `oko-ai.yaml`) |
| `oko-ai-proxy-embed` | 127.0.0.1:12435 | spawns llama-server (port 8081) | Embedding model proxy (config: `oko-ai-embed.yaml`) |

## Request Flow

1. **Client** sends OpenAI-compatible API request to `0.0.0.0:8000`
2. **oko-ai-proxy-openai** receives and forwards to `127.0.0.1:11434`
3. **oko-ai-proxy-stream** converts streaming format, forwards to `127.0.0.1:11436`
4. **oko-ai-proxy-router** inspects request and routes to:
   - `127.0.0.1:11435` (main model) or
   - `127.0.0.1:12435` (embedding model)
5. **oko-ai-proxy** or **oko-ai-proxy-embed** spawns/manages `llama-server` process
6. **llama-server** performs inference on port 8080 (main) or 8081 (embed)
7. Response flows back up the chain to the client
