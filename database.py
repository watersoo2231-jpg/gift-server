# -*- coding: utf-8 -*-
import sqlite3
import logging

DATABASE = 'gifts.db'
logger = logging.getLogger(__name__)

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    try:
        conn = get_db()
        cursor = conn.cursor()
        # Check if table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='gifts'")
        if cursor.fetchone() is None:
            logger.info("Creating gifts table...")
            conn.execute('''
                CREATE TABLE gifts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    gift_id TEXT UNIQUE NOT NULL,
                    order_id TEXT,
                    recipient_email TEXT NOT NULL,
                    recipient_phone TEXT,
                    product_id TEXT NOT NULL,
                    product_name TEXT,
                    amount INTEGER NOT NULL,
                    sender_name TEXT,
                    gift_message TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    address_token TEXT,
                    expire_at TIMESTAMP,
                    recv_name TEXT,
                    recv_phone TEXT,
                    recv_zip TEXT,
                    recv_addr1 TEXT,
                    recv_addr2 TEXT,
                    recv_memo TEXT
                )
            ''')
            conn.commit()
            logger.info("Gifts table created.")
        else:
            logger.info("Gifts table already exists.")
        conn.close()
    except Exception as e:
        logger.error(f"DB 초기화 실패: {e}")

if __name__ == '__main__':
    init_db()
