import json
import os
from pathlib import Path
from cryptography.fernet import Fernet

# Em produção (Railway) usa /data (volume persistente); localmente usa ~/.automacao_factory
CONFIG_DIR = Path(os.getenv("DATA_DIR", str(Path.home() / ".automacao_factory")))
CONFIG_FILE = CONFIG_DIR / "credenciais.enc"
KEY_FILE = CONFIG_DIR / "chave.key"

def _get_or_create_key():
    CONFIG_DIR.mkdir(exist_ok=True)
    if not KEY_FILE.exists():
        key = Fernet.generate_key()
        KEY_FILE.write_bytes(key)
        KEY_FILE.chmod(0o600)
    return KEY_FILE.read_bytes()

def _cipher():
    return Fernet(_get_or_create_key())

def salvar_credenciais(sistema: str, usuario: str, senha: str):
    creds = carregar_credenciais()
    creds[sistema] = {"usuario": usuario, "senha": senha}
    dados = json.dumps(creds).encode()
    encriptado = _cipher().encrypt(dados)
    CONFIG_FILE.write_bytes(encriptado)

def carregar_credenciais() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        encriptado = CONFIG_FILE.read_bytes()
        dados = _cipher().decrypt(encriptado)
        return json.loads(dados)
    except Exception:
        return {}

def get_credencial(sistema: str) -> dict:
    creds = carregar_credenciais()
    if sistema not in creds:
        raise ValueError(f"Credenciais para '{sistema}' não configuradas. Configure na tela de Configurações.")
    return creds[sistema]
