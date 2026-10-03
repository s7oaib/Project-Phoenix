# 06 — Hardware Profile

## Development profile: BLAZE-6

- CPU: Intel Core i7-13650HX
- Cores: 14
- Logical processors: 20
- RAM: approximately 24 GB
- GPU: NVIDIA GeForce RTX 3050 Laptop GPU
- VRAM: 6144 MiB
- Ollama: 0.35.1

## Planning-time free storage

- C: approximately 54 GB
- D: approximately 159 GB
- E: approximately 77 GB

Preferred location:

```text
D:\Phoenix\
  models\
  data\
  cache\
  logs\
```

## Runtime policy

- Avoid storing large model libraries on C:.
- Prefer one major model resident at a time.
- Colibri deep mode is benchmark-gated due to 24 GB system RAM.
- Hardware scan should not rely on Windows AdapterRAM alone when better GPU runtime data is available.

## Future profile names

EMBER, BLAZE, INFERNO, SUPERNOVA may later become supported presets.
