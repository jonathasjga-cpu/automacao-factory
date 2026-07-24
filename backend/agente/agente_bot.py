"""AutoFactory - Agente Local (rodar na maquina do usuario).

STDLIB puro: urllib + ssl + json + subprocess. Uma dependencia opcional:
certifi (fallback para CERTIFICATE_VERIFY_FAILED em Windows recem-instalado).

Loop:
1. GET /api/agente/proximo a cada 5s (com Bearer token embutido em config).
2. Se veio ordem, dispara o motor correspondente via subprocess.
3. Enquanto o motor roda, le _progresso.json a cada 1s e POST /progresso.
4. Ao terminar, le _resultado.json e POST /resultado.

Uso: python agente_bot.py
"""
import json
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib import request as urlreq, error as urlerr


RAIZ = Path(__file__).parent
CONFIG_FILE = RAIZ / "agente_config.json"


def _load_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"[FATAL] agente_config.json nao encontrado em {CONFIG_FILE}")
        sys.exit(1)
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def _ssl_context() -> ssl.SSLContext:
    """SSL context tolerante a Windows sem CA store atualizado."""
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


CTX = _ssl_context()


def _http(method: str, url: str, token: str, body: dict | None = None,
          timeout: int = 30, tentativas: int = 3) -> dict:
    """HTTP com retry em timeouts/502/503/504. Rede intermitente do Railway
    (~2-3 timeouts por hora) nao pode virar 'agente offline' cada vez."""
    data = None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    ultimo_erro: dict = {}
    for tent in range(1, tentativas + 1):
        req = urlreq.Request(url, data=data, method=method, headers=headers)
        try:
            with urlreq.urlopen(req, context=CTX, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urlerr.HTTPError as e:
            raw = b""
            try: raw = e.read()
            except Exception: pass
            body_txt = raw.decode("utf-8", "replace")
            ultimo_erro = {"_erro_http": e.code, "_body": body_txt}
            # 5xx merece retry; 4xx nao (token errado, ordem inexistente etc)
            if e.code < 500 or tent == tentativas:
                return ultimo_erro
            time.sleep(2 * tent)
        except Exception as e:
            ultimo_erro = {"_erro": str(e)}
            if tent == tentativas:
                return ultimo_erro
            time.sleep(2 * tent)
    return ultimo_erro


def rodar_motor(ordem: dict, panel_url: str, token: str) -> tuple[dict | None, str | None]:
    """Executa o motor apropriado num subprocess, monitorando progresso."""
    tmpdir = Path(tempfile.mkdtemp(prefix="autof-ordem-"))
    job_file = tmpdir / "job.json"
    prog_file = tmpdir / "_progresso.json"
    res_file = tmpdir / "_resultado.json"

    job_file.write_text(json.dumps(ordem, ensure_ascii=False), encoding="utf-8")

    tipo = ordem.get("tipo", "")
    if tipo == "carregar_faturas":
        motor_script = RAIZ / "motor_excel.py"
    elif tipo == "baixar_documentos":
        motor_script = RAIZ / "motor_documentos.py"
    else:
        return None, f"Tipo de ordem desconhecido: {tipo}"

    if not motor_script.exists():
        return None, f"Motor '{motor_script.name}' nao encontrado no pacote do agente."

    cmd = [
        sys.executable, str(motor_script),
        "--job", str(job_file),
        "--progresso", str(prog_file),
        "--resultado", str(res_file),
    ]
    print(f"[AGENTE] executando: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=str(RAIZ))

    ultimo_prog = ""
    while proc.poll() is None:
        time.sleep(1.0)
        try:
            if prog_file.exists():
                raw = prog_file.read_text(encoding="utf-8").strip()
                if raw:
                    p = json.loads(raw)
                    key = f'{p.get("feito")}/{p.get("total")}: {p.get("desc")}'
                    if key != ultimo_prog:
                        _http("POST", f"{panel_url}/api/agente/progresso/{ordem['id']}",
                              token, body={
                                  "feito": int(p.get("feito", 0)),
                                  "total": int(p.get("total", 0)),
                                  "desc": str(p.get("desc", "")),
                              })
                        ultimo_prog = key
        except Exception as e:
            print(f"[AGENTE] leitura progresso falhou: {e}")

    resultado = None
    erro = None
    if res_file.exists():
        try:
            resultado = json.loads(res_file.read_text(encoding="utf-8"))
            if isinstance(resultado, dict) and resultado.get("erro"):
                erro = resultado.get("erro")
                # Mantem resultado inteiro (com traceback) pro painel exibir
        except Exception as e:
            erro = f"Falha ao ler _resultado.json: {e}"
    else:
        erro = f"Motor terminou sem gerar _resultado.json (exitcode={proc.returncode})"
    return resultado, erro


def main():
    cfg = _load_config()
    panel = cfg["panel_url"].rstrip("/")
    token = cfg["token"]
    intervalo = int(cfg.get("intervalo_poll_seg", 5))

    print("=" * 60)
    print(f" AutoFactory - Agente Local")
    print(f" Painel: {panel}")
    print(f" Poll:   {intervalo}s")
    print("=" * 60)

    ocioso_desde = time.time()
    while True:
        try:
            r = _http("GET", f"{panel}/api/agente/proximo", token, timeout=15)
            if "_erro" in r:
                print(f"[AGENTE] erro conectando painel: {r['_erro']}")
                time.sleep(intervalo * 2)
                continue
            if "_erro_http" in r:
                print(f"[AGENTE] painel HTTP {r['_erro_http']}: {r.get('_body', '')[:200]}")
                time.sleep(intervalo * 2)
                continue

            if not r.get("tem_ordem"):
                # Feedback ocioso a cada minuto
                if time.time() - ocioso_desde > 60:
                    print(f"[AGENTE] ocioso (painel ok, sem ordens)")
                    ocioso_desde = time.time()
                time.sleep(intervalo)
                continue

            ocioso_desde = time.time()
            ordem = r["ordem"]
            print(f"[AGENTE] ordem {ordem['id']} tipo={ordem.get('tipo')}")
            resultado, erro = rodar_motor(ordem, panel, token)
            envio = _http("POST", f"{panel}/api/agente/resultado/{ordem['id']}", token,
                          body={"resultado": resultado, "erro": erro})
            print(f"[AGENTE] ordem {ordem['id']} devolvida. erro={erro} envio={envio}")

        except KeyboardInterrupt:
            print("[AGENTE] encerrado pelo usuario")
            break
        except Exception as e:
            print(f"[AGENTE] loop erro: {e}")
            time.sleep(intervalo * 2)


if __name__ == "__main__":
    main()
