# Triton Model Repository

This directory is the Triton model repository mounted into the `triton` container.

## Layout

- `yolov12/1/model.onnx`, `yolov12/1/model.plan`
- `arcface/1/model.onnx`, `arcface/1/model.plan`
- `adaface/1/model.onnx`, `adaface/1/model.plan`
- `lvface/1/model.onnx`, `lvface/1/model.plan`
- `realesrgan/1/model.onnx`, `realesrgan/1/model.plan`
- `antispoof/1/model.onnx`, `antispoof/1/model.plan`

Each model has a `config.pbtxt` with input/output schema and dynamic batching.

## TensorRT FP16 Build

Use `scripts/build_triton_engines.ps1` to copy ONNX assets into version folders and build FP16 engines:

```powershell
pwsh -File scripts/build_triton_engines.ps1
```

## INT8 Stretch Goal

INT8 is optional and requires calibration data. Example command template:

```powershell
trtexec --onnx=model.onnx --saveEngine=model_int8.plan --int8 --calib=calibration.cache
```

## Notes

- model.onnx and model.plan files are NOT committed to git. Run scripts/build_triton_engines.ps1 to populate them from the models/ directory.
- Triton configs are currently set to `platform: "tensorrt_plan"` and expect `model.plan`.
