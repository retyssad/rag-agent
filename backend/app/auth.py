import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, delete

from app.db import Database, User, AuthSession, uid, now
from app.observability import get_logger
from app.repository import Conflict

logger = get_logger('app.auth')

TOKEN_TTL_DAYS = 30
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
COOKIE_NAME = 'coach_token'


class Unauthorized(Exception):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt,
                            n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f'scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}'


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split('$')
        if scheme != 'scrypt':
            return False
        digest = hashlib.scrypt(password.encode('utf-8'), salt=bytes.fromhex(salt_hex),
                                n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


class AuthService:
    def __init__(self, db: Database):
        self.db = db

    def register(self, username: str, password: str) -> tuple[dict, str]:
        with self.db.transaction() as s:
            exists = s.scalars(select(User).where(User.username == username)).first()
            if exists:
                raise Conflict('用户名已被使用，请换一个')
            user = User(username=username, password_hash=hash_password(password))
            s.add(user)
            s.flush()
            view = {'id': user.id, 'username': user.username,
                    'created_at': user.created_at.isoformat() + 'Z'}
            token = self._issue(s, user.id)
            logger.info('注册新账号', extra={'event': 'auth.register', 'user_id': user.id})
            return view, token

    def login(self, username: str, password: str) -> tuple[dict, str]:
        with self.db.transaction() as s:
            user = s.scalars(select(User).where(User.username == username)).first()
            if not user or not verify_password(password, user.password_hash):
                # 不记录用户名与密码，只记录失败次数信号。
                logger.warning('登录失败', extra={'event': 'auth.login_failed'})
                raise Unauthorized('用户名或密码不正确')
            s.execute(delete(AuthSession).where(AuthSession.expires_at < now()))
            view = {'id': user.id, 'username': user.username,
                    'created_at': user.created_at.isoformat() + 'Z'}
            token = self._issue(s, user.id)
            logger.info('登录成功', extra={'event': 'auth.login', 'user_id': user.id})
            return view, token

    def resolve(self, token: str | None) -> dict:
        if not token:
            raise Unauthorized('请先登录')
        with self.db.transaction() as s:
            row = s.scalars(select(AuthSession).where(
                AuthSession.token_hash == token_digest(token))).first()
            if not row or row.expires_at < now():
                raise Unauthorized('登录已过期，请重新登录')
            user = s.get(User, row.user_id)
            if not user:
                raise Unauthorized('登录已过期，请重新登录')
            return {'id': user.id, 'username': user.username,
                    'created_at': user.created_at.isoformat() + 'Z'}

    def logout(self, token: str | None):
        if not token:
            return
        with self.db.transaction() as s:
            s.execute(delete(AuthSession).where(AuthSession.token_hash == token_digest(token)))
        logger.info('已登出', extra={'event': 'auth.logout'})

    def _issue(self, s, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=TOKEN_TTL_DAYS)
        s.add(AuthSession(user_id=user_id, token_hash=token_digest(token), expires_at=expires))
        return token
