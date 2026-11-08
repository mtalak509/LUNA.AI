# Files

- [LLM providers and configuration](llm-providers-and-config.md) - How LUNA reads its configuration from the environment and .env, how LLM_PROVIDER selects between OpenRouter, GPUStack/vLLM and Ollama through one ChatOpenAI client, and the reasoning-stream patch for vLLM.
- [Optional RAG knowledge base](rag-knowledge-base.md) - The optional two-step knowledge-base search over Qdrant — tools, feature flag, the RAGService/EmbeddingService/QdrantService layers, and the HTTP client retry and error taxonomy underneath.
