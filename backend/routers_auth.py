"""
Rotas de autenticação (/api/auth/*) e gestão de usuários (/api/users/*).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db import User, GwCredencial, get_db
from auth import (
    hash_password, verify_password, criar_token,
    get_current_user, require_admin,
)

router = APIRouter()


# ─── Schemas ──────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    login: str
    senha: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class UserOut(BaseModel):
    id: int
    login: str
    role: str
    ativo: bool

    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    login: str = Field(..., min_length=3, max_length=64)
    senha: str = Field(..., min_length=4)
    role: str = Field("usuario", pattern="^(admin|usuario)$")


class UserUpdate(BaseModel):
    senha: str | None = Field(None, min_length=4)
    role: str | None = Field(None, pattern="^(admin|usuario)$")
    ativo: bool | None = None


class RegisterRequest(BaseModel):
    login: str = Field(..., min_length=3, max_length=64)
    senha: str = Field(..., min_length=4)


# ─── Auth ─────────────────────────────────────────────────────────────────────
@router.post("/api/auth/login", response_model=LoginResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.login == req.login).first()
    if not user or not verify_password(req.senha, user.senha_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Login ou senha inválidos",
        )
    if not user.ativo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cadastro aguardando aprovação do administrador.",
        )
    token = criar_token(user)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": user.id, "login": user.login, "role": user.role},
    }


@router.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    """Auto-cadastro: cria usuário INATIVO aguardando aprovação do admin."""
    if db.query(User).filter(User.login == req.login).first():
        raise HTTPException(400, detail=f"Login '{req.login}' já está em uso")
    u = User(
        login=req.login,
        senha_hash=hash_password(req.senha),
        role="usuario",
        ativo=False,   # <<< aguardando aprovação
    )
    db.add(u); db.commit()
    return {"ok": True, "mensagem": "Cadastro enviado. Aguarde aprovação do administrador."}


@router.get("/api/auth/me", response_model=UserOut)
def me(current: User = Depends(get_current_user)):
    return current


# ─── GW pessoal (por usuário) ─────────────────────────────────────────────────
class MeuGwResponse(BaseModel):
    usuario: str = ""
    senha: str = ""
    configurado: bool = False


class MeuGwUpdate(BaseModel):
    usuario: str = Field(..., min_length=1)
    senha: str = Field(..., min_length=1)


@router.get("/api/meu-gw", response_model=MeuGwResponse)
def get_meu_gw(current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    gc = db.query(GwCredencial).filter(GwCredencial.user_id == current.id).first()
    if not gc:
        return MeuGwResponse()
    return MeuGwResponse(usuario=gc.usuario, senha=gc.senha, configurado=True)


@router.put("/api/meu-gw", response_model=MeuGwResponse)
def set_meu_gw(req: MeuGwUpdate,
               current: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    gc = db.query(GwCredencial).filter(GwCredencial.user_id == current.id).first()
    if gc:
        gc.usuario = req.usuario
        gc.senha = req.senha
    else:
        gc = GwCredencial(user_id=current.id, usuario=req.usuario, senha=req.senha)
        db.add(gc)
    db.commit(); db.refresh(gc)
    return MeuGwResponse(usuario=gc.usuario, senha=gc.senha, configurado=True)


# ─── Gestão de usuários (só admin) ────────────────────────────────────────────
@router.get("/api/users", response_model=list[UserOut])
def listar_usuarios(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return db.query(User).order_by(User.id).all()


@router.post("/api/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def criar_usuario(
    req: UserCreate,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if db.query(User).filter(User.login == req.login).first():
        raise HTTPException(400, detail=f"Login '{req.login}' já existe")
    u = User(
        login=req.login,
        senha_hash=hash_password(req.senha),
        role=req.role,
        ativo=True,
    )
    db.add(u); db.commit(); db.refresh(u)
    return u


@router.put("/api/users/{user_id}", response_model=UserOut)
def atualizar_usuario(
    user_id: int,
    req: UserUpdate,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(404, detail="Usuário não encontrado")
    if req.senha is not None:
        u.senha_hash = hash_password(req.senha)
    if req.role is not None:
        # Não permite remover o próprio admin
        if u.id == current.id and req.role != "admin":
            raise HTTPException(400, detail="Não pode remover seu próprio papel de admin")
        u.role = req.role
    if req.ativo is not None:
        if u.id == current.id and not req.ativo:
            raise HTTPException(400, detail="Não pode desativar sua própria conta")
        u.ativo = req.ativo
    db.commit(); db.refresh(u)
    return u


@router.delete("/api/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def deletar_usuario(
    user_id: int,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if user_id == current.id:
        raise HTTPException(400, detail="Não pode deletar a própria conta")
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(404, detail="Usuário não encontrado")
    db.delete(u); db.commit()
    return None
