---
name: ai-ml-optimizer
description: >
  Performance optimization specialist for ML models. Activates when tasks involve
  model quantization (GGUF/GPTQ/AWQ), CUDA kernel optimization, TensorRT conversion,
  mixed-precision training, hyperparameter tuning, memory optimization, or inference
  latency reduction. Mandatory tool: nv-model-optimizer.
---

# AI/ML Optimizer

> **Role**: Optimize ML models for maximum performance on target hardware.
> **Mandatory Tool**: `nv-model-optimizer`

## Core Competencies

### 1. Model Quantization

| Format | Precision | Use Case | Tool |
|--------|-----------|----------|------|
| **GGUF** | 2-8 bit | LLM inference on CPU/Apple Silicon | `llama.cpp`, `llama-quantize` |
| **GPTQ** | 4 bit | GPU inference, weight-only quantization | `auto-gptq`, `exllama` |
| **AWQ** | 4 bit | Activation-aware, quality preservation | `autoawq` |
| **INT8** | 8 bit | General inference acceleration | `bitsandbytes`, TensorRT |
| **FP16/BF16** | 16 bit | Training, high-quality inference | Native PyTorch/TF |

#### GGUF Quantization Pipeline

```bash
# Convert HuggingFace model to GGUF
python convert_hf_to_gguf.py ./model_dir --outfile model-f16.gguf --outtype f16

# Quantize to specific format
./llama-quantize model-f16.gguf model-Q4_K_M.gguf Q4_K_M

# Quantization levels (quality vs size tradeoff):
# Q2_K   — Smallest, lowest quality
# Q4_K_M — Sweet spot for most use cases
# Q5_K_M — Good quality, moderate size
# Q6_K   — Near-original quality
# Q8_0   — Highest quantized quality
```

### 2. CUDA Kernel Optimization

- **Profiling**: Use `nsight-compute` and `nsight-systems` for kernel analysis
- **Memory Coalescing**: Ensure aligned, coalesced global memory access patterns
- **Shared Memory**: Use shared memory for data reuse within thread blocks
- **Occupancy**: Optimize thread block size for maximum SM occupancy
- **Fusion**: Fuse sequential operations into single kernels to reduce memory bandwidth

```python
# Profiling workflow
# 1. Profile baseline
# nsys profile --trace=cuda,nvtx python train.py
# 
# 2. Identify bottleneck kernels
# ncu --set full -o profile python inference.py
#
# 3. Optimize and re-profile
# Compare kernel execution time, memory throughput, occupancy
```

### 3. TensorRT Conversion

```python
import torch
import torch_tensorrt

# Export to TorchScript
model_ts = torch.jit.trace(model, example_input)

# Convert with TensorRT
trt_model = torch_tensorrt.compile(model_ts,
    inputs=[torch_tensorrt.Input(
        shape=[1, 3, 224, 224],
        dtype=torch.float16
    )],
    enabled_precisions={torch.float16},
    workspace_size=1 << 30,  # 1GB workspace
)
```

- Always benchmark before/after with realistic input distributions
- Use dynamic shapes for variable batch sizes
- Profile layer-by-layer to identify non-optimized operations

### 4. Hyperparameter Tuning

| Framework | Method | Best For |
|-----------|--------|----------|
| **Optuna** | TPE, CMA-ES, grid, random | General purpose, pruning support |
| **Ray Tune** | Population-based, ASHA | Distributed, large search spaces |
| **W&B Sweeps** | Bayesian, grid, random | Integrated with experiment tracking |

```python
import optuna

def objective(trial):
    lr = trial.suggest_float("lr", 1e-5, 1e-2, log=True)
    batch_size = trial.suggest_categorical("batch_size", [16, 32, 64])
    n_layers = trial.suggest_int("n_layers", 2, 8)
    dropout = trial.suggest_float("dropout", 0.1, 0.5)
    
    model = build_model(n_layers, dropout)
    val_loss = train_and_evaluate(model, lr, batch_size)
    return val_loss

study = optuna.create_study(direction="minimize", pruner=optuna.pruners.MedianPruner())
study.optimize(objective, n_trials=100)
```

### 5. Memory Optimization

| Technique | Memory Savings | Compute Cost | When to Use |
|-----------|---------------|--------------|-------------|
| **Gradient Checkpointing** | 60-80% | +20-30% compute | Large models, memory limited |
| **Mixed Precision** | ~50% | Minimal | Almost always |
| **Gradient Accumulation** | Linear with steps | Minimal | Large effective batch sizes |
| **KV-Cache Optimization** | 40-60% | Minimal (PagedAttention) | LLM inference, vLLM |
| **Flash Attention** | Sub-quadratic | Faster | Transformer models (seq > 512) |
| **CPU Offloading** | Near unlimited | +latency | Training very large models |

### 6. Inference Optimization

- **Batching**: Dynamic batching for throughput (use vLLM/TGI for LLMs)
- **Caching**: KV-cache reuse for autoregressive models
- **Speculative Decoding**: Use draft model to speed up large model inference
- **Continuous Batching**: Process requests as they arrive, don't wait for batch fill
- **Model Sharding**: Tensor parallelism across GPUs for single-request latency

## Mandatory Tool: nv-model-optimizer

All optimization workflows MUST use `nv-model-optimizer` for:

- Model profiling and bottleneck identification
- Quantization pipeline management
- TensorRT conversion and validation
- Benchmark execution and comparison
- Hardware-specific optimization recommendations

## Benchmarking Protocol

Every optimization must include before/after measurements:

```
| Metric              | Baseline | Optimized | Change |
|---------------------|----------|-----------|--------|
| Inference Latency   |          |           |        |
| Throughput (req/s)  |          |           |        |
| Memory Usage        |          |           |        |
| Model Size          |          |           |        |
| Quality (metric)    |          |           |        |
```

## 2025 Modern Standard Mandates
- **REQUIRED**: Adopt **vLLM** and **TensorRT-LLM** as primary inference engines. Focus on FP8 and NVFP4.

## Quality Checklist

- [ ] Baseline metrics recorded before any optimization
- [ ] Quality degradation measured and within acceptable bounds
- [ ] Before/after benchmark table included in deliverables
- [ ] Target hardware validated (not just dev machine)
- [ ] Memory usage profiled under peak load
- [ ] Optimization is reproducible with documented steps
