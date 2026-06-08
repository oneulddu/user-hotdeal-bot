"""migrate_article_id_to_ulid

Revision ID: c1a2b3d4e5f6
Revises: d8013109febc
Create Date: 2026-01-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from ulid import ULID


# revision identifiers, used by Alembic.
revision: str = 'c1a2b3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'd8013109febc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: Change articles.id from auto-increment INT to ULID (VARCHAR(26))."""
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect in ('mysql', 'mariadb'):
        # MySQL/MariaDB: 기존 데이터 마이그레이션 필요
        # 1. 새로운 ULID 컬럼 추가
        op.add_column('articles', sa.Column('new_id', sa.String(26), nullable=True))

        # 2. 기존 레코드에 ULID 생성 (created_at 기반으로 시간순 ULID 생성)
        connection = op.get_bind()
        result = connection.execute(sa.text("SELECT id, created_at FROM articles ORDER BY created_at ASC"))
        rows = result.fetchall()

        for row in rows:
            old_id = row[0]
            created_at = row[1]
            # created_at 기반 ULID 생성 (시간 일관성 유지)
            if created_at:
                new_ulid = str(ULID.from_datetime(created_at))
            else:
                new_ulid = str(ULID())
            connection.execute(
                sa.text("UPDATE articles SET new_id = :new_id WHERE id = :old_id"),
                {"new_id": new_ulid, "old_id": old_id}
            )

        # 3. AUTO_INCREMENT 제거 후 PK 삭제 (MySQL/MariaDB는 AUTO_INCREMENT가 있으면 PK 삭제 불가)
        op.execute("ALTER TABLE articles MODIFY id INT NOT NULL")
        op.execute("ALTER TABLE articles DROP PRIMARY KEY")

        # 4. 기존 id 컬럼 삭제
        op.drop_column('articles', 'id')

        # 5. new_id를 id로 이름 변경
        op.alter_column('articles', 'new_id', new_column_name='id', nullable=False)

        # 6. 새로운 PK 추가
        op.execute("ALTER TABLE articles ADD PRIMARY KEY (id)")

    else:
        # SQLite: 테이블 재생성 필요 (SQLite는 컬럼 타입 변경이 제한적)
        # 1. 임시 테이블 생성
        op.create_table(
            'articles_new',
            sa.Column('id', sa.String(26), nullable=False, primary_key=True),
            sa.Column('article_id', sa.Integer(), nullable=False),
            sa.Column('crawler_name', sa.String(100), nullable=False),
            sa.Column('title', sa.String(500), nullable=False),
            sa.Column('category', sa.String(100), nullable=False),
            sa.Column('site_name', sa.String(100), nullable=False),
            sa.Column('board_name', sa.String(100), nullable=False),
            sa.Column('writer_name', sa.String(100), nullable=False),
            sa.Column('url', sa.String(1000), nullable=False),
            sa.Column('is_end', sa.Boolean(), nullable=False),
            sa.Column('extra', sa.JSON(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('deleted_at', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('crawler_name', 'article_id', name='uix_crawler_article_new')
        )

        # 2. 데이터 마이그레이션 (ULID 생성)
        connection = op.get_bind()
        result = connection.execute(sa.text(
            "SELECT id, article_id, crawler_name, title, category, site_name, board_name, "
            "writer_name, url, is_end, extra, created_at, updated_at, deleted_at "
            "FROM articles ORDER BY created_at ASC"
        ))
        rows = result.fetchall()

        for row in rows:
            created_at = row[11]
            if created_at:
                new_ulid = str(ULID.from_datetime(created_at))
            else:
                new_ulid = str(ULID())

            connection.execute(
                sa.text(
                    "INSERT INTO articles_new "
                    "(id, article_id, crawler_name, title, category, site_name, board_name, "
                    "writer_name, url, is_end, extra, created_at, updated_at, deleted_at) "
                    "VALUES (:id, :article_id, :crawler_name, :title, :category, :site_name, "
                    ":board_name, :writer_name, :url, :is_end, :extra, :created_at, :updated_at, :deleted_at)"
                ),
                {
                    "id": new_ulid,
                    "article_id": row[1],
                    "crawler_name": row[2],
                    "title": row[3],
                    "category": row[4],
                    "site_name": row[5],
                    "board_name": row[6],
                    "writer_name": row[7],
                    "url": row[8],
                    "is_end": row[9],
                    "extra": row[10],
                    "created_at": row[11],
                    "updated_at": row[12],
                    "deleted_at": row[13],
                }
            )

        # 3. 기존 테이블 삭제
        op.drop_table('articles')

        # 4. 새 테이블 이름 변경
        op.rename_table('articles_new', 'articles')

        # 5. 인덱스 재생성
        op.create_index(op.f('ix_articles_article_id'), 'articles', ['article_id'], unique=False)
        op.create_index(op.f('ix_articles_crawler_name'), 'articles', ['crawler_name'], unique=False)
        op.create_index('ix_articles_created_at', 'articles', ['created_at'], unique=False)
        op.create_index('ix_articles_deleted_at', 'articles', ['deleted_at'], unique=False)
        op.create_index('ix_articles_is_end', 'articles', ['is_end'], unique=False)


def downgrade() -> None:
    """Downgrade schema: Revert ULID back to auto-increment INT (data loss warning)."""
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect in ('mysql', 'mariadb'):
        # MySQL/MariaDB
        # 1. 새로운 auto-increment 컬럼 추가
        op.add_column('articles', sa.Column('new_id', sa.Integer(), autoincrement=True, nullable=True))

        # 2. 시퀀스 값 할당
        connection = op.get_bind()
        result = connection.execute(sa.text("SELECT id FROM articles ORDER BY id ASC"))
        rows = result.fetchall()

        for idx, row in enumerate(rows, start=1):
            old_id = row[0]
            connection.execute(
                sa.text("UPDATE articles SET new_id = :new_id WHERE id = :old_id"),
                {"new_id": idx, "old_id": old_id}
            )

        # 3. PK 변경
        op.execute("ALTER TABLE articles DROP PRIMARY KEY")
        op.drop_column('articles', 'id')
        op.alter_column('articles', 'new_id', new_column_name='id', nullable=False)
        op.execute("ALTER TABLE articles ADD PRIMARY KEY (id)")
        op.execute("ALTER TABLE articles MODIFY id INT AUTO_INCREMENT")

    else:
        # SQLite
        op.create_table(
            'articles_old',
            sa.Column('id', sa.Integer(), autoincrement=True, primary_key=True),
            sa.Column('article_id', sa.Integer(), nullable=False),
            sa.Column('crawler_name', sa.String(100), nullable=False),
            sa.Column('title', sa.String(500), nullable=False),
            sa.Column('category', sa.String(100), nullable=False),
            sa.Column('site_name', sa.String(100), nullable=False),
            sa.Column('board_name', sa.String(100), nullable=False),
            sa.Column('writer_name', sa.String(100), nullable=False),
            sa.Column('url', sa.String(1000), nullable=False),
            sa.Column('is_end', sa.Boolean(), nullable=False),
            sa.Column('extra', sa.JSON(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('deleted_at', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('crawler_name', 'article_id', name='uix_crawler_article_old')
        )

        # 데이터 복사 (id는 auto-increment로 새로 생성)
        op.execute(
            "INSERT INTO articles_old "
            "(article_id, crawler_name, title, category, site_name, board_name, "
            "writer_name, url, is_end, extra, created_at, updated_at, deleted_at) "
            "SELECT article_id, crawler_name, title, category, site_name, board_name, "
            "writer_name, url, is_end, extra, created_at, updated_at, deleted_at "
            "FROM articles ORDER BY id ASC"
        )

        op.drop_table('articles')
        op.rename_table('articles_old', 'articles')

        # 인덱스 재생성
        op.create_index(op.f('ix_articles_article_id'), 'articles', ['article_id'], unique=False)
        op.create_index(op.f('ix_articles_crawler_name'), 'articles', ['crawler_name'], unique=False)
        op.create_index('ix_articles_created_at', 'articles', ['created_at'], unique=False)
        op.create_index('ix_articles_deleted_at', 'articles', ['deleted_at'], unique=False)
        op.create_index('ix_articles_is_end', 'articles', ['is_end'], unique=False)
