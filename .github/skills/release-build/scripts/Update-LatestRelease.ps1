[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$')]
    [string]$Version,

    [Parameter(Mandatory = $false)]
    [ValidateSet('Candidate', 'Published')]
    [string]$Phase = 'Candidate',

    [Parameter(Mandatory = $false)]
    [ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')]
    [string]$Repository = 'HiKat/GeotagPhoto',

    [Parameter(Mandatory = $false)]
    [string]$RepositoryRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $pathRoot = [System.IO.Path]::GetPathRoot($fullPath)
    $trimmedPath = $fullPath.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $trimmedRoot = $pathRoot.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    if ([string]::Equals(
        $trimmedPath,
        $trimmedRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        return $pathRoot
    }
    return $trimmedPath
}

function Get-ExistingItemIncludingLink {
    param(
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $matches = @(Get-ChildItem -LiteralPath $Parent -Force -ErrorAction Stop |
        Where-Object {
            [string]::Equals($_.Name, $Name, [System.StringComparison]::OrdinalIgnoreCase)
        })
    if ($matches.Count -gt 1) {
        throw "大文字・小文字だけが異なる同名項目があります: $Parent -> $Name"
    }
    if ($matches.Count -eq 1) {
        return $matches[0]
    }
    return $null
}

function Assert-PathWithinRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $false)][switch]$AllowRoot
    )

    $normalizedPath = Get-NormalizedPath $Path
    $normalizedRoot = Get-NormalizedPath $Root
    if ($AllowRoot -and [string]::Equals(
        $normalizedPath,
        $normalizedRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        return $normalizedPath
    }

    $rootPrefix = $normalizedRoot
    if (-not $rootPrefix.EndsWith(
        [string][System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::Ordinal
    )) {
        $rootPrefix += [System.IO.Path]::DirectorySeparatorChar
    }
    if (-not $normalizedPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "許可されたルート外のパスです。ルート=$normalizedRoot, パス=$normalizedPath"
    }
    return $normalizedPath
}

function Assert-DirectChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $false)][string]$ExpectedName
    )

    $normalizedPath = Assert-PathWithinRoot -Path $Path -Root $Parent
    $normalizedParent = Get-NormalizedPath $Parent
    $actualParent = Get-NormalizedPath ([System.IO.Path]::GetDirectoryName($normalizedPath))
    if (-not [string]::Equals(
        $actualParent,
        $normalizedParent,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "直下のパスではありません。親=$normalizedParent, パス=$normalizedPath"
    }
    if (-not [string]::IsNullOrWhiteSpace($ExpectedName)) {
        $actualName = [System.IO.Path]::GetFileName($normalizedPath)
        if (-not [string]::Equals(
            $actualName,
            $ExpectedName,
            [System.StringComparison]::Ordinal
        )) {
            throw "期待した名前と一致しません。期待=$ExpectedName, 実際=$actualName"
        }
    }
    return $normalizedPath
}

function Assert-NotReparsePoint {
    param([Parameter(Mandatory = $true)]$Item)

    if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "再解析ポイントは使用できません: $($Item.FullName)"
    }
}

function Get-SymbolicLinkTarget {
    param([Parameter(Mandatory = $true)]$Item)

    if ($Item.LinkType -ne 'SymbolicLink') {
        throw "シンボリックリンクではありません: $($Item.FullName) (LinkType=$($Item.LinkType))"
    }

    $rawTarget = [string]($Item.Target | Select-Object -First 1)
    if ([string]::IsNullOrWhiteSpace($rawTarget)) {
        throw "シンボリックリンクのリンク先を取得できません: $($Item.FullName)"
    }
    if (-not [System.IO.Path]::IsPathRooted($rawTarget)) {
        $rawTarget = Join-Path $Item.Parent.FullName $rawTarget
    }
    return Get-NormalizedPath $rawTarget
}

function Assert-RequiredFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $false)][string]$AllowedRoot
    )

    if (-not [string]::IsNullOrWhiteSpace($AllowedRoot)) {
        $null = Assert-PathWithinRoot -Path $Path -Root $AllowedRoot
    }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item -or $item.PSIsContainer) {
        throw "必須ファイルがありません: $Path"
    }
    Assert-NotReparsePoint $item
    if ($item.Length -le 0) {
        throw "必須ファイルが空です: $Path"
    }
}

function Assert-RequiredDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $false)][string]$AllowedRoot
    )

    if (-not [string]::IsNullOrWhiteSpace($AllowedRoot)) {
        $null = Assert-PathWithinRoot -Path $Path -Root $AllowedRoot
    }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item -or -not $item.PSIsContainer) {
        throw "必須ディレクトリがありません: $Path"
    }
    Assert-NotReparsePoint $item
    $descendants = @(Get-ChildItem -LiteralPath $Path -Force -Recurse -ErrorAction Stop)
    foreach ($descendant in $descendants) {
        Assert-NotReparsePoint $descendant
    }
    if (-not ($descendants | Where-Object { -not $_.PSIsContainer } | Select-Object -First 1)) {
        throw "必須ディレクトリが空です: $Path"
    }
}

function Read-Utf8TextStrict {
    param([Parameter(Mandatory = $true)][string]$Path)

    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $content = $utf8.GetString($bytes)
    if ($content.Length -gt 0 -and $content[0] -eq [char]0xFEFF) {
        return $content.Substring(1)
    }
    return $content
}

function Assert-TextContains {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$ExpectedValues
    )

    Assert-RequiredFile $Path
    $content = Read-Utf8TextStrict $Path
    foreach ($expectedValue in $ExpectedValues) {
        if (-not $content.Contains($expectedValue)) {
            throw "必要なバージョン表記がありません: $Path -> $expectedValue"
        }
    }
}

function ConvertTo-ReleaseVersion {
    param([Parameter(Mandatory = $true)][string]$Tag)

    try {
        return [System.Version]::Parse($Tag.Substring(1))
    }
    catch {
        throw "比較可能なバージョンではありません: $Tag ($($_.Exception.Message))"
    }
}

function Get-ReleaseVersionDirectories {
    param([Parameter(Mandatory = $true)][string]$ReleaseRoot)

    $results = New-Object System.Collections.Generic.List[object]
    foreach ($item in @(Get-ChildItem -LiteralPath $ReleaseRoot -Force -ErrorAction Stop)) {
        if (-not $item.PSIsContainer -or $item.Name -notmatch '^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$') {
            continue
        }
        Assert-NotReparsePoint $item
        $results.Add([PSCustomObject]@{
            Tag = $item.Name
            ParsedVersion = ConvertTo-ReleaseVersion $item.Name
            Path = Get-NormalizedPath $item.FullName
        })
    }
    return $results.ToArray()
}

function Assert-ReleaseNotesHeading {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Version
    )

    Assert-RequiredFile $Path
    $content = Read-Utf8TextStrict $Path
    $firstLine = @($content -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -First 1)
    if ($firstLine.Count -ne 1) {
        throw "リリースノートに見出しがありません: $Path"
    }
    $versionPattern = [System.Text.RegularExpressions.Regex]::Escape($Version)
    $headingPattern = (
        '^## 変更内容 \((?:' +
        'v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*) → ' +
        ')?' + $versionPattern + '\)$'
    )
    if (-not [System.Text.RegularExpressions.Regex]::IsMatch(
        [string]$firstLine[0],
        $headingPattern,
        [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
    )) {
        throw (
            "リリースノートの先頭見出しで遷移先バージョンが厳密一致しません。" +
            "期待する終端=$Version, 実際='$($firstLine[0])'"
        )
    }
}

function Get-StreamSha256 {
    param([Parameter(Mandatory = $true)][System.IO.Stream]$Stream)

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha256.ComputeHash($Stream))).Replace('-', '')
    }
    finally {
        $sha256.Dispose()
    }
}

function Get-DistributionManifest {
    param([Parameter(Mandatory = $true)][string]$DistributionDirectory)

    Assert-RequiredDirectory $DistributionDirectory
    $root = Get-NormalizedPath $DistributionDirectory
    $manifest = [System.Collections.Generic.Dictionary[string,object]]::new(
        [System.StringComparer]::Ordinal
    )
    $caseInsensitiveNames = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    foreach ($item in @(Get-ChildItem -LiteralPath $root -Force -Recurse -ErrorAction Stop)) {
        Assert-NotReparsePoint $item
        if ($item.PSIsContainer) {
            continue
        }
        $itemPath = Assert-PathWithinRoot -Path $item.FullName -Root $root
        $relativeName = $itemPath.Substring($root.Length + 1).Replace('\', '/')
        if (-not $caseInsensitiveNames.Add($relativeName)) {
            throw "配布フォルダーに大文字・小文字だけが異なるパスがあります: $relativeName"
        }
        if ($manifest.ContainsKey($relativeName)) {
            throw "配布フォルダーに重複パスがあります: $relativeName"
        }
        $manifest.Add($relativeName, [PSCustomObject]@{
            Length = [long]$item.Length
            Sha256 = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash
        })
    }
    if ($manifest.Count -eq 0) {
        throw "配布フォルダーにファイルがありません: $DistributionDirectory"
    }
    return ,$manifest
}

function Get-SafeZipEntryName {
    param([Parameter(Mandatory = $true)][string]$EntryName)

    if ([string]::IsNullOrWhiteSpace($EntryName)) {
        throw 'ZIP に空のエントリ名があります。'
    }
    $normalizedName = $EntryName.Replace('\', '/')
    if ($normalizedName.StartsWith('/', [System.StringComparison]::Ordinal) -or
        $normalizedName.StartsWith('//', [System.StringComparison]::Ordinal) -or
        $normalizedName -match '^[A-Za-z]:' -or
        $normalizedName.Contains(':')) {
        throw "ZIP に絶対パスまたは ADS と解釈されるエントリがあります: $EntryName"
    }

    $isDirectory = $normalizedName.EndsWith('/', [System.StringComparison]::Ordinal)
    $canonicalName = $normalizedName.TrimEnd('/')
    $segments = @($canonicalName.Split('/'))
    if ($segments.Count -eq 0) {
        throw "ZIP のエントリ名が不正です: $EntryName"
    }
    foreach ($segment in $segments) {
        if ([string]::IsNullOrEmpty($segment) -or $segment -eq '.' -or $segment -eq '..') {
            throw "ZIP に空要素・現在参照・親参照を含むエントリがあります: $EntryName"
        }
        if ($segment.EndsWith(' ', [System.StringComparison]::Ordinal) -or
            $segment.EndsWith('.', [System.StringComparison]::Ordinal)) {
            throw "ZIP に Windows 上で曖昧になるエントリがあります: $EntryName"
        }
    }
    return [PSCustomObject]@{
        CanonicalName = $canonicalName
        IsDirectory = $isDirectory
    }
}

function Assert-ZipPackage {
    param(
        [Parameter(Mandatory = $true)][string]$ZipPath,
        [Parameter(Mandatory = $true)]$ExpectedManifest
    )

    Assert-RequiredFile $ZipPath
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        $seenNames = [System.Collections.Generic.HashSet[string]]::new(
            [System.StringComparer]::Ordinal
        )
        $seenNamesIgnoreCase = [System.Collections.Generic.HashSet[string]]::new(
            [System.StringComparer]::OrdinalIgnoreCase
        )
        $zipManifest = [System.Collections.Generic.Dictionary[string,object]]::new(
            [System.StringComparer]::Ordinal
        )
        foreach ($entry in $archive.Entries) {
            $safeName = Get-SafeZipEntryName $entry.FullName
            $canonicalName = [string]$safeName.CanonicalName
            if (-not $seenNames.Add($canonicalName)) {
                throw "ZIP に重複エントリがあります: $canonicalName"
            }
            if (-not $seenNamesIgnoreCase.Add($canonicalName)) {
                throw "ZIP に大文字・小文字だけが異なる衝突エントリがあります: $canonicalName"
            }

            $externalAttributesBytes = [System.BitConverter]::GetBytes([int]$entry.ExternalAttributes)
            $externalAttributes = [System.BitConverter]::ToUInt32($externalAttributesBytes, 0)
            $unixFileType = (($externalAttributes -shr 16) -band 0xF000)
            if ($unixFileType -eq 0xA000) {
                throw "ZIP にシンボリックリンクがあります: $canonicalName"
            }

            if (-not ($canonicalName -eq 'GeotagPhoto' -or
                $canonicalName.StartsWith('GeotagPhoto/', [System.StringComparison]::Ordinal))) {
                throw "ZIP に配布ルート外のエントリがあります: $canonicalName"
            }
            if ($safeName.IsDirectory) {
                continue
            }
            if ($canonicalName -eq 'GeotagPhoto') {
                throw 'ZIP の配布ルートがファイルになっています: GeotagPhoto'
            }

            $relativeName = $canonicalName.Substring('GeotagPhoto/'.Length)
            if (-not $ExpectedManifest.ContainsKey($relativeName)) {
                throw "ZIP に配布フォルダー外のファイルがあります: $canonicalName"
            }
            $expected = $ExpectedManifest[$relativeName]
            if ([long]$entry.Length -ne [long]$expected.Length) {
                throw "ZIP 内外のファイルサイズが一致しません: $relativeName (ZIP=$($entry.Length), 展開先=$($expected.Length))"
            }
            $entryStream = $entry.Open()
            try {
                $entryHash = Get-StreamSha256 $entryStream
            }
            finally {
                $entryStream.Dispose()
            }
            if (-not [string]::Equals(
                $entryHash,
                [string]$expected.Sha256,
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
                throw "ZIP 内外の SHA-256 が一致しません: $relativeName (ZIP=$entryHash, 展開先=$($expected.Sha256))"
            }
            $zipManifest.Add($relativeName, $expected)
        }

        if ($zipManifest.Count -ne $ExpectedManifest.Count) {
            $missing = @($ExpectedManifest.Keys | Where-Object { -not $zipManifest.ContainsKey($_) })
            throw (
                "ZIP と配布フォルダーのファイル数が一致しません。" +
                "ZIP=$($zipManifest.Count), 展開先=$($ExpectedManifest.Count), " +
                "不足=$(if ($missing.Count -gt 0) { $missing -join ', ' } else { '(なし)' })"
            )
        }
    }
    finally {
        $archive.Dispose()
    }
}

function Invoke-GitCommand {
    param(
        [Parameter(Mandatory = $true)][string]$GitPath,
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $output = @(& $GitPath -C $RepositoryRoot @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    $outputLines = @($output | ForEach-Object { [string]$_ })
    if ($exitCode -ne 0) {
        throw "git.exe が失敗しました (exit=$exitCode): git $($Arguments -join ' ')`n$($outputLines -join "`n")"
    }
    return $outputLines
}

function Invoke-GhCommand {
    param(
        [Parameter(Mandatory = $true)][string]$GhPath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $output = @(& $GhPath @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    $outputLines = @($output | ForEach-Object { [string]$_ })
    if ($exitCode -ne 0) {
        throw "gh.exe が失敗しました (exit=$exitCode): gh $($Arguments -join ' ')`n$($outputLines -join "`n")"
    }
    return $outputLines
}

function Resolve-ApplicationExecutable {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $false)][string[]]$AdditionalCandidates = @()
    )

    $commands = @(Get-Command -Name $Name -CommandType Application -ErrorAction SilentlyContinue)
    $candidates = New-Object System.Collections.Generic.List[string]
    foreach ($command in $commands) {
        if (-not [string]::IsNullOrWhiteSpace($command.Source)) {
            $candidates.Add([string]$command.Source)
        }
    }
    foreach ($candidate in $AdditionalCandidates) {
        if (-not [string]::IsNullOrWhiteSpace($candidate)) {
            $candidates.Add($candidate)
        }
    }

    foreach ($candidate in $candidates) {
        $item = Get-Item -LiteralPath $candidate -Force -ErrorAction SilentlyContinue
        if ($null -eq $item -or $item.PSIsContainer) {
            continue
        }
        $normalizedPath = Get-NormalizedPath $item.FullName
        if (-not [string]::Equals(
            [System.IO.Path]::GetFileName($normalizedPath),
            $Name,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            continue
        }
        Assert-NotReparsePoint $item
        if ($item.Length -le 0) {
            throw "実行ファイルが空です: $normalizedPath"
        }
        return $normalizedPath
    }
    throw "実行可能な $Name の実体が見つかりません。"
}

function ConvertTo-NormalizedNewlines {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text)

    return $Text.Replace("`r`n", "`n").Replace("`r", "`n")
}

function Get-SingleGitLine {
    param(
        [Parameter(Mandatory = $true)][string]$GitPath,
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $lines = @(Invoke-GitCommand -GitPath $GitPath -RepositoryRoot $RepositoryRoot -Arguments $Arguments)
    if ($lines.Count -ne 1 -or [string]::IsNullOrWhiteSpace($lines[0])) {
        throw "git.exe の出力が1行ではありません: git $($Arguments -join ' ')"
    }
    return ([string]$lines[0]).Trim()
}

function Get-LsRemoteEntries {
    param(
        [Parameter(Mandatory = $true)][string]$GitPath,
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string[]]$RequestedRefs
    )

    $arguments = @('ls-remote', '--exit-code', 'origin') + $RequestedRefs
    $lines = @(Invoke-GitCommand -GitPath $GitPath -RepositoryRoot $RepositoryRoot -Arguments $arguments)
    $requestedRefSet = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($requestedRef in $RequestedRefs) {
        if (-not $requestedRefSet.Add($requestedRef)) {
            throw "同じリモート参照が重複指定されました: $requestedRef"
        }
    }
    $entries = [System.Collections.Generic.Dictionary[string,string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($line in $lines) {
        if ($line -notmatch '^([0-9a-fA-F]{40}|[0-9a-fA-F]{64})\t(.+)$') {
            throw "git ls-remote の出力形式が不正です: $line"
        }
        $objectId = $Matches[1]
        $refName = $Matches[2]
        if (-not $requestedRefSet.Contains($refName)) {
            throw "git ls-remote が要求外の参照を返しました: $refName"
        }
        if ($entries.ContainsKey($refName)) {
            throw "git ls-remote が同じ参照を複数返しました: $refName"
        }
        $entries.Add($refName, $objectId)
    }
    return ,$entries
}

function Assert-TaggedFileMatchesWorkingTree {
    param(
        [Parameter(Mandatory = $true)][string]$GitPath,
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$TagRef,
        [Parameter(Mandatory = $true)][string]$RepositoryPath
    )

    $objectSpec = "${TagRef}:$RepositoryPath"
    $objectType = Get-SingleGitLine -GitPath $GitPath -RepositoryRoot $RepositoryRoot `
        -Arguments @('cat-file', '-t', $objectSpec)
    if (-not [string]::Equals($objectType, 'blob', [System.StringComparison]::Ordinal)) {
        throw "タグ内の対象が blob ではありません: $objectSpec (type=$objectType)"
    }
    $tagBlob = Get-SingleGitLine -GitPath $GitPath -RepositoryRoot $RepositoryRoot `
        -Arguments @('rev-parse', '--verify', $objectSpec)
    $workingBlob = Get-SingleGitLine -GitPath $GitPath -RepositoryRoot $RepositoryRoot `
        -Arguments @('hash-object', '--', $RepositoryPath)
    if (-not [string]::Equals($tagBlob, $workingBlob, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "タグ内と作業ツリーのファイルが一致しません: $RepositoryPath (tag=$tagBlob, working=$workingBlob)"
    }
}

function Remove-ValidatedTemporaryLink {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ReleaseRoot,
        [Parameter(Mandatory = $true)][string]$ExpectedName
    )

    $validatedPath = Assert-DirectChildPath -Path $Path -Parent $ReleaseRoot -ExpectedName $ExpectedName
    $item = Get-Item -LiteralPath $validatedPath -Force -ErrorAction Stop
    if ($item.LinkType -ne 'SymbolicLink') {
        throw "削除対象は SymbolicLink ではありません: $validatedPath (LinkType=$($item.LinkType))"
    }
    [System.IO.Directory]::Delete($validatedPath)
}

function Remove-ValidatedTemporaryDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$TemporaryRoot,
        [Parameter(Mandatory = $true)][string]$ExpectedName
    )

    $validatedPath = Assert-DirectChildPath `
        -Path $Path `
        -Parent $TemporaryRoot `
        -ExpectedName $ExpectedName
    $item = Get-ExistingItemIncludingLink -Parent $TemporaryRoot -Name $ExpectedName
    if ($null -eq $item) {
        return
    }
    if (-not $item.PSIsContainer) {
        throw "一時削除対象がディレクトリではありません: $validatedPath"
    }
    Assert-NotReparsePoint $item
    foreach ($descendant in @(Get-ChildItem -LiteralPath $validatedPath -Force -Recurse -ErrorAction Stop)) {
        $null = Assert-PathWithinRoot -Path $descendant.FullName -Root $validatedPath
        Assert-NotReparsePoint $descendant
    }
    [System.IO.Directory]::Delete($validatedPath, $true)
}

function Assert-GitHubRelease {
    param(
        [Parameter(Mandatory = $true)][string]$GhPath,
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string]$Version,
        [Parameter(Mandatory = $true)][string]$ZipPath,
        [Parameter(Mandatory = $true)][string]$ReleaseNotesPath
    )

    $releaseJsonLines = @(Invoke-GhCommand -GhPath $GhPath -Arguments @(
        'release', 'view', $Version,
        '--repo', $Repository,
        '--json', 'tagName,isDraft,isPrerelease,assets,body'
    ))
    if ($releaseJsonLines.Count -eq 0) {
        throw "GitHub Release の情報が空です: $Repository $Version"
    }
    try {
        $published = ($releaseJsonLines -join "`n") | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "GitHub Release の JSON を解析できません: $($_.Exception.Message)"
    }
    if (-not [string]::Equals(
        [string]$published.tagName,
        $Version,
        [System.StringComparison]::Ordinal
    )) {
        throw "GitHub Release のタグが一致しません。期待=$Version, 実際=$($published.tagName)"
    }
    if ([bool]$published.isDraft -or [bool]$published.isPrerelease) {
        throw "GitHub Release が公開済み安定版ではありません。draft=$($published.isDraft), prerelease=$($published.isPrerelease)"
    }

    $expectedAssetName = "GeotagPhoto-$Version-win64.zip"
    $matchingAssets = @($published.assets | Where-Object {
        [string]::Equals(
            [string]$_.name,
            $expectedAssetName,
            [System.StringComparison]::Ordinal
        )
    })
    if ($matchingAssets.Count -ne 1) {
        throw "GitHub Release の期待 ZIP アセットが1件ではありません: $expectedAssetName (件数=$($matchingAssets.Count))"
    }
    $localZipItem = Get-Item -LiteralPath $ZipPath -Force -ErrorAction Stop
    if ([long]$matchingAssets[0].size -ne [long]$localZipItem.Length) {
        throw "GitHub Release の ZIP サイズが一致しません。remote=$($matchingAssets[0].size), local=$($localZipItem.Length)"
    }

    $releaseNotes = ConvertTo-NormalizedNewlines (Read-Utf8TextStrict $ReleaseNotesPath)
    $releaseBody = ConvertTo-NormalizedNewlines ([string]$published.body)
    if (-not [string]::Equals(
        $releaseBody,
        $releaseNotes,
        [System.StringComparison]::Ordinal
    )) {
        throw 'GitHub Release の本文が release_notes.md と一致しません（CRLF/LF の差は正規化済み）。'
    }

    $temporaryRootItem = Get-Item -LiteralPath ([System.IO.Path]::GetTempPath()) -Force -ErrorAction Stop
    if (-not $temporaryRootItem.PSIsContainer) {
        throw "TEMP ルートがディレクトリではありません: $($temporaryRootItem.FullName)"
    }
    Assert-NotReparsePoint $temporaryRootItem
    $temporaryRoot = Get-NormalizedPath $temporaryRootItem.FullName
    $temporaryName = "GeotagPhoto-release-verify-$([System.Guid]::NewGuid().ToString('N'))"
    $temporaryDirectory = Assert-DirectChildPath `
        -Path (Join-Path $temporaryRoot $temporaryName) `
        -Parent $temporaryRoot `
        -ExpectedName $temporaryName
    if ($null -ne (Get-Item -LiteralPath $temporaryDirectory -Force -ErrorAction SilentlyContinue)) {
        throw "一意であるべき検証用 TEMP ディレクトリが既に存在します: $temporaryDirectory"
    }
    [System.IO.Directory]::CreateDirectory($temporaryDirectory) | Out-Null
    $createdTemporaryItem = Get-Item -LiteralPath $temporaryDirectory -Force -ErrorAction Stop
    Assert-NotReparsePoint $createdTemporaryItem

    $validationError = $null
    try {
        $null = Invoke-GhCommand -GhPath $GhPath -Arguments @(
            'release', 'download', $Version,
            '--repo', $Repository,
            '--pattern', $expectedAssetName,
            '--dir', $temporaryDirectory
        )
        $downloadedItems = @(Get-ChildItem -LiteralPath $temporaryDirectory -Force -ErrorAction Stop)
        if ($downloadedItems.Count -ne 1 -or $downloadedItems[0].PSIsContainer -or
            -not [string]::Equals(
                $downloadedItems[0].Name,
                $expectedAssetName,
                [System.StringComparison]::Ordinal
            )) {
            throw "GitHub Release から期待 ZIP だけを取得できませんでした: $temporaryDirectory"
        }
        Assert-NotReparsePoint $downloadedItems[0]
        if ([long]$downloadedItems[0].Length -ne [long]$localZipItem.Length) {
            throw "ダウンロード後の ZIP サイズが一致しません。remote=$($downloadedItems[0].Length), local=$($localZipItem.Length)"
        }
        $downloadedHash = (Get-FileHash -LiteralPath $downloadedItems[0].FullName -Algorithm SHA256).Hash
        $localHash = (Get-FileHash -LiteralPath $localZipItem.FullName -Algorithm SHA256).Hash
        if (-not [string]::Equals(
            $downloadedHash,
            $localHash,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "GitHub Release の ZIP SHA-256 がローカル成果物と一致しません。remote=$downloadedHash, local=$localHash"
        }
    }
    catch {
        $validationError = $_
    }

    try {
        Remove-ValidatedTemporaryDirectory `
            -Path $temporaryDirectory `
            -TemporaryRoot $temporaryRoot `
            -ExpectedName $temporaryName
    }
    catch {
        if ($null -ne $validationError) {
            throw (
                "GitHub Release 検証失敗: $($validationError.Exception.Message)`n" +
                "検証用 TEMP の安全な削除にも失敗しました: $($_.Exception.Message)`n" +
                "確認対象: $temporaryDirectory"
            )
        }
        throw "検証用 TEMP の安全な削除に失敗しました: $($_.Exception.Message)（確認対象: $temporaryDirectory）"
    }
    if ($null -ne $validationError) {
        throw $validationError
    }
}

function Assert-LatestLink {
    param(
        [Parameter(Mandatory = $true)][string]$ReleaseRoot,
        [Parameter(Mandatory = $true)][string]$ExpectedTarget,
        [Parameter(Mandatory = $true)][string]$ExpectedExeHash
    )

    $latestItem = Get-ExistingItemIncludingLink -Parent $ReleaseRoot -Name 'latest'
    if ($null -eq $latestItem) {
        throw "releases/latest がありません: $ReleaseRoot"
    }
    $actualTarget = Get-SymbolicLinkTarget $latestItem
    if ($actualTarget -ne $ExpectedTarget) {
        throw "releases/latest のリンク先が一致しません。期待=$ExpectedTarget, 実際=$actualTarget"
    }

    $latestExe = Join-Path $latestItem.FullName 'GeotagPhoto\GeotagPhoto.exe'
    Assert-RequiredFile $latestExe
    $latestExeHash = (Get-FileHash -LiteralPath $latestExe -Algorithm SHA256).Hash
    if ($latestExeHash -ne $ExpectedExeHash) {
        throw "releases/latest 経由の EXE が候補版と一致しません。latest=$latestExeHash, 候補版=$ExpectedExeHash"
    }
    return $actualTarget
}

if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = Join-Path $PSScriptRoot '..\..\..\..'
}
$repoRootItem = Get-Item -LiteralPath (Resolve-Path -LiteralPath $RepositoryRoot -ErrorAction Stop).Path `
    -Force -ErrorAction Stop
if (-not $repoRootItem.PSIsContainer) {
    throw "リポジトリルートがディレクトリではありません: $RepositoryRoot"
}
Assert-NotReparsePoint $repoRootItem
$repoRoot = Get-NormalizedPath $repoRootItem.FullName
$releaseRoot = Assert-DirectChildPath `
    -Path (Join-Path $repoRoot 'releases') `
    -Parent $repoRoot `
    -ExpectedName 'releases'
$releaseRootItem = Get-Item -LiteralPath $releaseRoot -Force -ErrorAction SilentlyContinue
if ($null -eq $releaseRootItem -or -not $releaseRootItem.PSIsContainer) {
    throw "releases ディレクトリがありません: $releaseRoot"
}
Assert-NotReparsePoint $releaseRootItem

$mutexSha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    $mutexNameHash = ([System.BitConverter]::ToString(
        $mutexSha256.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($releaseRoot.ToLowerInvariant()))
    )).Replace('-', '')
}
finally {
    $mutexSha256.Dispose()
}
$latestMutex = New-Object System.Threading.Mutex($false, "Local\GeotagPhoto.ReleaseLatest.$mutexNameHash")
$mutexAcquired = $false
$releaseLockStream = $null

try {
    try {
        $mutexAcquired = $latestMutex.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        $mutexAcquired = $true
    }
    if (-not $mutexAcquired) {
        throw '別のリリース処理が releases/latest を更新中です。完了後に再実行してください。'
    }

    $lockPath = Assert-DirectChildPath `
        -Path (Join-Path $releaseRoot '.geotagphoto-release.lock') `
        -Parent $releaseRoot `
        -ExpectedName '.geotagphoto-release.lock'
    $existingLockItem = Get-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    if ($null -ne $existingLockItem) {
        if ($existingLockItem.PSIsContainer) {
            throw "排他ロックパスがファイルではありません: $lockPath"
        }
        Assert-NotReparsePoint $existingLockItem
    }
    try {
        $releaseLockStream = [System.IO.File]::Open(
            $lockPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
    }
    catch {
        throw "別の処理がリリースを検証または更新中か、ロックを取得できません: $lockPath ($($_.Exception.Message))"
    }
    $openedLockItem = Get-Item -LiteralPath $lockPath -Force -ErrorAction Stop
    if ($openedLockItem.PSIsContainer) {
        throw "排他ロックパスがファイルではありません: $lockPath"
    }
    Assert-NotReparsePoint $openedLockItem

    # ここから検証・公開確認・latest 更新が完了するまで、ファイルロックを保持する。
    $orphanedLinks = @(Get-ChildItem -LiteralPath $releaseRoot -Force -ErrorAction Stop |
        Where-Object { $_.Name -like 'latest.__new.*' -or $_.Name -like 'latest.__old.*' })
    if ($orphanedLinks.Count -gt 0) {
        throw (
            '前回の latest 更新で残った可能性がある退避リンクを検出しました。' +
            '自動変更せず停止します: ' +
            (($orphanedLinks | ForEach-Object { $_.FullName }) -join ', ')
        )
    }

    $requestedVersion = ConvertTo-ReleaseVersion $Version
    $releaseVersions = @(Get-ReleaseVersionDirectories $releaseRoot)
    $newerReleases = @($releaseVersions |
        Where-Object { $_.ParsedVersion -gt $requestedVersion } |
        Sort-Object ParsedVersion)
    if ($Phase -eq 'Candidate' -and $newerReleases.Count -gt 0) {
        throw (
            "既存の新しいリリースより古いバージョンへ latest を戻せません: " +
            "要求=$Version, 新しい版=$(($newerReleases | ForEach-Object { $_.Tag }) -join ', ')"
        )
    }
    $existingLatest = Get-ExistingItemIncludingLink -Parent $releaseRoot -Name 'latest'
    $existingTarget = $null
    if ($null -ne $existingLatest) {
        if ($existingLatest.LinkType -ne 'SymbolicLink') {
            throw "releases/latest はディレクトリ・シンボリックリンクではありません。自動変更しません: $($existingLatest.FullName)"
        }
        $existingTarget = Get-SymbolicLinkTarget $existingLatest
        $existingTarget = Assert-DirectChildPath -Path $existingTarget -Parent $releaseRoot
        $existingTargetTag = [System.IO.Path]::GetFileName($existingTarget)
        if ($existingTargetTag -notmatch '^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$') {
            throw "releases/latest のリンク先名がリリース形式ではありません: $existingTarget"
        }
        $existingTargetVersion = ConvertTo-ReleaseVersion $existingTargetTag
        if ($Phase -eq 'Candidate' -and $existingTargetVersion -gt $requestedVersion) {
            throw "releases/latest を古いバージョンへ戻せません。要求=$Version, 現在=$existingTargetTag"
        }
    }

    $releaseDirectory = Assert-DirectChildPath `
        -Path (Join-Path $releaseRoot $Version) `
        -Parent $releaseRoot `
        -ExpectedName $Version
    $releaseItem = Get-Item -LiteralPath $releaseDirectory -Force -ErrorAction Stop
    if (-not $releaseItem.PSIsContainer) {
        throw "候補版ディレクトリがありません: $releaseDirectory"
    }
    Assert-NotReparsePoint $releaseItem
    $releaseDirectory = Get-NormalizedPath $releaseItem.FullName
    if ($Phase -eq 'Published') {
        if ($null -eq $existingLatest) {
            throw "Published 検証では releases/latest が必須です。期待=$releaseDirectory"
        }
        if (-not [string]::Equals(
            $existingTarget,
            $releaseDirectory,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw (
                "Published 検証は releases/latest を変更しません。" +
                "期待=$releaseDirectory, 実際=$existingTarget"
            )
        }
    }

    $numericVersion = $Version.Substring(1)
    $expectedFileVersion = "$numericVersion.0"
    $distributionDirectory = Assert-DirectChildPath `
        -Path (Join-Path $releaseDirectory 'GeotagPhoto') `
        -Parent $releaseDirectory `
        -ExpectedName 'GeotagPhoto'
    $exePath = Join-Path $distributionDirectory 'GeotagPhoto.exe'
    $zipPath = Assert-DirectChildPath `
        -Path (Join-Path $releaseDirectory "GeotagPhoto-$Version-win64.zip") `
        -Parent $releaseDirectory `
        -ExpectedName "GeotagPhoto-$Version-win64.zip"
    $releaseNotesPath = Assert-DirectChildPath `
        -Path (Join-Path $releaseDirectory 'release_notes.md') `
        -Parent $releaseDirectory `
        -ExpectedName 'release_notes.md'

    $requiredFiles = @(
        $exePath,
        (Join-Path $distributionDirectory 'COPYING'),
        (Join-Path $distributionDirectory 'NOTICE.md'),
        (Join-Path $distributionDirectory 'THIRD_PARTY_NOTICES.md'),
        (Join-Path $distributionDirectory 'README.txt'),
        (Join-Path $distributionDirectory 'static\logo\app.ico'),
        (Join-Path $distributionDirectory 'static\logo\app.png'),
        $releaseNotesPath,
        $zipPath
    )
    foreach ($requiredFile in $requiredFiles) {
        Assert-RequiredFile -Path $requiredFile -AllowedRoot $releaseDirectory
    }
    Assert-RequiredDirectory `
        -Path (Join-Path $distributionDirectory 'third_party_licenses') `
        -AllowedRoot $distributionDirectory

    $distributionManifest = Get-DistributionManifest $distributionDirectory
    $exe = Get-Item -LiteralPath $exePath -Force -ErrorAction Stop
    $actualFileVersion = $exe.VersionInfo.FileVersion
    $actualProductVersion = $exe.VersionInfo.ProductVersion
    if ($actualFileVersion -ne $expectedFileVersion) {
        throw "EXE の FileVersion が一致しません。期待=$expectedFileVersion, 実際=$actualFileVersion"
    }
    if ($actualProductVersion -ne $expectedFileVersion) {
        throw "EXE の ProductVersion が一致しません。期待=$expectedFileVersion, 実際=$actualProductVersion"
    }
    $exeHash = [string]$distributionManifest['GeotagPhoto.exe'].Sha256

    $readmeMarkers = @(
        "GeotagPhoto $Version（最新）",
        "releases/tag/$Version",
        "GeotagPhoto-$Version-win64.zip"
    )
    Assert-TextContains (Join-Path $distributionDirectory 'README.txt') $readmeMarkers
    $specPath = Assert-DirectChildPath `
        -Path (Join-Path $repoRoot 'SPEC.md') `
        -Parent $repoRoot `
        -ExpectedName 'SPEC.md'
    Assert-TextContains $specPath @(
        "現在の最新仕様 [$Version]",
        "### $Version [$Version]"
    )
    Assert-ReleaseNotesHeading `
        -Path $releaseNotesPath `
        -Version $Version
    Assert-ZipPackage `
        -ZipPath $zipPath `
        -ExpectedManifest $distributionManifest
    $zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash

    if ($Phase -eq 'Published') {
        $readmePath = Assert-DirectChildPath `
            -Path (Join-Path $repoRoot 'README.md') `
            -Parent $repoRoot `
            -ExpectedName 'README.md'
        Assert-TextContains $readmePath $readmeMarkers

        $gitPath = Resolve-ApplicationExecutable -Name 'git.exe'

        $trackedSpec = @(Invoke-GitCommand -GitPath $gitPath -RepositoryRoot $repoRoot `
            -Arguments @('ls-files', '--error-unmatch', '--', 'SPEC.md'))
        if ($trackedSpec.Count -ne 1 -or -not [string]::Equals(
            ([string]$trackedSpec[0]).Trim(),
            'SPEC.md',
            [System.StringComparison]::Ordinal
        )) {
            throw '公開時は SPEC.md が Git の追跡対象である必要があります。'
        }

        $tagRef = "refs/tags/$Version"
        $mainRef = 'refs/heads/main'
        $tagCommit = Get-SingleGitLine -GitPath $gitPath -RepositoryRoot $repoRoot `
            -Arguments @('rev-parse', '--verify', "${tagRef}^{commit}")
        $mainCommit = Get-SingleGitLine -GitPath $gitPath -RepositoryRoot $repoRoot `
            -Arguments @('rev-parse', '--verify', "${mainRef}^{commit}")
        if (-not [string]::Equals($mainCommit, $tagCommit, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "ローカル main と公開タグが一致しません。main=$mainCommit, tag=$tagCommit"
        }

        $remoteMainEntries = Get-LsRemoteEntries `
            -GitPath $gitPath `
            -RepositoryRoot $repoRoot `
            -RequestedRefs @($mainRef)
        if ($remoteMainEntries.Count -ne 1 -or -not $remoteMainEntries.ContainsKey($mainRef)) {
            throw "origin に完全名 $mainRef が1件だけ存在することを確認できません。"
        }

        $peeledTagRef = "${tagRef}^{}"
        $remoteTagEntries = Get-LsRemoteEntries `
            -GitPath $gitPath `
            -RepositoryRoot $repoRoot `
            -RequestedRefs @($tagRef, $peeledTagRef)
        if (-not $remoteTagEntries.ContainsKey($tagRef)) {
            throw "origin に完全名 $tagRef がありません。"
        }
        if ($remoteTagEntries.ContainsKey($peeledTagRef)) {
            $remoteTagCommit = $remoteTagEntries[$peeledTagRef]
        }
        else {
            $remoteTagCommit = $remoteTagEntries[$tagRef]
        }
        $remoteMainCommit = $remoteMainEntries[$mainRef]
        if (-not [string]::Equals(
            $remoteMainCommit,
            $tagCommit,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "origin/main と公開タグが一致しません。origin/main=$remoteMainCommit, tag=$tagCommit"
        }
        if (-not [string]::Equals(
            $remoteTagCommit,
            $tagCommit,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "origin の公開タグとローカルタグが一致しません。origin/tag=$remoteTagCommit, local/tag=$tagCommit"
        }

        Assert-TaggedFileMatchesWorkingTree `
            -GitPath $gitPath `
            -RepositoryRoot $repoRoot `
            -TagRef $tagRef `
            -RepositoryPath 'README.md'
        Assert-TaggedFileMatchesWorkingTree `
            -GitPath $gitPath `
            -RepositoryRoot $repoRoot `
            -TagRef $tagRef `
            -RepositoryPath 'SPEC.md'

        $ghCandidates = @()
        if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
            $ghCandidates += (Join-Path $env:ProgramFiles 'GitHub CLI\gh.exe')
        }
        $ghPath = Resolve-ApplicationExecutable `
            -Name 'gh.exe' `
            -AdditionalCandidates $ghCandidates
        Assert-GitHubRelease `
            -GhPath $ghPath `
            -Repository $Repository `
            -Version $Version `
            -ZipPath $zipPath `
            -ReleaseNotesPath $releaseNotesPath
    }

    if ($Phase -eq 'Published') {
        $verifiedTarget = Assert-LatestLink `
            -ReleaseRoot $releaseRoot `
            -ExpectedTarget $releaseDirectory `
            -ExpectedExeHash $exeHash
        [PSCustomObject]@{
            Version = $Version
            Phase = $Phase
            Repository = $Repository
            ReleaseDirectory = $releaseDirectory
            ExeVersion = $actualFileVersion
            ExeSHA256 = $exeHash
            ZipSHA256 = $zipHash
            DistributionFileCount = $distributionManifest.Count
            LatestTarget = $verifiedTarget
            LatestUpdated = $false
        }
        return
    }

    if ($null -ne $existingLatest -and [string]::Equals(
        $existingTarget,
        $releaseDirectory,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        $verifiedTarget = Assert-LatestLink `
            -ReleaseRoot $releaseRoot `
            -ExpectedTarget $releaseDirectory `
            -ExpectedExeHash $exeHash
        [PSCustomObject]@{
            Version = $Version
            Phase = $Phase
            ReleaseDirectory = $releaseDirectory
            ExeVersion = $actualFileVersion
            ExeSHA256 = $exeHash
            ZipSHA256 = $zipHash
            DistributionFileCount = $distributionManifest.Count
            LatestTarget = $verifiedTarget
            LatestUpdated = $false
        }
        return
    }

    $transactionId = [System.Guid]::NewGuid().ToString('N')
    $newLinkName = "latest.__new.$transactionId"
    $oldLinkName = "latest.__old.$transactionId"
    $newLinkPath = Assert-DirectChildPath `
        -Path (Join-Path $releaseRoot $newLinkName) `
        -Parent $releaseRoot `
        -ExpectedName $newLinkName
    $oldLinkPath = Assert-DirectChildPath `
        -Path (Join-Path $releaseRoot $oldLinkName) `
        -Parent $releaseRoot `
        -ExpectedName $oldLinkName
    $latestPath = Assert-DirectChildPath `
        -Path (Join-Path $releaseRoot 'latest') `
        -Parent $releaseRoot `
        -ExpectedName 'latest'
    $oldMoved = $false
    $newInstalled = $false

    try {
        # 現行 latest を触る前に新リンクを作り、権限とリンク先を検証する。
        New-Item -ItemType SymbolicLink -Path $newLinkPath -Target $releaseDirectory | Out-Null
        $newLinkItem = Get-Item -LiteralPath $newLinkPath -Force -ErrorAction Stop
        $newTarget = Get-SymbolicLinkTarget $newLinkItem
        if (-not [string]::Equals(
            $newTarget,
            $releaseDirectory,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "候補リンクの検証に失敗しました。期待=$releaseDirectory, 実際=$newTarget"
        }

        if ($null -ne $existingLatest) {
            if ($existingLatest.LinkType -ne 'SymbolicLink') {
                throw "移動対象 latest が SymbolicLink ではありません: $($existingLatest.FullName)"
            }
            [System.IO.Directory]::Move($latestPath, $oldLinkPath)
            $oldMoved = $true
        }
        [System.IO.Directory]::Move($newLinkPath, $latestPath)
        $newInstalled = $true

        $verifiedTarget = Assert-LatestLink `
            -ReleaseRoot $releaseRoot `
            -ExpectedTarget $releaseDirectory `
            -ExpectedExeHash $exeHash

        if ($oldMoved) {
            Remove-ValidatedTemporaryLink `
                -Path $oldLinkPath `
                -ReleaseRoot $releaseRoot `
                -ExpectedName $oldLinkName
            $oldMoved = $false
        }
    }
    catch {
        $originalError = $_
        $rollbackErrors = New-Object System.Collections.Generic.List[string]

        if ($newInstalled) {
            try {
                Remove-ValidatedTemporaryLink `
                    -Path $latestPath `
                    -ReleaseRoot $releaseRoot `
                    -ExpectedName 'latest'
                $newInstalled = $false
            }
            catch {
                $rollbackErrors.Add("新 latest の削除に失敗: $($_.Exception.Message)")
            }
        }

        if ($oldMoved) {
            try {
                if ($null -ne (Get-ExistingItemIncludingLink -Parent $releaseRoot -Name 'latest')) {
                    throw 'latest が既に存在するため旧リンクを復元できません。'
                }
                $null = Assert-DirectChildPath `
                    -Path $oldLinkPath `
                    -Parent $releaseRoot `
                    -ExpectedName $oldLinkName
                [System.IO.Directory]::Move($oldLinkPath, $latestPath)
                $oldMoved = $false
            }
            catch {
                $rollbackErrors.Add("旧 latest の復元に失敗: $($_.Exception.Message)")
            }
        }

        # 候補リンクだけが残っている場合の cleanup も、元エラーと分離せず集約する。
        if (-not $oldMoved -and -not $newInstalled) {
            try {
                $temporaryItem = Get-ExistingItemIncludingLink -Parent $releaseRoot -Name $newLinkName
                if ($null -ne $temporaryItem) {
                    Remove-ValidatedTemporaryLink `
                        -Path $newLinkPath `
                        -ReleaseRoot $releaseRoot `
                        -ExpectedName $newLinkName
                }
            }
            catch {
                $rollbackErrors.Add("候補リンクの cleanup に失敗: $($_.Exception.Message)")
            }
        }

        if ($rollbackErrors.Count -gt 0) {
            throw (
                "latest 更新失敗: $($originalError.Exception.Message)`n" +
                "ロールバックにも失敗しました: $($rollbackErrors -join '; ')`n" +
                "確認対象: $oldLinkPath, $newLinkPath"
            )
        }
        throw "latest 更新失敗（元の状態へ復元済み）: $($originalError.Exception.Message)"
    }

    [PSCustomObject]@{
        Version = $Version
        Phase = $Phase
        ReleaseDirectory = $releaseDirectory
        ExeVersion = $actualFileVersion
        ExeSHA256 = $exeHash
        ZipSHA256 = $zipHash
        DistributionFileCount = $distributionManifest.Count
        LatestTarget = $verifiedTarget
        LatestUpdated = $true
    }
}
finally {
    if ($null -ne $releaseLockStream) {
        $releaseLockStream.Dispose()
    }
    if ($mutexAcquired) {
        $latestMutex.ReleaseMutex()
    }
    $latestMutex.Dispose()
}
