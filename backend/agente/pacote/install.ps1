# AutoFactory Agente - Instalador PowerShell
# Executa em ExecutionPolicy Bypass (via wrapper .bat)
# Faz absolutamente TUDO com log detalhado e fallbacks.

$ErrorActionPreference = "Continue"
$LOG_FILE = Join-Path $env:USERPROFILE "autofactory_install.log"

function Log($msg, $color = "White") {
    $ts = Get-Date -Format "HH:mm:ss"
    $line = "[$ts] $msg"
    Write-Host $line -ForegroundColor $color
    Add-Content -Path $LOG_FILE -Value $line -Encoding UTF8
}

# Reset log
"" | Out-File -FilePath $LOG_FILE -Encoding UTF8

Log "=========================================="
Log " AutoFactory Agente - Instalacao"
Log "=========================================="
Log "Data: $(Get-Date)"
Log "PC:   $env:COMPUTERNAME"
Log "User: $env:USERNAME"
Log ""

# ========================================================================
# PASSO 0: Desativar stub Python da Microsoft Store
# ========================================================================
Log "PASSO 0/5: Desativando stub Python da Microsoft Store (se existir)..."
foreach ($stub in @("python.exe", "python3.exe")) {
    $p = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\$stub"
    if (Test-Path $p) {
        try {
            Remove-Item $p -Force -ErrorAction Stop
            Log "  removido: $p" "Green"
        } catch {
            Log "  aviso: nao consegui remover $p ($_)" "Yellow"
        }
    }
}

# ========================================================================
# PASSO 1: Procurar Python real
# ========================================================================
function Find-Python {
    $candidatos = @()
    $pf86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    # Instalacoes de usuario (winget/python.org "just me")
    $candidatos += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*" -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { Join-Path $_.FullName "python.exe" }
    # Instalacao no C:\
    $candidatos += Get-ChildItem "C:\Python3*" -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { Join-Path $_.FullName "python.exe" }
    # All users em Program Files
    $candidatos += Get-ChildItem "$env:ProgramFiles\Python3*" -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { Join-Path $_.FullName "python.exe" }
    if ($pf86) {
        $candidatos += Get-ChildItem "$pf86\Python3*" -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName "python.exe" }
    }
    # Testa cada um: precisa executar `--version` e imprimir "Python 3.X.Y"
    foreach ($c in $candidatos) {
        if (Test-Path $c) {
            try {
                $ver = & $c --version 2>&1
                if ($ver -match '^Python 3\.\d+\.\d+') {
                    return $c
                }
            } catch { }
        }
    }
    # Ultimo recurso: python do PATH - mas so se responder version valida
    try {
        $ver = & python --version 2>&1
        if ($ver -match '^Python 3\.\d+\.\d+') {
            $exe = (Get-Command python -ErrorAction Stop).Source
            if ($exe -and ($exe -notlike "*WindowsApps*")) {
                return $exe
            }
        }
    } catch { }
    return $null
}

Log ""
Log "PASSO 1/5: Procurando Python 3 instalado..."
$py = Find-Python
if ($py) {
    Log "  Python encontrado: $py" "Green"
    try {
        $ver = & $py --version 2>&1
        Log "  Versao: $ver" "Green"
    } catch { }
} else {
    Log "  Python nao encontrado. Instalando..." "Yellow"

    # ========================================================================
    # PASSO 1a: Instalar Python via winget OU download direto
    # ========================================================================
    $wingetOK = $false
    try {
        $null = Get-Command winget -ErrorAction Stop
        $wingetOK = $true
    } catch { }

    if ($wingetOK) {
        Log "  Instalando via winget (Python 3.12)..." "Yellow"
        try {
            $out = & winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements 2>&1
            Log ($out | Out-String)
        } catch {
            Log "  winget deu erro: $_" "Yellow"
        }
    } else {
        Log "  winget indisponivel. Baixando instalador do python.org..." "Yellow"
        $inst = Join-Path $env:TEMP "python-3.12.7-amd64.exe"
        $url = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
        try {
            # Usa BitsTransfer se disponivel, senao Invoke-WebRequest
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $url -OutFile $inst -UseBasicParsing
            Log "  download OK ($((Get-Item $inst).Length) bytes). Instalando..." "Yellow"
            $proc = Start-Process -FilePath $inst -ArgumentList "/quiet","InstallAllUsers=0","PrependPath=1","Include_pip=1","Include_launcher=1" -Wait -PassThru
            Log "  instalador saiu com codigo $($proc.ExitCode)"
        } catch {
            Log "  ERRO ao baixar/instalar Python: $_" "Red"
        }
    }

    # Re-detecta
    $py = Find-Python
    if (-not $py) {
        Log ""
        Log "ERRO: Nao consegui instalar Python automaticamente." "Red"
        Log "    Instale manualmente: https://www.python.org/downloads/" "Red"
        Log "    Marque 'Add python.exe to PATH' no instalador!" "Red"
        exit 1
    }
    Log "  Python instalado: $py" "Green"
}

# ========================================================================
# PASSO 2: Verificar pip
# ========================================================================
Log ""
Log "PASSO 2/5: Verificando pip..."
try {
    $pipVer = & $py -m pip --version 2>&1
    Log "  $pipVer" "Green"
} catch {
    Log "  pip nao funciona. Tentando ensurepip..." "Yellow"
    & $py -m ensurepip --upgrade
}

# ========================================================================
# PASSO 3: Instalar dependencias
# ========================================================================
Log ""
Log "PASSO 3/5: Instalando dependencias (playwright, pandas, openpyxl, httpx, certifi, pypdf)..."
Log "  NAO CLIQUE dentro da janela - Windows pausa o processo se voce clicar."
# pypdf e' obrigatorio: sem ele a separacao do PDF agrupado em faturas
# individuais falha silenciosamente e o ZIP 'Faturas separadas' nao e' gerado.
$deps = @("playwright", "pandas", "openpyxl", "httpx", "certifi", "pypdf")

# Upgrade pip primeiro (silencioso)
& $py -m pip install --upgrade pip 2>&1 | Out-Null

# Instala as deps
$pipOut = & $py -m pip install --upgrade $deps 2>&1
Log ($pipOut | Out-String)
if ($LASTEXITCODE -ne 0) {
    Log "  Primeira tentativa falhou. Tentando com --user..." "Yellow"
    $pipOut = & $py -m pip install --upgrade --user $deps 2>&1
    Log ($pipOut | Out-String)
    if ($LASTEXITCODE -ne 0) {
        Log ""
        Log "ERRO: Falha ao instalar dependencias. Verifique internet / antivirus." "Red"
        Log "    Erro pip acima." "Red"
        exit 2
    }
}

# ========================================================================
# PASSO 4: Verificar imports
# ========================================================================
Log ""
Log "PASSO 4/5: Verificando imports..."
$tmpPy = Join-Path $env:TEMP "autof_verifica.py"
$sb = New-Object System.Text.StringBuilder
[void]$sb.AppendLine('import pandas, openpyxl, playwright, httpx, certifi, pypdf')
[void]$sb.AppendLine('print("OK")')
[IO.File]::WriteAllText($tmpPy, $sb.ToString(), [Text.UTF8Encoding]::new($false))
$saida = & $py $tmpPy 2>&1
foreach ($linha in $saida) { Log ("  " + $linha) }
Remove-Item $tmpPy -Force -ErrorAction SilentlyContinue
if ($LASTEXITCODE -ne 0) {
    Log "ERRO: Deps instaladas mas import falhou." "Red"
    exit 3
}

# ========================================================================
# PASSO 5: Verificar Chrome
# ========================================================================
Log ""
Log "PASSO 5/5: Verificando Google Chrome..."
$pf86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
$chromeCandidatos = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
if ($pf86) { $chromeCandidatos += "$pf86\Google\Chrome\Application\chrome.exe" }
$chrome = $null
foreach ($c in $chromeCandidatos) {
    if (Test-Path $c) { $chrome = $c; break }
}
if ($chrome) {
    Log "  Chrome encontrado: $chrome" "Green"
} else {
    Log "  Chrome NAO encontrado - necessario pro agente funcionar." "Yellow"
    Log "  Tentando instalar via winget..." "Yellow"
    if ($wingetOK -or (Get-Command winget -ErrorAction SilentlyContinue)) {
        try {
            $out = & winget install -e --id Google.Chrome --scope user --silent --accept-package-agreements --accept-source-agreements 2>&1
            Log ($out | Out-String)
            foreach ($c in $chromeCandidatos) {
                if (Test-Path $c) { $chrome = $c; break }
            }
        } catch {
            Log "  winget Chrome falhou: $_" "Yellow"
        }
    }
    if (-not $chrome) {
        Log ""
        Log "  AVISO: Chrome nao foi instalado automaticamente." "Yellow"
        Log "  Baixe manualmente: https://www.google.com/chrome/" "Yellow"
        Log "  Sem Chrome o agente NAO funciona." "Yellow"
        # Nao falha o instalador - user pode instalar Chrome depois
    } else {
        Log "  Chrome instalado: $chrome" "Green"
    }
}

Log ""
Log "=========================================="
Log " Instalacao concluida com sucesso"
Log "=========================================="
exit 0
