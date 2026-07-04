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

        if ($PSCmdlet.ShouldProcess($LinkPath, 'Replace existing item')) {
            $isReparsePoint = (($existing.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
            if ($existing.PSIsContainer -and -not $isReparsePoint) {
                throw "Refusing to remove an existing directory: $LinkPath. Remove or rename it manually, then re-run this script."
            }

            Remove-Item -LiteralPath $absoluteLink -Force
        }
    }

    if ($PSCmdlet.ShouldProcess($LinkPath, "Create symbolic link to $LinkTargetPath")) {
        $linkName = Split-Path -Leaf $absoluteLink
        Push-Location -LiteralPath $parent
        try {
            try {
                New-Item -ItemType SymbolicLink -Path $linkName -Target $LinkTargetPath | Out-Null
            }
            catch [System.UnauthorizedAccessException] {
                throw "Unable to create symbolic links. Run PowerShell as Administrator or enable Windows Developer Mode, then re-run this script."
            }
        }
        finally {
            Pop-Location
        }
        Write-Host "Linked: $LinkPath -> $LinkTargetPath"
    }
}

New-RepoSymbolicLink -LinkPath '.agents\skills' -SourcePath '.github\skills' -LinkTargetPath '..\.github\skills' -Kind Directory
New-RepoSymbolicLink -LinkPath 'AGENTS.md' -SourcePath '.github\copilot-instructions.md' -LinkTargetPath '.github\copilot-instructions.md' -Kind File
