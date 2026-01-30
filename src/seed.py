# c:\0124newSIm\seed.py
# 初期データを生成・投入するスクリプト

import json
import random
from database import db
import gamebalance as gb
import name_generator

# NPCテーブルのカラム順序を固定定義
NPC_COLUMNS = [
    "name", "age", "gender", "company_id", "department", "role", 
    "salary", "desired_salary", "loyalty", "is_genius", 
    "last_resigned_week", "last_company_id", 
    "diligence", "management", "adaptability", "store_ops", 
    "production", "development", "sales", "hr", "pr", "accounting", 
    "executive", "industry_aptitude"
]

def generate_random_npc(age=None, company_id=None, department=None, role=None):
    if age is None:
        age = random.randint(gb.START_AGE, 40)
    
    is_genius = random.random() < gb.GENIUS_RATE
    base_stat_min = 30 if is_genius else 0
    base_stat_max = 60 if is_genius else 40

    # 年齢による成長補正 (22歳から1歳あたり平均1.0上昇と仮定)
    age_bonus = max(0, (age - gb.START_AGE) * 1.0)

    def get_stat():
        base = random.randint(base_stat_min, base_stat_max)
        return min(100, int(base + age_bonus))
    
    gender = random.choice(["M", "F"])
    name = name_generator.generate_person_name(gender)

    npc = {
        "name": name,
        "age": age,
        "gender": gender,
        "company_id": company_id,
        "department": department,
        "role": role,
        "salary": 0, # 後で計算
        "desired_salary": 0,
        "loyalty": 50,
        "is_genius": is_genius,
        "last_resigned_week": 0,
        "last_company_id": None,
        "diligence": get_stat(),
        "management": get_stat(),
        "adaptability": get_stat(),
        "store_ops": get_stat(),
        "production": get_stat(),
        "development": get_stat(),
        "sales": get_stat(),
        "hr": get_stat(),
        "pr": get_stat(),
        "accounting": get_stat(),
        "executive": get_stat(),
        "industry_aptitude": 0.1
    }
    
    # 給与計算: 最高能力値に基づく
    max_stat = max(
        npc['production'], npc['sales'], npc['development'], 
        npc['hr'], npc['pr'], npc['accounting'], npc['store_ops']
    )
    # 基準: 能力50で400万円。最低200万円。
    salary = int(gb.BASE_SALARY_YEARLY * (max_stat / 50.0))
    npc['salary'] = max(2000000, salary)
    npc['desired_salary'] = npc['salary'] # 初期は満足している状態
    
    return npc

def generate_facilities_data(req_factory_cap, req_store_cap, req_office_cap):
    facilities = []
    
    # Factory
    current_cap = 0
    while current_cap < req_factory_cap:
        size = random.choice([10, 20, 50, 100])
        rent = size * gb.RENT_FACTORY
        name = name_generator.generate_facility_name('factory')
        facilities.append(('factory', size, rent, None, 0, name))
        current_cap += size
    
    # Office
    current_cap = 0
    while current_cap < req_office_cap:
        size = random.choice([10, 20, 50, 100])
        rent = size * gb.RENT_OFFICE
        name = name_generator.generate_facility_name('office')
        facilities.append(('office', size, rent, None, 0, name))
        current_cap += size
        
    # Store
    current_cap = 0
    while current_cap < req_store_cap:
        size = random.choice([5, 10, 20])
        access = random.choice(['S', 'A', 'B', 'C', 'D'])
        # アクセスによる賃料補正 (簡易)
        access_mult = {'S': 2.0, 'A': 1.5, 'B': 1.0, 'C': 0.8, 'D': 0.5}
        rent = int(size * gb.RENT_STORE_BASE * access_mult[access])
        name = name_generator.generate_facility_name('store')
        facilities.append(('store', size, rent, access, 0, name))
        current_cap += size
        
    return facilities

def run_seed():
    db.init_db()
    
    # 1. ゲーム状態初期化
    db.execute_query("INSERT INTO game_state (week, economic_index) VALUES (1, 1.0)")

    # 市場規模に応じた生成数の計算
    demand = gb.BASE_MARKET_DEMAND
    
    # 企業数: 需要200台につきメーカー1社、100台につき小売1社 (最低数は確保)
    num_npc_makers = 8 # 競合を増やして難易度アップ
    num_npc_retailers = 3
    
    # 2. 企業作成
    # プレイヤー企業
    player_id = db.execute_query("""
        INSERT INTO companies (name, type, funds) 
        VALUES ('Player Corp', 'player', ?)
    """, (gb.INITIAL_FUNDS_MAKER,))

    # NPCメーカー
    npc_maker_ids = []
    for i in range(num_npc_makers):
        name = name_generator.generate_company_name('npc_maker')
        mid = db.execute_query("""
            INSERT INTO companies (name, type, funds) 
            VALUES (?, 'npc_maker', ?)
        """, (name, gb.INITIAL_FUNDS_MAKER,))
        npc_maker_ids.append(mid)

    # NPC小売
    for i in range(num_npc_retailers):
        name = name_generator.generate_company_name('npc_retail')
        db.execute_query("""
            INSERT INTO companies (name, type, funds) 
            VALUES (?, 'npc_retail', ?)
        """, (name, gb.INITIAL_FUNDS_RETAIL,))

    # システムサプライヤー (各パーツごとに3社)
    supplier_templates = [
        {"score": 2.0, "cost": 0.8},
        {"score": 3.0, "cost": 1.0},
        {"score": 4.5, "cost": 1.5}
    ]
    
    for part in gb.INDUSTRIES[gb.CURRENT_INDUSTRY]['parts']:
        for s in supplier_templates:
            s_name = name_generator.generate_supplier_name(part['label'])
            db.execute_query("""
                INSERT INTO companies (name, type, funds, trait_material_score, trait_cost_multiplier, part_category)
                VALUES (?, 'system_supplier', 0, ?, ?, ?)
            """, (s_name, s['score'], s['cost'], part['key']))

    # 3. NPC生成 (従業員 + 失業者)
    npc_data_list = []
    
    # 企業ごとの必要人員計算と生成
    # メーカー (NPC Makers only - Player starts with nothing)
    maker_ids = npc_maker_ids
    maker_share = demand / len(maker_ids)
    
    # 小売 (NPC Retailers)
    retail_ids = []
    retail_rows = db.fetch_all("SELECT id FROM companies WHERE type='npc_retail'")
    for r in retail_rows: retail_ids.append(r['id'])
    
    retail_share = demand / len(retail_ids)

    # 施設生成用の集計
    total_factory_needs = 0
    total_store_needs = 0
    total_office_needs = 0
    company_facilities_req = {} # {company_id: {factory: 0, store: 0, office: 0}}

    def add_employees(company_id, c_type, share):
        # 部署ごとの必要人数 (要件定義のコスト構造に基づく)
        staff_req = {}
        if c_type == 'maker':
            # 生産能力: 初期NPCの能力値(平均30程度)が基準(50)より低いため、実効効率は0.6倍程度になる。
            # 競争を発生させるため、実効供給力が需要を上回るように係数を強化する (1.5 -> 2.5)
            # 1人あたり生産効率: gb.BASE_PRODUCTION_EFFICIENCY
            needed_prod = (share * 2.5) / gb.BASE_PRODUCTION_EFFICIENCY
            staff_req[gb.DEPT_PRODUCTION] = max(1, int(needed_prod * 1.2 / gb.NPC_SCALE_FACTOR))
            
            # 他部署は生産人員に対する比率で設定
            staff_req[gb.DEPT_DEV] = max(1, int(staff_req[gb.DEPT_PRODUCTION] * 0.28))
            staff_req[gb.DEPT_SALES] = max(1, int(staff_req[gb.DEPT_PRODUCTION] * 0.06))
            staff_req[gb.DEPT_ACCOUNTING] = max(1, int(staff_req[gb.DEPT_PRODUCTION] * 0.07))
            staff_req[gb.DEPT_PR] = max(1, int(staff_req[gb.DEPT_PRODUCTION] * 0.05))
            
            # 人事: 全従業員数 / HR_CAPACITY * 余裕係数(1.5)
            total_others = sum(staff_req.values()) * gb.NPC_SCALE_FACTOR
            staff_req[gb.DEPT_HR] = max(1, int((total_others / gb.HR_CAPACITY_PER_PERSON) * 1.5 / gb.NPC_SCALE_FACTOR))
        else:
            # 店舗能力: 同様に初期能力不足を考慮して強化 (1.0 -> 2.5)
            needed_store = (share * 2.5) / gb.BASE_SALES_EFFICIENCY
            staff_req[gb.DEPT_STORE] = max(1, int(needed_store * 1.2 / gb.NPC_SCALE_FACTOR))
            
            # 他部署
            staff_req[gb.DEPT_SALES] = max(1, int(staff_req[gb.DEPT_STORE] * 0.1))
            staff_req[gb.DEPT_PR] = max(1, int(staff_req[gb.DEPT_STORE] * 0.1))
            staff_req[gb.DEPT_ACCOUNTING] = max(1, int(staff_req[gb.DEPT_STORE] * 0.15))
            
            # 人事: 余裕係数(2.0) - 小売は人数が少ないので多めに
            total_others = sum(staff_req.values()) * gb.NPC_SCALE_FACTOR
            staff_req[gb.DEPT_HR] = max(1, int((total_others / gb.HR_CAPACITY_PER_PERSON) * 2.0 / gb.NPC_SCALE_FACTOR))

        # CEO生成 (HR所属とする)
        ceo = generate_random_npc(age=random.randint(40, 60), company_id=company_id, department=gb.DEPT_HR, role=gb.ROLE_CEO)
        # CEOは能力高め
        ceo['management'] = random.randint(70, 100)
        ceo['executive'] = random.randint(70, 100)
        # 固定順序でタプル化
        npc_data_list.append(tuple(ceo[col] for col in NPC_COLUMNS))

        c_fac_req = {'factory': 0, 'store': 0, 'office': 0}

        for dept, count in staff_req.items():
            if count <= 0: continue
            
            # 施設要件加算
            if dept == gb.DEPT_PRODUCTION: c_fac_req['factory'] += count * gb.NPC_SCALE_FACTOR
            elif dept == gb.DEPT_STORE: c_fac_req['store'] += count * gb.NPC_SCALE_FACTOR
            else: c_fac_req['office'] += count * gb.NPC_SCALE_FACTOR

            for i in range(count):
                role = gb.ROLE_MEMBER
                # 部長は各部署1人のみ
                if i == 0: role = gb.ROLE_MANAGER
                
                npc = generate_random_npc(company_id=company_id, department=dept, role=role)
                npc_data_list.append(tuple(npc[col] for col in NPC_COLUMNS))
        
        company_facilities_req[company_id] = c_fac_req
        return c_fac_req

    print("Generating Employees...")
    for mid in maker_ids:
        req = add_employees(mid, 'maker', maker_share)
        total_factory_needs += req['factory']
        total_office_needs += req['office']

    for rid in retail_ids:
        req = add_employees(rid, 'retail', retail_share)
        total_store_needs += req['store']
        total_office_needs += req['office']

    # 失業者生成 (全体失業率5% -> 雇用者数 / 0.95 = 全体数)
    employed_count = len(npc_data_list)
    total_population = int(employed_count / 0.7)
    unemployed_count = total_population - employed_count
    
    print(f"Employed: {employed_count}, Unemployed: {unemployed_count}, Total: {total_population}")
    
    for _ in range(unemployed_count):
        npc = generate_random_npc(company_id=None)
        npc_data_list.append(tuple(npc[col] for col in NPC_COLUMNS))

    # 高速化のためバッチインサート
    # カラム順序を固定定義したものを使用
    columns = NPC_COLUMNS
    
    if npc_data_list:
        placeholders = ','.join(['?'] * len(columns))
        col_str = ','.join(columns)
        conn, should_close = db.get_connection()
        try:
            conn.executemany(f"INSERT INTO npcs ({col_str}) VALUES ({placeholders})", npc_data_list)
            if should_close:
                conn.commit()
        finally:
            if should_close:
                conn.close()

    # 4. 初期商品設計書 (各NPCメーカー)
    # 標準的なパーツ構成を作成
    model_t_parts = {}
    for part in gb.INDUSTRIES[gb.CURRENT_INDUSTRY]['parts']:
        # Standard Materials Inc. を探す
        supplier = db.fetch_one("""
            SELECT id, trait_material_score, trait_cost_multiplier 
            FROM companies 
            WHERE type='system_supplier' AND part_category=? AND trait_material_score=3.0
        """, (part['key'],))
        
        model_t_parts[part['key']] = {
            "supplier_id": supplier['id'],
            "score": supplier['trait_material_score'],
            "cost": int(part['base_cost'] * supplier['trait_cost_multiplier'])
        }

    # メーカー1社あたりの週次期待需要 (市場規模 / メーカー数)
    expected_maker_demand = int(gb.BASE_MARKET_DEMAND / len(maker_ids))
    
    # 初期モデルの基準価格を、開発完了時と同じロジックで計算
    initial_material_cost = sum(p['cost'] for p in model_t_parts.values())
    initial_concept_score = 3.0
    initial_base_price = int(initial_material_cost * ((initial_concept_score + 3.0) / 2.0))

    # 全メーカー（プレイヤー含む）に設計書と在庫を付与
    maker_design_map = {} # {maker_id: design_id} 小売在庫生成用

    for mid in maker_ids:
        p_name = name_generator.generate_product_name()
        design_id = db.execute_query("""
            INSERT INTO product_designs (company_id, name, material_score, concept_score, production_efficiency, base_price, sales_price, status, developed_week, parts_config)
            VALUES (?, ?, 3.0, 3.0, 1.0, ?, ?, 'completed', 0, ?)
        """, (mid, p_name, initial_base_price, initial_base_price, json.dumps(model_t_parts)))
        maker_design_map[mid] = design_id

        # 5. 初期在庫
        # 2週分程度の在庫を持たせる
        initial_stock = expected_maker_demand * 2
        db.execute_query("INSERT INTO inventory (company_id, design_id, quantity, sales_price) VALUES (?, ?, ?, ?)", (mid, design_id, initial_stock, initial_base_price))

    # プレイヤー企業用: 初期設計書のみ付与 (在庫なし)
    # これにより、ゲーム開始直後から生産活動が可能になる
    p_name = name_generator.generate_product_name()
    db.execute_query("""
        INSERT INTO product_designs (company_id, name, material_score, concept_score, production_efficiency, base_price, sales_price, status, developed_week, parts_config)
        VALUES (?, ?, 3.0, 3.0, 1.0, ?, ?, 'completed', 0, ?)
    """, (player_id, p_name, initial_base_price, initial_base_price, json.dumps(model_t_parts)))

    # 5.5 初期在庫 (小売)
    # 小売も2週分の需要に対応できる在庫を持たせる
    # 各メーカーの商品を均等に取り扱うと仮定
    if retail_ids:
        expected_retail_demand = int(gb.BASE_MARKET_DEMAND / len(retail_ids))
        stock_per_model = int((expected_retail_demand * 2) / len(maker_ids))
        
        for rid in retail_ids:
            for mid, did in maker_design_map.items():
                db.execute_query("INSERT INTO inventory (company_id, design_id, quantity, sales_price) VALUES (?, ?, ?, ?)", 
                                 (rid, did, stock_per_model, initial_base_price))

    # 6. 施設生成
    # 各企業に必要な施設を生成して割り当てる
    facilities_data = []
    
    for cid, req in company_facilities_req.items():
        # 自社用 (余裕を持って1.2倍)
        f_data = generate_facilities_data(int(req['factory'] * 1.2), int(req['store'] * 1.2), int(req['office'] * 1.2))
        # company_idとis_ownedを設定
        for i in range(len(f_data)):
            # f_data is list of tuples: (type, size, rent, access, is_owned, name)
            # We need to insert company_id into the query or modify data
            # DB schema: type, size, rent, access_score, is_owned, company_id, name
            # generate_facilities_data returns tuples for INSERT without company_id
            # Let's modify generate_facilities_data or just construct here.
            # Actually generate_facilities_data returns (type, size, rent, access, 0, name)
            # We want to set company_id and is_owned=0 (rented)
            ftype, size, rent, access, _, fname = f_data[i]
            # 賃貸契約済みとして登録
            db.execute_query("INSERT INTO facilities (type, size, rent, access_score, is_owned, company_id, name) VALUES (?, ?, ?, ?, 0, ?, ?)", 
                             (ftype, size, rent, access, cid, fname))

    # 市場の空き物件 (全体需要の20%程度を追加)
    market_factory = int(total_factory_needs * 0.2)
    market_store = int(total_store_needs * 0.2)
    market_office = int(total_office_needs * 0.2)
    
    market_facilities = generate_facilities_data(market_factory, market_store, market_office)
    
    if market_facilities:
        conn, should_close = db.get_connection()
        try:
            conn.executemany("INSERT INTO facilities (type, size, rent, access_score, is_owned, name) VALUES (?, ?, ?, ?, ?, ?)", market_facilities)
            if should_close:
                conn.commit()
        finally:
            if should_close:
                conn.close()

    print("Seed data initialized.")

if __name__ == "__main__":
    run_seed()
