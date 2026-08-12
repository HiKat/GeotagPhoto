[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path

function Test-ExpectedSymbolicLink {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Item,

        [Parameter(Mandatory = $true)]
        [string]$ExpectedTarget,

        [Parameter(Mandatory = $true)]
        [string]$ExpectedSource
    )

    $linkType = $Item.PSObject.Properties['LinkType']
    $target = $Item.PSObject.Properties['Target']
    if ($null -eq $linkType -or $linkType.Value -ne 'SymbolicLink' -or $null -eq $target) {
        return $false
    }

    $targetValue = [string]@($target.Value)[0]
    if ($targetValue -eq $ExpectedTarget) {
        return $true
    }

    $candidateTarget = if ([System.IO.Path]::IsPathRooted($targetValue)) {
        $targetValue
    }
    else {
        Join-Path (Split-Path -Parent $Item.FullName) $targetValue
    }

    try {
        $resolvedTarget = (Resolve-Path -LiteralPath $candidateTarget).Path
        $resolvedSource = (Resolve-Path -LiteralPath (Join-Path $RepoRoot $ExpectedSource)).Path
        return $resolvedTarget -eq $resolvedSource
    }
    catch {
        return $false
    }
}

function New-RepoSymbolicLink {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LinkPath,

        [Parameter(Mandatory = $true)]
        [string]$SourcePath,

        [Parameter(Mandatory = $true)]
        [string]$LinkTargetPath,

        [Parameter(Mandatory = $true)]
        [ValidateSet('File', 'Directory')]
        [string]$Kind
    )

    $absoluteLink = Join-Path $RepoRoot $LinkPath
    $absoluteSource = Join-Path $RepoRoot $SourcePath
    $parent = Split-Path -Parent $absoluteLink

    $sourcePathType = if ($Kind -eq 'Directory') { 'Container' } else { 'Leaf' }
    if (-not (Test-Path -LiteralPath $absoluteSource -PathType $sourcePathType)) {
        throw "Source does not exist or is not a $($Kind.ToLowerInvariant()): $SourcePath"
    }

    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent | Out-Null
    }

    $existing = Get-Item -LiteralPath $absoluteLink -Force -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        if (Test-ExpectedSymbolicLink -Item $existing -ExpectedTarget $LinkTargetPath -ExpectedSource $SourcePath) {
            Write-Host "Already linked: $LinkPath -> $LinkTargetPath"
            return
        }

        if (-not $Force) {
            throw "Target already exists and is not the expected symbolic link: $LinkPath. Re-run with -Force to replace it."
        }

        $isReparsePoint = (($existing.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
        if ($existing.PSIsContainer -and -not $isReparsePoint) {
            throw "Refusing to remove an existing directory: $LinkPath. Remove or rename it manually, then re-run this script."
        }
    }

    $action = if ($null -eq $existing) {
        "Create symbolic link to $LinkTargetPath"
    }
    else {
        "Safely replace existing item with symbolic link to $LinkTargetPath"
    }
    if (-not $PSCmdlet.ShouldProcess($LinkPath, $action)) {
        return
    }

    $transactionId = [Guid]::NewGuid().ToString('N')
    $temporaryLink = Join-Path $parent ".__agent_link_new_$transactionId"
    $backupPath = Join-Path $parent ".__agent_link_old_$transactionId"
    $temporaryCreated = $false
    $existingMoved = $false
    $installed = $false
    $existingWasDirectory = $null -ne $existing -and $existing.PSIsContainer

    try {
        # 権限不足でも既存項目を失わないよう、新リンクを先に作成・検証する。
        try {
            Push-Location -LiteralPath $parent
            try {
                New-Item `
                    -ItemType SymbolicLink `
                    -Path (Split-Path -Leaf $temporaryLink) `
                    -Target $LinkTargetPath | Out-Null
            }
            finally {
                Pop-Location
            }
        }
        catch [System.UnauthorizedAccessException] {
            throw "Unable to create symbolic links. Run PowerShell as Administrator or enable Windows Developer Mode, then re-run this script."
        }
        $temporaryCreated = $true
        $temporaryItem = Get-Item -LiteralPath $temporaryLink -Force -ErrorAction Stop
        if (-not (Test-ExpectedSymbolicLink -Item $temporaryItem -ExpectedTarget $LinkTargetPath -ExpectedSource $SourcePath)) {
            throw "Created link did not resolve to the expected source: $LinkPath -> $LinkTargetPath"
        }

        if ($null -ne $existing) {
            if ($existingWasDirectory) {
                [System.IO.Directory]::Move($absoluteLink, $backupPath)
            }
            else {
                [System.IO.File]::Move($absoluteLink, $backupPath)
            }
            $existingMoved = $true
        }

        if ($Kind -eq 'Directory') {
            [System.IO.Directory]::Move($temporaryLink, $absoluteLink)
        }
        else {
            [System.IO.File]::Move($temporaryLink, $absoluteLink)
        }
        $temporaryCreated = $false
        $installed = $true

        $installedItem = Get-Item -LiteralPath $absoluteLink -Force -ErrorAction Stop
        if (-not (Test-ExpectedSymbolicLink -Item $installedItem -ExpectedTarget $LinkTargetPath -ExpectedSource $SourcePath)) {
            throw "Installed link did not resolve to the expected source: $LinkPath -> $LinkTargetPath"
        }

        if ($existingMoved) {
            if ($existingWasDirectory) {
                [System.IO.Directory]::Delete($backupPath)
            }
            else {
                [System.IO.File]::Delete($backupPath)
            }
            $existingMoved = $false
        }
        Write-Host "Linked: $LinkPath -> $LinkTargetPath"
    }
    catch {
        $originalError = $_
        $rollbackErrors = New-Object System.Collections.Generic.List[string]

        if ($installed) {
            try {
                $installedItem = Get-Item -LiteralPath $absoluteLink -Force -ErrorAction Stop
                if (-not (Test-ExpectedSymbolicLink -Item $installedItem -ExpectedTarget $LinkTargetPath -ExpectedSource $SourcePath)) {
                    throw "Refusing to remove an unexpected replacement: $absoluteLink"
                }
                if ($Kind -eq 'Directory') {
                    [System.IO.Directory]::Delete($absoluteLink)
                }
                else {
                    [System.IO.File]::Delete($absoluteLink)
                }
                $installed = $false
            }
            catch {
                $rollbackErrors.Add("Failed to remove the new link: $($_.Exception.Message)")
            }
        }

        if ($existingMoved) {
            try {
                if (Test-Path -LiteralPath $absoluteLink) {
                    throw "Cannot restore the original item because the destination exists: $absoluteLink"
                }
                if ($existingWasDirectory) {
                    [System.IO.Directory]::Move($backupPath, $absoluteLink)
                }
                else {
                    [System.IO.File]::Move($backupPath, $absoluteLink)
                }
                $existingMoved = $false
            }
            catch {
                $rollbackErrors.Add("Failed to restore the original item: $($_.Exception.Message)")
            }
        }

        if ($rollbackErrors.Count -gt 0) {
            throw (
                "Symbolic-link update failed: $($originalError.Exception.Message)`n" +
                "Rollback also failed: $($rollbackErrors -join '; ')`n" +
                "Recovery paths: $absoluteLink, $backupPath, $temporaryLink"
            )
        }
        throw "Symbolic-link update failed; the original item was preserved or restored: $($originalError.Exception.Message)"
    }
    finally {
        if ($temporaryCreated) {
            try {
                $temporaryItem = Get-Item -LiteralPath $temporaryLink -Force -ErrorAction SilentlyContinue
                if ($null -ne $temporaryItem -and $temporaryItem.LinkType -eq 'SymbolicLink') {
                    if ($Kind -eq 'Directory') {
                        [System.IO.Directory]::Delete($temporaryLink)
                    }
                    else {
                        [System.IO.File]::Delete($temporaryLink)
                    }
                }
            }
            catch {
                Write-Warning "Temporary link cleanup failed; inspect manually: $temporaryLink ($($_.Exception.Message))"
            }
        }
    }
}

New-RepoSymbolicLink -LinkPath '.agents\skills' -SourcePath '.github\skills' -LinkTargetPath '..\.github\skills' -Kind Directory
New-RepoSymbolicLink -LinkPath 'AGENTS.md' -SourcePath '.github\copilot-instructions.md' -LinkTargetPath '.github\copilot-instructions.md' -Kind File
