param([string]$Python = 'python')

$ErrorActionPreference = 'Stop'
$previousPlatform = $env:QT_QPA_PLATFORM
$previousStyle = $env:QT_STYLE_OVERRIDE
$previousScale = $env:QT_SCALE_FACTOR
$tests = @(
    'tests/test_editor.py',
    'tests/test_editor_controls.py',
    'tests/test_annotation_preferences.py',
    'tests/test_pin_interactions.py'
)

try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    foreach ($style in @('windows11', 'Fusion')) {
        $env:QT_STYLE_OVERRIDE = $style
        & $Python -c 'import os; from PySide6.QtWidgets import QApplication; app = QApplication([]); actual = app.style().objectName(); assert actual.casefold() == os.environ["QT_STYLE_OVERRIDE"].casefold(), actual'
        if ($LASTEXITCODE -ne 0) { throw "Qt did not load the $style theme." }
        foreach ($scale in @('1', '1.25', '1.5', '2')) {
            $env:QT_SCALE_FACTOR = $scale
            Write-Output "Qt style $style at scale $scale"
            & $Python -m pytest -q @tests
            if ($LASTEXITCODE -ne 0) { throw "Interaction tests failed for $style at scale $scale." }
        }
    }
} finally {
    $env:QT_QPA_PLATFORM = $previousPlatform
    $env:QT_STYLE_OVERRIDE = $previousStyle
    $env:QT_SCALE_FACTOR = $previousScale
}
