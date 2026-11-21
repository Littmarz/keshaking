# models/ (не в git)

```
models/
├── ollama/                  volume Ollama (LLM)
└── hf/
    ├── bge-m3/              эмбеддинги (качает 📄)
    ├── bge-reranker-v2-m3/  реранкер (качает 📄, использует 🔎)
    └── Qwen3-8B/            для vLLM (🤖), если используем
```

Скачивание: `make models` (скрипт `deploy/scripts/download_models.sh`).
