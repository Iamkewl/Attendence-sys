Param(
    [switch]$SkipEngineBuild
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$modelMap = @(
    @{ Name = "yolov12"; Source = "models/yolov12l.onnx" },
    @{ Name = "arcface"; Source = "models/arcface.onnx" },
    @{ Name = "adaface"; Source = "models/adaface_ir101_webface12m.onnx" },
    @{ Name = "lvface"; Source = "models/lvface_base.onnx" },
    @{ Name = "realesrgan"; Source = "models/realesrgan_x4.onnx" },
    @{ Name = "antispoof"; Source = "models/anti_spoof_2.7_80x80.onnx" }
)

foreach ($model in $modelMap) {
    $versionDir = Join-Path $repoRoot ("triton_models/{0}/1" -f $model.Name)
    $onnxTarget = Join-Path $versionDir "model.onnx"
    $planTarget = Join-Path $versionDir "model.plan"

    if (Test-Path $model.Source) {
        Copy-Item -Path $model.Source -Destination $onnxTarget -Force
        Write-Host ("Copied ONNX for {0}: {1}" -f $model.Name, $model.Source)
    }
    else {
        Write-Warning ("Missing source ONNX for {0}: {1}" -f $model.Name, $model.Source)
        continue
    }

    if ($SkipEngineBuild) {
        Write-Host ("Skipping TensorRT build for {0}" -f $model.Name)
        continue
    }

    $trtexec = Get-Command trtexec -ErrorAction SilentlyContinue
    if (-not $trtexec) {
        Write-Warning "trtexec not found in PATH. Install TensorRT and re-run without -SkipEngineBuild."
        continue
    }

    & trtexec --onnx=$onnxTarget --saveEngine=$planTarget --fp16
    if ($LASTEXITCODE -ne 0) {
        throw ("trtexec failed for model {0}" -f $model.Name)
    }

    Write-Host ("Built FP16 TensorRT engine: {0}" -f $planTarget)
}

Write-Host "Triton model preparation completed."
