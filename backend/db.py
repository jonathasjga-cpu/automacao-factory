"""
Camada de banco — SQLite local, Postgres em produção.

Variáveis de ambiente suportadas:
  DATABASE_URL   — string de conexão (postgres://... no Railway)
  DATA_DIR       — diretório do SQLite local (default: ~/.automacao_factory)
"""
import os
from pathlib import Path
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime, func
)
from sqlalchemy.orm import sessionmaker, declarative_base, Session

# Diretório local para SQLite
DATA_DIR = Path(os.getenv("DATA_DIR", str(Path.home() / ".automacao_factory")))
DATA_DIR.mkdir(exist_ok=True)

# String de conexão: DATABASE_URL (produção) ou SQLite local
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'app.db'}")

# Railway às vezes fornece 'postgres://' — SQLAlchemy exige 'postgresql://'
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# check_same_thread=False só é necessário em SQLite (FastAPI é multi-thread)
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    login = Column(String(64), unique=True, nullable=False, index=True)
    senha_hash = Column(String(255), nullable=False)
    role = Column(String(16), default="usuario", nullable=False)  # 'admin' | 'usuario'
    ativo = Column(Boolean, default=True, nullable=False)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())


def init_db():
    """Cria tabelas se não existirem e roda o seed do admin."""
    Base.metadata.create_all(bind=engine)
    _seed_admin()


def _seed_admin():
    """Cria usuário admin padrão se não houver nenhum usuário no banco."""
    from auth import hash_password
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            admin = User(
                login="admin",
                senha_hash=hash_password("admin123"),
                role="admin",
                ativo=True,
            )
            db.add(admin)
            db.commit()
            print("[db] Usuário admin criado: login=admin senha=admin123")
    finally:
        db.close()


def get_db() -> Session:
    """Dependency para FastAPI: abre sessão e fecha ao final da request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
