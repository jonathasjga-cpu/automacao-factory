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

def salvar_credenciais(sistema: str, usuario: str, senha: str, url: str = None):
    creds = carregar_credenciais()
    entry = {"usuario": usuario, "senha": senha}
    if url is not None:
        entry["url"] = url
    elif "url" in creds.get(sistema, {}):
        entry["url"] = creds[sistema]["url"]   # preserva URL existente
    creds[sistema] = entry
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

def get_credencial(sistema: str, user_id: int | None = None) -> dict:
    """
    Retorna credencial do sistema.
    - Para sistema="gw", se user_id for fornecido, busca credencial pessoal no banco.
      Se o usuário não tem credencial GW, levanta ValueError.
    - Para outros sistemas (factories), usa sempre o arquivo compartilhado.
    """
    if sistema == "gw" and user_id is not None:
        # Import local para evitar ciclo (db -> auth -> config_manager)
        from db import SessionLocal, GwCredencial
        db = SessionLocal()
        try:
            gc = db.query(GwCredencial).filter(GwCredencial.user_id == user_id).first()
            if not gc:
                raise ValueError(
                    "Você ainda não cadastrou seu acesso pessoal do GW. "
                    "Vá em Configurações → Meu acesso GW."
                )
            return {"usuario": gc.usuario, "senha": gc.senha}
        finally:
            db.close()

    creds = carregar_credenciais()
    if sistema not in creds:
        raise ValueError(f"Credenciais para '{sistema}' não configuradas. Configure na tela de Configurações.")
    return creds[sistema]
