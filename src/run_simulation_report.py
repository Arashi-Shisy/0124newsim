# c:\0124newSIm\src\run_simulation_report.py
import csv
import os
from database import db
from simulation import Simulation
from seed import run_seed

# シミュレーション実行週数
SIMULATION_WEEKS = 13
# 出力ファイル名
OUTPUT_FILE = "simulation_report.csv"

def run_report():
    print("=== NewSim Balance Check Report Generator ===")
    
    # 1. データベースの初期化とシードデータの投入
    print("Initializing database and seed data...")
    run_seed()
    
    sim = Simulation()
    stats = []
    
    print(f"Starting simulation for {SIMULATION_WEEKS} weeks...")
    
    # シミュレーションループ
    for _ in range(SIMULATION_WEEKS):
        # 現在の週を取得（処理開始前の週）
        current_week = sim.get_current_week()
        
        # 1週間進める
        sim.proceed_week()
        
        # --- データ集計 (処理が行われた週 = current_week に関するデータを集計) ---
        
        # 1. 失業率
        # 全NPC数
        total_npcs = db.fetch_one("SELECT COUNT(*) as cnt FROM npcs")['cnt']
        # 無職のNPC数 (company_id が NULL)
        unemployed = db.fetch_one("SELECT COUNT(*) as cnt FROM npcs WHERE company_id IS NULL")['cnt']
        
        unemployment_rate = 0.0
        if total_npcs > 0:
            unemployment_rate = (unemployed / total_npcs) * 100
            
        # 2. B2C売上 (transactionsテーブル type='b2c')
        b2c_res = db.fetch_one("SELECT SUM(amount) as total FROM transactions WHERE week = ? AND type = 'b2c'", (current_week,))
        b2c_sales = b2c_res['total'] if b2c_res and b2c_res['total'] else 0
        
        # 2.5 B2C総需要
        demand_res = db.fetch_one("SELECT b2c_demand FROM market_trends WHERE week = ?", (current_week,))
        b2c_demand = demand_res['b2c_demand'] if demand_res else 0
        
        # 3. B2B取引金額 (transactionsテーブル type='b2b')
        b2b_res = db.fetch_one("SELECT SUM(amount) as total FROM transactions WHERE week = ? AND type = 'b2b'", (current_week,))
        b2b_sales = b2b_res['total'] if b2b_res and b2b_res['total'] else 0
        
        # 4. 倒産情報 (news_logsテーブルから倒産を含むメッセージを抽出)
        # simulation.py で 'error' または 'market' タイプで倒産ログが出力される
        bankruptcy_logs = db.fetch_all("""
            SELECT message FROM news_logs 
            WHERE week = ? AND message LIKE '%倒産%'
        """, (current_week,))
        
        bankruptcy_info = ""
        if bankruptcy_logs:
            bankruptcy_info = "; ".join([log['message'] for log in bankruptcy_logs])
            
        # 5. 平均資金 (メーカー vs 小売)
        avg_funds_maker = db.fetch_one("SELECT AVG(funds) as val FROM companies WHERE type IN ('player', 'npc_maker') AND is_active = 1")['val'] or 0
        avg_funds_retail = db.fetch_one("SELECT AVG(funds) as val FROM companies WHERE type = 'npc_retail' AND is_active = 1")['val'] or 0

        # 6. 総在庫数 (メーカー vs 小売)
        # メーカー在庫
        maker_inv = db.fetch_one("""
            SELECT SUM(i.quantity) as val 
            FROM inventory i JOIN companies c ON i.company_id = c.id 
            WHERE c.type IN ('player', 'npc_maker') AND c.is_active = 1
        """)['val'] or 0
        
        # 小売在庫
        retail_inv = db.fetch_one("""
            SELECT SUM(i.quantity) as val 
            FROM inventory i JOIN companies c ON i.company_id = c.id 
            WHERE c.type = 'npc_retail' AND c.is_active = 1
        """)['val'] or 0

        # 7. 平均給与 & 平均忠誠度
        npc_stats = db.fetch_one("SELECT AVG(salary) as avg_sal, AVG(loyalty) as avg_loy FROM npcs WHERE company_id IS NOT NULL")
        avg_salary = npc_stats['avg_sal'] or 0
        avg_loyalty = npc_stats['avg_loy'] or 0

        # 8. 稼働企業数
        active_companies = db.fetch_one("SELECT COUNT(*) as cnt FROM companies WHERE is_active = 1 AND type != 'system_supplier'")['cnt']

        # 統計データをリストに追加
        stats.append({
            "week": current_week,
            "unemployment_rate": f"{unemployment_rate:.2f}%",
            "b2c_demand": b2c_demand,
            "b2c_sales": b2c_sales,
            "b2b_sales": b2b_sales,
            "avg_funds_maker": int(avg_funds_maker),
            "avg_funds_retail": int(avg_funds_retail),
            "maker_inventory": maker_inv,
            "retail_inventory": retail_inv,
            "avg_salary": int(avg_salary),
            "avg_loyalty": f"{avg_loyalty:.1f}",
            "active_companies": active_companies,
            "bankruptcies": bankruptcy_info
        })

    # レポート出力
    # srcディレクトリの親（プロジェクトルート）に出力
    output_path = os.path.join(os.path.dirname(__file__), "..", OUTPUT_FILE)
    print(f"Exporting report to {output_path}...")
    
    try:
        with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = [
                "week", "unemployment_rate", "b2c_demand", "b2c_sales", "b2b_sales", 
                "avg_funds_maker", "avg_funds_retail", 
                "maker_inventory", "retail_inventory", 
                "avg_salary", "avg_loyalty", "active_companies",
                "bankruptcies"
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            
            writer.writeheader()
            writer.writerows(stats)
        print("Report generation completed successfully.")
        
    except Exception as e:
        print(f"Error exporting report: {e}")
        
    # 詳細レポート出力 (Company Weekly Stats)
    detail_output_path = os.path.join(os.path.dirname(__file__), "..", "company_details.csv")
    print(f"Exporting detailed report to {detail_output_path}...")
    
    try:
        # 企業名も含めて取得
        details = db.fetch_all("""
            SELECT w.*, c.name as company_name 
            FROM weekly_stats w 
            JOIN companies c ON w.company_id = c.id 
            WHERE c.type != 'system_supplier'
            ORDER BY w.week, w.company_id
        """)
        
        if details:
            with open(detail_output_path, 'w', newline='', encoding='utf-8-sig') as f:
                # 日本語カラム名へのマッピングと順序定義
                column_map = {
                    "week": "週",
                    "company_id": "企業ID",
                    "company_name": "企業名",
                    "total_revenue": "売上高",
                    "total_expenses": "費用計",
                    "profit": "収支", # 計算項目
                    "funds": "現金残高",
                    "loan_balance": "借入残高",
                    "inventory_count": "在庫数",
                    "b2b_sales": "B2B販売数",
                    "b2c_sales": "B2C販売数",
                    "production_ordered": "生産指示数",
                    "production_completed": "生産完了数",
                    "development_ordered": "開発指示数",
                    "development_completed": "開発完了数",
                    "hired_count": "採用数",
                    "facility_size": "施設規模",
                    "labor_costs": "人件費",
                    "facility_costs": "施設費"
                }
                
                fieldnames = list(column_map.values())
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                
                writer.writeheader()
                for row in details:
                    data = dict(row)
                    # 収支の計算
                    data['profit'] = data['total_revenue'] - data['total_expenses']
                    
                    # 日本語キーに変換して書き込み
                    jp_row = {column_map.get(k, k): v for k, v in data.items() if k in column_map or k == 'profit'}
                    
                    # column_mapの順序通りに並べるための処理
                    ordered_row = {v: jp_row.get(v, 0) for v in fieldnames}
                    writer.writerow(ordered_row)
            print("Detailed report generation completed successfully.")
        else:
            print("No detailed stats available.")
            
    except Exception as e:
        print(f"Error exporting detailed report: {e}")

if __name__ == "__main__":
    run_report()
