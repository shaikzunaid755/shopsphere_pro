"""
Creates the MySQL database (if it does not exist) using the values in .env.
run:  python -m scripts.create_database
"""
import pymysql

from app.core.config import settings


def main():
    conn = pymysql.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER,
                           password=settings.DB_PASSWORD)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{settings.DB_NAME}` "
                        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        print(f"Database '{settings.DB_NAME}' is ready")
    finally:
        conn.close()


if __name__ == "__main__":
    main()