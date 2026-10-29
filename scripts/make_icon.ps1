# 生成 DivergenceQuant 应用图标（256x256 PNG + ICO）
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\make_icon.ps1
Add-Type -AssemblyName System.Drawing

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$assets = Join-Path $root 'assets'
New-Item -ItemType Directory -Force -Path $assets | Out-Null
$pngPath = Join-Path $assets 'app_icon.png'
$icoPath = Join-Path $assets 'app_icon.ico'

$size = 256
$bmp = New-Object System.Drawing.Bitmap($size, $size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.Clear([System.Drawing.Color]::Transparent)

$path = New-Object System.Drawing.Drawing2D.GraphicsPath
$r = 52
$m = 8
$path.AddArc($m, $m, $r, $r, 180, 90)
$path.AddArc($size - $m - $r, $m, $r, $r, 270, 90)
$path.AddArc($size - $m - $r, $size - $m - $r, $r, $r, 0, 90)
$path.AddArc($m, $size - $m - $r, $r, $r, 90, 90)
$path.CloseFigure()

$bg = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 12, 16, 22))
$g.FillPath($bg, $path)
$border = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 0, 245, 160), 7)
$g.DrawPath($border, $path)

$white = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 255, 255, 255), 10)
$white.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
$white.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
$g.DrawLine($white, 56, 176, 104, 104)
$g.DrawLine($white, 104, 104, 152, 144)

$green = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 0, 245, 160), 11)
$green.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
$green.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
$g.DrawLine($green, 152, 144, 200, 64)

$dotBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 0, 245, 160))
$g.FillEllipse($dotBrush, 188, 52, 24, 24)

$g.Dispose()
$bmp.Save($pngPath, [System.Drawing.Imaging.ImageFormat]::Png)

$png = [System.IO.File]::ReadAllBytes($pngPath)
$ms = New-Object System.IO.MemoryStream
$bw = New-Object System.IO.BinaryWriter($ms)
$bw.Write([UInt16]0)
$bw.Write([UInt16]1)
$bw.Write([UInt16]1)
$bw.Write([Byte]0)
$bw.Write([Byte]0)
$bw.Write([Byte]0)
$bw.Write([Byte]0)
$bw.Write([UInt16]1)
$bw.Write([UInt16]32)
$bw.Write([UInt32]$png.Length)
$bw.Write([UInt32]22)
$bw.Write($png)
$bw.Flush()
[System.IO.File]::WriteAllBytes($icoPath, $ms.ToArray())
$bw.Dispose()
$ms.Dispose()
$bmp.Dispose()

Write-Output ("PNG: " + $pngPath + " (" + (Get-Item $pngPath).Length + " bytes)")
Write-Output ("ICO: " + $icoPath + " (" + (Get-Item $icoPath).Length + " bytes)")
