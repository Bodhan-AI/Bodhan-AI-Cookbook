# Local Bodhan Models & LLM Setup Guide (Docker Compose & GPU)

This guide is based on serving the 4 Bodhan Indic models alongside a local **vLLM orchestrator LLM (`Qwen/Qwen2.5-7B-Instruct`)** on NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition (96 GB VRAM)

---

## 1. Download Bodhan Model Weights

Ensure `.env` contains Hugging Face token or export token to the terminal session:
```bash
HF_TOKEN=hf_...
```

Run the automated download script:
```bash
python3 scripts/download_models.py --model all
```

---

## 2. Start the Cluster (Models + vLLM Orchestrator)

Run Docker Compose:
```bash
docker compose up --build -d
```

### What Docker Compose Starts:
1. **`bodhan-gateway`** (Port `8000`): Unified endpoint for Bodhan Indic models (`/v1/audio/transcriptions`, `/v1/chat/completions`, `/v1/audio/speech`, `/v1/ocr`).
2. **`bodhan-llm`** (Port `8005`): **vLLM serving `Qwen/Qwen2.5-7B-Instruct`** with auto tool-calling parser enabled (`--tool-call-parser hermes`).
3. **`bodhan-transcribe`**, **`bodhan-translate`**, **`bodhan-speak`**, **`bodhan-ocr`**: The 4 Indic model microservices running on GPU.
  
---

## 3. How the LLM Weights Are Downloaded

The orchestrator service (`llm-srv`) uses the official **`vllm/vllm-openai:latest`** image.
* When the container starts for the first time, vLLM automatically downloads `Qwen/Qwen2.5-7B-Instruct` from Hugging Face into `~/.cache/huggingface`.
* Once downloaded, it is permanently cached on SSD and never re-downloaded.

monitor the download progress via:
```bash
docker compose logs -f llm-srv
```

---

## 4. Test the Endpoints

### Test the Orchestrator LLM (Port 8005):
```bash
curl http://localhost:8005/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen2.5-7B-Instruct",
    "messages": [
      {"role": "system", "content": "You are an agentic AI assistant."},
      {"role": "user", "content": "List 3 steps to process an Indic audio file."}
    ]
  }'
```

### Test Bodhan Models (Port 8000):
```bash
curl http://localhost:8000/health
curl http://localhost:8000/v1/models
```

---

## 5. Agent Configuration in `.env`

To run the agent using 100% local models on your A6000:
```bash
# Bodhan Models Gateway
BODHAN_BASE_URL=http://localhost:8000/v1

# Local vLLM Orchestrator
LLM_BASE_URL=http://localhost:8005/v1
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
LLM_API_KEY=dummy
```
