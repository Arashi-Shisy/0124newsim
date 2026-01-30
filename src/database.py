# c:\0124newSIm\database.py
# SQLiteデータベースの定義と操作を行うクラス
import sqlite3
import json
import os
from contextlib import contextmanager
import threading

# database.pyの場所を基準に絶対パスを設定
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "newsim.db")

class Database:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._local = threading.local()

    def get_connection(self):
        # トランザクション内なら既存のコネクションを返す
        if hasattr(self._local, 'connection') and self._local.connection:
            return self._local.connection, False  # (conn, should_close)
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn, True

    def init_db(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        
        conn, should_close = self.get_connection()
        cursor = conn.cursor()

        # ゲーム状態
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS game_state (
            week INTEGER PRIMARY KEY,
            economic_index REAL
        )
        """)

        # 企業
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            type TEXT, -- 'player', 'npc_maker', 'npc_retail', 'system_supplier'
            funds INTEGER,
            brand_power REAL DEFAULT 0,
            industry TEXT DEFAULT 'automotive',
            credit_rating INTEGER DEFAULT 50,
            dev_knowhow REAL DEFAULT 0,
            borrowing_limit INTEGER DEFAULT 0,
            is_active BOOLEAN DEFAULT 1,
            -- Supplier Traits
            trait_material_score REAL DEFAULT 3.0,
            trait_cost_multiplier REAL DEFAULT 1.0,
            part_category TEXT -- 'engine', 'body', etc. for system_supplier
        )
        """)

        # NPC
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS npcs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            age INTEGER,
            gender TEXT,
            company_id INTEGER,
            department TEXT,
            role TEXT,
            salary INTEGER,
            desired_salary INTEGER,
            loyalty REAL,
            is_genius BOOLEAN,
            last_resigned_week INTEGER DEFAULT 0,
            last_company_id INTEGER,
            
            -- 能力値 (真値)
            diligence REAL,
            management REAL,
            adaptability REAL,
            store_ops REAL,
            production REAL,
            development REAL,
            sales REAL,
            hr REAL,
            pr REAL,
            accounting REAL,
            executive REAL,
            industry_aptitude REAL,
            
            FOREIGN KEY(company_id) REFERENCES companies(id)
        )
        """)

        # 商品設計書
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS product_designs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER,
            name TEXT,
            material_score REAL,
            concept_score REAL,
            production_efficiency REAL,
            base_price INTEGER,
            sales_price INTEGER,
            status TEXT, -- 'developing', 'completed', 'obsolete'
            strategy TEXT, -- 開発方針
            developed_week INTEGER,
            parts_config TEXT, -- JSON: {part_key: {supplier_id, score, cost}}
            awareness REAL DEFAULT 0,
            FOREIGN KEY(company_id) REFERENCES companies(id)
        )
        """)

        # 在庫 (メーカー在庫、小売在庫)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER,
            design_id INTEGER,
            quantity INTEGER,
            sales_price INTEGER DEFAULT 0, -- 小売での販売価格 (メーカー在庫の場合はMSRPまたは0)
            FOREIGN KEY(company_id) REFERENCES companies(id),
            FOREIGN KEY(design_id) REFERENCES product_designs(id)
        )
        """)

        # 施設 (オフィス、工場、店舗)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS facilities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER,
            name TEXT,
            type TEXT, -- 'office', 'factory', 'store'
            size INTEGER, -- 収容人数
            rent INTEGER,
            access_score TEXT, -- 店舗用 S-D
            is_owned BOOLEAN DEFAULT 0,
            FOREIGN KEY(company_id) REFERENCES companies(id)
        )
        """)

        # 借入金
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS loans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER,
            amount INTEGER,
            interest_rate REAL, -- 年利 (0.05 = 5%)
            remaining_weeks INTEGER,
            FOREIGN KEY(company_id) REFERENCES companies(id)
        )
        """)

        # 取引履歴 (ログ用)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week INTEGER,
            type TEXT, -- 'b2b', 'b2c'
            buyer_id INTEGER,
            seller_id INTEGER,
            design_id INTEGER,
            quantity INTEGER,
            amount INTEGER
        )
        """)

        # 会計エントリ (PL作成用)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS account_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week INTEGER,
            company_id INTEGER,
            category TEXT, -- 'revenue', 'cogs', 'labor', 'rent', 'ad', 'interest', 'material', 'stock_purchase'
            amount INTEGER
        )
        """)

        # ニュースログ
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS news_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week INTEGER,
            company_id INTEGER,
            message TEXT,
            type TEXT -- 'info', 'warning', 'error', 'market'
        )
        """)

        # 求人オファー
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS job_offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week INTEGER,
            company_id INTEGER,
            npc_id INTEGER,
            offer_salary INTEGER,
            target_dept TEXT,
            FOREIGN KEY(company_id) REFERENCES companies(id),
            FOREIGN KEY(npc_id) REFERENCES npcs(id)
        )
        """)

        # B2B注文
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS b2b_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week INTEGER,
            buyer_id INTEGER,
            seller_id INTEGER,
            design_id INTEGER,
            quantity INTEGER,
            amount INTEGER,
            status TEXT, -- 'pending', 'accepted', 'rejected', 'completed'
            FOREIGN KEY(buyer_id) REFERENCES companies(id),
            FOREIGN KEY(seller_id) REFERENCES companies(id),
            FOREIGN KEY(design_id) REFERENCES product_designs(id)
        )
        """)

        # インデックスの作成 (パフォーマンス向上)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_npcs_company_id ON npcs(company_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_inventory_company_design ON inventory(company_id, design_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_product_designs_company ON product_designs(company_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_facilities_company ON facilities(company_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_week_type ON transactions(week, type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_b2b_orders_status ON b2b_orders(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_offers_week ON job_offers(week)")

        # 週次企業統計 (レポート用)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS weekly_stats (
            week INTEGER,
            company_id INTEGER,
            production_ordered INTEGER DEFAULT 0,
            production_completed INTEGER DEFAULT 0,
            development_ordered INTEGER DEFAULT 0,
            development_completed INTEGER DEFAULT 0,
            inventory_count INTEGER DEFAULT 0,
            b2b_sales INTEGER DEFAULT 0,
            b2c_sales INTEGER DEFAULT 0,
            hired_count INTEGER DEFAULT 0,
            facility_size INTEGER DEFAULT 0,
            total_revenue INTEGER DEFAULT 0,
            total_expenses INTEGER DEFAULT 0,
            labor_costs INTEGER DEFAULT 0,
            facility_costs INTEGER DEFAULT 0,
            loan_balance INTEGER DEFAULT 0,
            funds INTEGER DEFAULT 0,
            phase TEXT,
            PRIMARY KEY (week, company_id)
        )
        """)

        # 市場トレンド (週次)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_trends (
            week INTEGER PRIMARY KEY,
            b2c_demand INTEGER DEFAULT 0
        )
        """)

        conn.commit()
        conn.close()

    def execute_query(self, query, params=()):
        conn, should_close = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            if should_close:
                conn.commit()
            return cursor.lastrowid
        except:
            if should_close:
                conn.rollback()
            raise
        finally:
            if should_close:
                conn.close()

    def fetch_one(self, query, params=()):
        conn, should_close = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            result = cursor.fetchone()
            return result
        finally:
            if should_close:
                conn.close()

    def fetch_all(self, query, params=()):
        conn, should_close = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            result = cursor.fetchall()
            return result
        finally:
            if should_close:
                conn.close()
            
    @contextmanager
    def transaction(self):
        # ネスト対応: 既にトランザクション中なら何もしない（親に任せる）
        is_root = False
        if not (hasattr(self._local, 'connection') and self._local.connection):
            self._local.connection = sqlite3.connect(self.db_path)
            self._local.connection.row_factory = sqlite3.Row
            is_root = True
            
        conn = self._local.connection
        try:
            yield conn
            if is_root:
                conn.commit()
        except Exception:
            if is_root:
                conn.rollback()
            raise
        finally:
            if is_root:
                conn.close()
                self._local.connection = None
            
    def log_file_event(self, week, company_id, event_type, details):
        try:
            res = self.fetch_one("SELECT name FROM companies WHERE id = ?", (company_id,))
            comp_name = res['name'] if res else "Unknown"
        except:
            comp_name = "Unknown"
            
        log_entry = f"Week {week} | {comp_name} (ID: {company_id}) | {event_type} | {details}\n"
        
        with open("simulation_events.log", "a", encoding="utf-8") as f:
            f.write(log_entry)
            
    def increment_weekly_stat(self, week, company_id, column, value):
        query = f"""
            INSERT INTO weekly_stats (week, company_id, {column}) 
            VALUES (?, ?, ?) 
            ON CONFLICT(week, company_id) 
            DO UPDATE SET {column} = {column} + ?
        """
        self.execute_query(query, (week, company_id, value, value))

    def set_weekly_stat(self, week, company_id, column, value):
        query = f"""
            INSERT INTO weekly_stats (week, company_id, {column}) 
            VALUES (?, ?, ?) 
            ON CONFLICT(week, company_id) 
            DO UPDATE SET {column} = ?
        """
        self.execute_query(query, (week, company_id, value, value))

db = Database()
