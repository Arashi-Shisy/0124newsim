# c:\0124newSIm\src\seed.py
# 初期データを生成・投入するスクリプト

import json
import random
import math
from database import db
import gamebalance as gb
import name_generator

# NPCテーブルのカラム順序を固定定義
NPC_COLUMNS = [
    "name", "age", "gender", "company_id", "division_id", "department", "role", 
    "salary", "desired_salary", "loyalty", "is_genius", 
    "last_resigned_week", "last_company_id", 
    "diligence", "management", "adaptability", "store_ops", 
    "production", "development", "sales", "hr", "pr", "accounting", 
    "executive", "aptitudes"
]

def create_npc_tuple(name, age, gender, company_id, division_id, department, role, salary, stats, aptitudes):
    return (
        name, age, gender, company_id, division_id, department, role,
        salary, salary, 50, 0, # desired_salary=salary, loyalty=50, is_genius=0
        0, None, # last_resigned, last_company
        stats['diligence'], stats['management'], stats['adaptability'], stats['store_ops'],
        stats['production'], stats['development'], stats['sales'], stats['hr'], stats['pr'], stats['accounting'],
        stats['executive'], json.dumps(aptitudes)
    )

def generate_random_npc(age=None):
    if age is None:
        age = random.randint(gb.START_AGE, 60)
    gender = random.choice(["M", "F"])
    name = name_generator.generate_person_name(gender)
    
    # 年齢と能力の比例 (22歳=20, 60歳=60 程度 + ランダム)
    base_stat = 20 + (age - 22) + random.randint(-10, 10)
    base_stat = max(10, min(90, base_stat))
    
    stats = {k: base_stat + random.randint(-5, 5) for k in ["diligence", "management", "adaptability", "store_ops", "production", "development", "sales", "hr", "pr", "accounting", "executive"]}
    # Clamp
    for k in stats: stats[k] = max(0, min(100, stats[k]))
    
    aptitudes = {ind: 0.1 for ind in gb.INDUSTRIES.keys()}
    
    # 給与は能力依存だが無職なので0 (採用時に決定されるが、データ上は0でよい)
    # ただし desired_salary は設定しておくとよい
    max_stat = max(stats.values())
    desired_salary = int(gb.BASE_SALARY_YEARLY * (max_stat / 50.0))
    
    return {
        "name": name, "age": age, "gender": gender, "company_id": None, "division_id": None, "department": None, "role": None,
        "salary": 0, "desired_salary": desired_salary, "loyalty": 50, "is_genius": 0,
        "last_resigned_week": 0, "last_company_id": None,
        "diligence": stats['diligence'], "management": stats['management'], "adaptability": stats['adaptability'], "store_ops": stats['store_ops'],
        "production": stats['production'], "development": stats['development'], "sales": stats['sales'], "hr": stats['hr'], "pr": stats['pr'], "accounting": stats['accounting'],
        "executive": stats['executive'], "aptitudes": json.dumps(aptitudes)
    }

def generate_unemployed_npc():
    npc = generate_random_npc()
    return tuple(npc[col] for col in NPC_COLUMNS)

def run_seed():
    db.init_db()
    
    # 1. ゲーム状態初期化
    db.execute_query("INSERT INTO game_state (week, economic_index) VALUES (1, 1.0)")

    # 設定値
    NUM_MAKERS = 8
    NUM_RETAILERS = 3
    INITIAL_FUNDS = 3_000_000_000 # 10億円
    EMPLOYEE_STAT = 50
    EMPLOYEES_PER_DEPT = 2
    MAKER_INITIAL_STOCK = 50
    RETAIL_INITIAL_STOCK_TOTAL = 100
    NUM_UNEMPLOYED = 15000
    VACANT_FACILITY_CAPACITY = 20000

    MAKER_DEPTS = [gb.DEPT_PRODUCTION, gb.DEPT_DEV, gb.DEPT_SALES, gb.DEPT_HR, gb.DEPT_PR, gb.DEPT_ACCOUNTING]
    RETAIL_DEPTS = [gb.DEPT_STORE, gb.DEPT_SALES, gb.DEPT_HR, gb.DEPT_PR, gb.DEPT_ACCOUNTING]

    npc_data_list = []
    
    # ---------------------------------------------------------
    # 2. サプライヤー生成
    # ---------------------------------------------------------
    print("Generating Suppliers...")
    supplier_ids = {}
    for ind in gb.INDUSTRIES.values():
        for part in ind['parts']:
            if part['key'] not in supplier_ids:
                supplier_ids[part['key']] = []
                s_name = name_generator.generate_supplier_name(part['label'])
                sid = db.execute_query("""
                    INSERT INTO companies (name, type, funds, trait_material_score, trait_cost_multiplier, part_category)
                    VALUES (?, 'system_supplier', 0, 3.0, 1.0, ?)
                """, (s_name, part['key']))
                supplier_ids[part['key']].append(sid)

    # ---------------------------------------------------------
    # 3. プレイヤー企業生成 (最低限プレイ可能な状態にする)
    # ---------------------------------------------------------
    print("Generating Player Company...")
    player_id = db.execute_query("""
        INSERT INTO companies (name, type, funds, stock_price, outstanding_shares, market_cap, listing_status) 
        VALUES ('Player Corp', 'player', ?, ?, ?, ?, 'private')
    """, (INITIAL_FUNDS, gb.INITIAL_STOCK_PRICE, gb.INITIAL_SHARES, gb.INITIAL_STOCK_PRICE * gb.INITIAL_SHARES))
    
    p_div_auto = db.execute_query("INSERT INTO divisions (company_id, name, industry_key) VALUES (?, ?, ?)", (player_id, "自動車事業部", "automotive"))
    p_div_pc = db.execute_query("INSERT INTO divisions (company_id, name, industry_key) VALUES (?, ?, ?)", (player_id, "PC事業部", "pc"))
    
    # プレイヤーにも初期施設と従業員を付与 (自動車事業部のみ)
    db.execute_query("INSERT INTO facilities (type, size, rent, is_owned, company_id, division_id, name) VALUES ('office', 50, ?, 0, ?, NULL, '本社オフィス')", (50 * gb.RENT_OFFICE, player_id))
    db.execute_query("INSERT INTO facilities (type, size, rent, is_owned, company_id, division_id, name) VALUES ('office', 50, ?, 0, ?, ?, '事業部オフィス')", (50 * gb.RENT_OFFICE, player_id, p_div_auto))
    db.execute_query("INSERT INTO facilities (type, size, rent, is_owned, company_id, division_id, name) VALUES ('factory', 20, ?, 0, ?, ?, '工場')", (20 * gb.RENT_FACTORY, player_id, p_div_auto))

    for dept in MAKER_DEPTS:
        for i in range(EMPLOYEES_PER_DEPT):
            role = gb.ROLE_MANAGER if i == 0 else gb.ROLE_MEMBER
            stats = {k: EMPLOYEE_STAT for k in ["diligence", "management", "adaptability", "store_ops", "production", "development", "sales", "hr", "pr", "accounting", "executive"]}
            apts = {k: 1.0 for k in gb.INDUSTRIES.keys()}
            gender = random.choice(["M", "F"])
            name = name_generator.generate_person_name(gender)
            
            # 共通部門は事業部IDなし
            target_div = p_div_auto if dept in [gb.DEPT_PRODUCTION, gb.DEPT_DEV, gb.DEPT_SALES] else None
            
            npc_data_list.append(create_npc_tuple(
                name, 30, gender, player_id, target_div, dept, role, gb.BASE_SALARY_YEARLY, stats, apts
            ))

    # ---------------------------------------------------------
    # 4. NPC企業生成
    # ---------------------------------------------------------
    print("Generating NPC Companies...")
    
    industry_designs = {k: [] for k in gb.INDUSTRIES.keys()} # industry_key -> list of design dicts
    retailer_ids = {k: [] for k in gb.INDUSTRIES.keys()} # industry_key -> list of (company_id, division_id)

    for ind_key, ind_def in gb.INDUSTRIES.items():
        
        # メーカー生成
        for i in range(NUM_MAKERS):
            name = name_generator.generate_company_name('npc_maker')
            mid = db.execute_query("""
                INSERT INTO companies (name, type, funds, stock_price, outstanding_shares, market_cap, listing_status, orientation, industry, brand_power) 
                VALUES (?, 'npc_maker', ?, ?, ?, ?, 'public', 'standard', ?, 50)
            """, (name, INITIAL_FUNDS, gb.INITIAL_STOCK_PRICE, gb.INITIAL_SHARES, gb.INITIAL_STOCK_PRICE * gb.INITIAL_SHARES, ind_key))
            
            div_id = db.execute_query("INSERT INTO divisions (company_id, name, industry_key) VALUES (?, ?, ?)", (mid, f"{ind_def['name']}事業部", ind_key))
            
            # 施設
            db.execute_query("INSERT INTO facilities (type, size, rent, is_owned, company_id, division_id, name) VALUES ('office', 50, ?, 0, ?, NULL, '本社オフィス')", (50 * gb.RENT_OFFICE, mid))
            db.execute_query("INSERT INTO facilities (type, size, rent, is_owned, company_id, division_id, name) VALUES ('office', 50, ?, 0, ?, ?, '事業部オフィス')", (50 * gb.RENT_OFFICE, mid, div_id))
            db.execute_query("INSERT INTO facilities (type, size, rent, is_owned, company_id, division_id, name) VALUES ('factory', 20, ?, 0, ?, ?, '工場')", (20 * gb.RENT_FACTORY, mid, div_id))
            
            # 従業員
            for dept in MAKER_DEPTS:
                for j in range(EMPLOYEES_PER_DEPT):
                    role = gb.ROLE_MANAGER if j == 0 else gb.ROLE_MEMBER
                    stats = {k: EMPLOYEE_STAT for k in ["diligence", "management", "adaptability", "store_ops", "production", "development", "sales", "hr", "pr", "accounting", "executive"]}
                    apts = {k: 1.0 for k in gb.INDUSTRIES.keys()}
                    gender = random.choice(["M", "F"])
                    p_name = name_generator.generate_person_name(gender)
                    
                    target_div = div_id if dept in [gb.DEPT_PRODUCTION, gb.DEPT_DEV, gb.DEPT_SALES] else None
                    
                    npc_data_list.append(create_npc_tuple(
                        p_name, 30, gender, mid, target_div, dept, role, gb.BASE_SALARY_YEARLY, stats, apts
                    ))
            
            # CEO (別途追加)
            ceo_name = name_generator.generate_person_name('M')
            stats = {k: EMPLOYEE_STAT for k in ["diligence", "management", "adaptability", "store_ops", "production", "development", "sales", "hr", "pr", "accounting", "executive"]}
            apts = {k: 1.0 for k in gb.INDUSTRIES.keys()}
            npc_data_list.append(create_npc_tuple(
                ceo_name, 50, 'M', mid, None, gb.DEPT_HR, gb.ROLE_CEO, gb.BASE_SALARY_YEARLY * 2, stats, apts
            ))

            # 設計書と在庫
            # 初期製品を2つ生成
            for _ in range(2):
                # パーツ構成
                parts_config = {}
                total_material_cost = 0
                for part in ind_def['parts']:
                    sid = supplier_ids[part['key']][0]
                    cost = int(part['base_cost'])
                    parts_config[part['key']] = {"supplier_id": sid, "score": 3.0, "cost": cost}
                    total_material_cost += cost
                
                base_price = int(total_material_cost * 3.0)
                prod_name = name_generator.generate_product_name()
                
                design_id = db.execute_query("""
                    INSERT INTO product_designs (company_id, division_id, industry_key, name, material_score, concept_score, production_efficiency, base_price, sales_price, status, developed_week, parts_config)
                    VALUES (?, ?, ?, ?, 3.0, 3.0, ?, ?, ?, 'completed', 0, ?)
                """, (mid, div_id, ind_key, prod_name, ind_def['production_efficiency_base'], base_price, base_price, json.dumps(parts_config)))
                
                # 在庫
                db.execute_query("INSERT INTO inventory (company_id, division_id, design_id, quantity, sales_price) VALUES (?, ?, ?, ?, ?)", 
                                 (mid, div_id, design_id, MAKER_INITIAL_STOCK, base_price))
                
                industry_designs[ind_key].append({'id': design_id, 'price': base_price})

        # 小売生成
        for i in range(NUM_RETAILERS):
            name = name_generator.generate_company_name('npc_retail')
            rid = db.execute_query("""
                INSERT INTO companies (name, type, funds, stock_price, outstanding_shares, market_cap, listing_status, orientation, industry, brand_power) 
                VALUES (?, 'npc_retail', ?, ?, ?, ?, 'public', 'standard', ?, 50)
            """, (name, INITIAL_FUNDS, gb.INITIAL_STOCK_PRICE, gb.INITIAL_SHARES, gb.INITIAL_STOCK_PRICE * gb.INITIAL_SHARES, ind_key))
            
            div_id = db.execute_query("INSERT INTO divisions (company_id, name, industry_key) VALUES (?, ?, ?)", (rid, "販売事業部", ind_key))
            retailer_ids[ind_key].append((rid, div_id))
            
            # 施設
            db.execute_query("INSERT INTO facilities (type, size, rent, is_owned, company_id, division_id, name) VALUES ('office', 50, ?, 0, ?, NULL, '本社オフィス')", (50 * gb.RENT_OFFICE, rid))
            db.execute_query("INSERT INTO facilities (type, size, rent, is_owned, company_id, division_id, name) VALUES ('office', 50, ?, 0, ?, ?, '事業部オフィス')", (50 * gb.RENT_OFFICE, rid, div_id))
            db.execute_query("INSERT INTO facilities (type, size, rent, is_owned, company_id, division_id, name) VALUES ('store', 20, ?, 0, ?, ?, '店舗')", (20 * gb.RENT_STORE_BASE, rid, div_id))
            
            # 従業員
            for dept in RETAIL_DEPTS:
                for j in range(EMPLOYEES_PER_DEPT):
                    role = gb.ROLE_MANAGER if j == 0 else gb.ROLE_MEMBER
                    stats = {k: EMPLOYEE_STAT for k in ["diligence", "management", "adaptability", "store_ops", "production", "development", "sales", "hr", "pr", "accounting", "executive"]}
                    apts = {k: 1.0 for k in gb.INDUSTRIES.keys()}
                    gender = random.choice(["M", "F"])
                    p_name = name_generator.generate_person_name(gender)
                    
                    target_div = div_id if dept in [gb.DEPT_STORE, gb.DEPT_SALES] else None
                    
                    npc_data_list.append(create_npc_tuple(
                        p_name, 30, gender, rid, target_div, dept, role, gb.BASE_SALARY_YEARLY, stats, apts
                    ))
            
            # CEO
            ceo_name = name_generator.generate_person_name('F')
            stats = {k: EMPLOYEE_STAT for k in ["diligence", "management", "adaptability", "store_ops", "production", "development", "sales", "hr", "pr", "accounting", "executive"]}
            apts = {k: 1.0 for k in gb.INDUSTRIES.keys()}
            npc_data_list.append(create_npc_tuple(
                ceo_name, 50, 'F', rid, None, gb.DEPT_HR, gb.ROLE_CEO, gb.BASE_SALARY_YEARLY * 2, stats, apts
            ))

    # 小売の初期在庫生成 (全メーカー生成後に実施)
    for ind_key, retailers in retailer_ids.items():
        designs = industry_designs[ind_key]
        if not designs: continue
        
        for rid, div_id in retailers:
            # ランダムに設計書を選んで合計100台にする
            # 5種類選んで20台ずつとする
            selected_designs = random.choices(designs, k=5)
            for d in selected_designs:
                db.execute_query("INSERT INTO inventory (company_id, division_id, design_id, quantity, sales_price) VALUES (?, ?, ?, ?, ?)", 
                                 (rid, div_id, d['id'], 20, d['price']))

    # ---------------------------------------------------------
    # 5. 無職NPC生成
    # ---------------------------------------------------------
    print(f"Generating {NUM_UNEMPLOYED} Unemployed NPCs...")
    for _ in range(NUM_UNEMPLOYED):
        npc_data_list.append(generate_unemployed_npc())

    # バッチインサート
    if npc_data_list:
        placeholders = ','.join(['?'] * len(NPC_COLUMNS))
        col_str = ','.join(NPC_COLUMNS)
        conn, should_close = db.get_connection()
        try:
            # SQLite limit is usually 999 variables, so we need to chunk if too large, 
            # but executemany handles this in Python sqlite3 usually.
            # However, to be safe with large data, let's chunk manually if needed or trust the driver.
            # Python's executemany is optimized.
            conn.executemany(f"INSERT INTO npcs ({col_str}) VALUES ({placeholders})", npc_data_list)
            if should_close:
                conn.commit()
        finally:
            if should_close:
                conn.close()

    # ---------------------------------------------------------
    # 6. 空き物件生成
    # ---------------------------------------------------------
    print("Generating Vacant Facilities...")
    market_facilities = []
    current_cap = 0
    while current_cap < VACANT_FACILITY_CAPACITY:
        size = random.choice([20, 50, 100])
        ftype = random.choice(['office', 'factory', 'store'])
        rent = 0
        if ftype == 'office': rent = size * gb.RENT_OFFICE
        elif ftype == 'factory': rent = size * gb.RENT_FACTORY
        elif ftype == 'store': rent = size * gb.RENT_STORE_BASE
        
        name = name_generator.generate_facility_name(ftype)
        market_facilities.append((ftype, size, rent, 0, None, None, name)) # is_owned=0, company_id=None, division_id=None
        current_cap += size
    
    if market_facilities:
        conn, should_close = db.get_connection()
        try:
            conn.executemany("INSERT INTO facilities (type, size, rent, is_owned, company_id, division_id, name) VALUES (?, ?, ?, ?, ?, ?, ?)", market_facilities)
            if should_close:
                conn.commit()
        finally:
            if should_close:
                conn.close()

    print("Seed data initialized.")

if __name__ == "__main__":
    run_seed()
