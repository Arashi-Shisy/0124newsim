# c:\0124newSIm\src\seed.py
# 初期データを生成・投入するスクリプト

import json
import random
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

def generate_random_npc(age=None, company_id=None, division_id=None, department=None, role=None):
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

    # 全業界に対して初期適性 0.1 を設定
    aptitudes = {ind: 0.1 for ind in gb.INDUSTRIES.keys()}

    npc = {
        "name": name,
        "age": age,
        "gender": gender,
        "company_id": company_id,
        "division_id": division_id,
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
        "aptitudes": json.dumps(aptitudes)
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

    # 業界ごとの需要合計を計算
    industry_demands = {}
    total_world_demand = 0
    for ind_key, ind_val in gb.INDUSTRIES.items():
        d = sum(cat['base_demand'] for cat in ind_val['categories'].values())
        industry_demands[ind_key] = d
        total_world_demand += d
    
    # メーカー総数 (市場規模に合わせて設定)
    total_makers = 14
    num_npc_retailers = 3
    
    # 割り当て数計算 (最低2社保証)
    industry_counts = {k: 2 for k in industry_demands.keys()}
    remaining_slots = total_makers - sum(industry_counts.values())
    
    if remaining_slots > 0:
        # 需要比率に基づいて残りを配分
        allocations = {}
        for k, d in industry_demands.items():
            allocations[k] = (d / total_world_demand) * remaining_slots
        
        # 整数部を加算
        for k in allocations:
            count = int(allocations[k])
            industry_counts[k] += count
            allocations[k] -= count # 小数部を残す
            
        # 端数を小数部の大きい順に割り当て
        remaining_slots = total_makers - sum(industry_counts.values())
        sorted_keys = sorted(allocations.keys(), key=lambda k: allocations[k], reverse=True)
        for i in range(remaining_slots):
            industry_counts[sorted_keys[i % len(sorted_keys)]] += 1
            
    print(f"Industry Maker Counts: {industry_counts}")
    
    # 2. 企業作成
    # プレイヤー企業
    player_id = db.execute_query("""
        INSERT INTO companies (name, type, funds, stock_price, outstanding_shares, market_cap, listing_status) 
        VALUES ('Player Corp', 'player', ?, ?, ?, ?, 'private')
    """, (gb.INITIAL_FUNDS_MAKER, gb.INITIAL_STOCK_PRICE, gb.INITIAL_SHARES, gb.INITIAL_STOCK_PRICE * gb.INITIAL_SHARES))
    
    # プレイヤー事業部作成 (自動車、家電)
    p_div_auto = db.execute_query("INSERT INTO divisions (company_id, name, industry_key) VALUES (?, ?, ?)", (player_id, "自動車事業部", "automotive"))
    p_div_home = db.execute_query("INSERT INTO divisions (company_id, name, industry_key) VALUES (?, ?, ?)", (player_id, "家電事業部", "home_appliances"))

    # NPCメーカー
    npc_maker_ids = []
    maker_share_map = {} # id -> share_qty

    # シェア分布のベース (Power Law)
    base_shares_dist = [0.25, 0.20, 0.15, 0.12, 0.10, 0.08, 0.05, 0.03, 0.02]

    for ind_key, count in industry_counts.items():
        # この業界のシェア配分を決定
        if count > len(base_shares_dist):
            shares = base_shares_dist + [0.01] * (count - len(base_shares_dist))
        else:
            shares = base_shares_dist[:count]
        
        # 正規化
        total_s = sum(shares)
        shares = [s / total_s for s in shares]
        
        # シェア順に企業生成
        for i, share in enumerate(shares):
            name = name_generator.generate_company_name('npc_maker')
            
            # 経営方針の決定 (上位はLuxury/Standard寄り、下位はValue/Niche寄り)
            if i == 0: # Top share
                orientation = random.choice(['standard', 'luxury'])
            elif i < count / 2:
                orientation = random.choice(['standard', 'value'])
            else:
                orientation = random.choice(['value', 'standard'])
            
            # 初期資金 (シェアに応じて傾斜)
            funds = int(gb.INITIAL_FUNDS_MAKER * (share / 0.1))
            # ブランド力
            brand_power = int(50 * (share / 0.1))
            brand_power = min(100, max(10, brand_power))

            mid = db.execute_query("""
                INSERT INTO companies (name, type, funds, stock_price, outstanding_shares, market_cap, listing_status, orientation, industry, brand_power) 
                VALUES (?, 'npc_maker', ?, ?, ?, ?, 'public', ?, ?, ?)
            """, (name, funds, gb.INITIAL_STOCK_PRICE, gb.INITIAL_SHARES, gb.INITIAL_STOCK_PRICE * gb.INITIAL_SHARES, orientation, ind_key, brand_power))
            npc_maker_ids.append(mid)
            
            # シェアに基づく需要数
            maker_share_map[mid] = industry_demands[ind_key] * share

    # NPC小売
    npc_retail_ids = []
    retail_share_map = {} # id -> share_qty

    # 小売は各業界3社ずつ (需要をちょうど満たす規模で)
    retail_counts = {k: 3 for k in industry_demands.keys()}

    for ind_key, count in retail_counts.items():
        # 均等割り (小売は地域独占的な側面もあるため簡易的に)
        share_per_company = 1.0 / count
        
        for _ in range(count):
            name = name_generator.generate_company_name('npc_retail')
            orientation = random.choice(['standard', 'value'])
            
            rid = db.execute_query("""
                INSERT INTO companies (name, type, funds, stock_price, outstanding_shares, market_cap, listing_status, orientation, industry) 
                VALUES (?, 'npc_retail', ?, ?, ?, ?, 'public', ?, ?)
            """, (name, gb.INITIAL_FUNDS_RETAIL, gb.INITIAL_STOCK_PRICE, gb.INITIAL_SHARES, gb.INITIAL_STOCK_PRICE * gb.INITIAL_SHARES, orientation, ind_key))
            npc_retail_ids.append(rid)
            retail_share_map[rid] = industry_demands[ind_key] * share_per_company

    # システムサプライヤー (各パーツごとに3社)
    supplier_templates = [
        {"score": 2.0, "cost": 0.8},
        {"score": 3.0, "cost": 1.0},
        {"score": 4.5, "cost": 1.5}
    ]
    
    # 全業界のパーツサプライヤー生成
    for ind in gb.INDUSTRIES.values():
        for cat in ind['categories'].values():
            for part in cat['parts']:
                for s in supplier_templates:
                    s_name = name_generator.generate_supplier_name(part['label'])
                    # 重複チェックは省略（簡易）
                    db.execute_query("""
                        INSERT INTO companies (name, type, funds, trait_material_score, trait_cost_multiplier, part_category)
                        VALUES (?, 'system_supplier', 0, ?, ?, ?)
                    """, (s_name, s['score'], s['cost'], part['key']))

    # 3. NPC生成 (従業員 + 失業者)
    npc_data_list = []
    
    # 企業ごとの必要人員計算と生成
    # メーカー (NPC Makers only - Player starts with nothing)
    maker_ids = npc_maker_ids
    
    # 小売 (NPC Retailers) - 上記で生成したIDリストを使用
    retail_ids = npc_retail_ids
    
    # 施設生成用の集計
    total_factory_needs = 0
    total_store_needs = 0
    total_office_needs = 0
    company_facilities_req = {} # {company_id: {factory: 0, store: 0, office: 0}}
    company_div_map = {} # {company_id: division_id} 施設紐付け用
    
    def add_employees(company_id, division_id, c_type, share, industry_key='automotive'):
        # 部署ごとの必要人数 (要件定義のコスト構造に基づく)
        staff_req = {}
        if c_type == 'maker':
            # 業界の平均生産効率を取得
            avg_efficiency = gb.BASE_PRODUCTION_EFFICIENCY
            if industry_key in gb.INDUSTRIES:
                 cats = gb.INDUSTRIES[industry_key]['categories']
                 effs = [c.get('production_efficiency_base', gb.BASE_PRODUCTION_EFFICIENCY) for c in cats.values()]
                 if effs:
                     avg_efficiency = sum(effs) / len(effs)

            # 生産能力: 初期NPCの能力値(平均30程度)が基準(50)より低いため、実効効率は0.6倍程度になる。
            # 競争を発生させるため、実効供給力が需要を上回るように係数を強化する (1.5 -> 2.5)
            needed_prod = (share * 2.5) / avg_efficiency
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
            # 業界の販売効率を取得
            sales_efficiency = gb.BASE_SALES_EFFICIENCY
            if industry_key in gb.INDUSTRIES:
                sales_efficiency = gb.INDUSTRIES[industry_key].get('sales_efficiency_base', gb.BASE_SALES_EFFICIENCY)

            # 店舗能力: 初期能力不足を考慮して強化 (1.0 -> 2.0) - 3社でちょうど需要を満たす調整
            needed_store = (share * 2.0) / sales_efficiency
            staff_req[gb.DEPT_STORE] = max(1, int(needed_store * 1.2 / gb.NPC_SCALE_FACTOR))
            
            # 他部署
            staff_req[gb.DEPT_SALES] = max(1, int(staff_req[gb.DEPT_STORE] * 0.1))
            staff_req[gb.DEPT_PR] = max(1, int(staff_req[gb.DEPT_STORE] * 0.1))
            staff_req[gb.DEPT_ACCOUNTING] = max(1, int(staff_req[gb.DEPT_STORE] * 0.15))
            
            # 人事: 余裕係数(2.0) - 小売は人数が少ないので多めに
            total_others = sum(staff_req.values()) * gb.NPC_SCALE_FACTOR
            staff_req[gb.DEPT_HR] = max(1, int((total_others / gb.HR_CAPACITY_PER_PERSON) * 2.0 / gb.NPC_SCALE_FACTOR))

        # CEO生成 (HR所属とする)
        # CEOは共通部門(division_id=None)
        ceo = generate_random_npc(age=random.randint(40, 60), company_id=company_id, division_id=None, department=gb.DEPT_HR, role=gb.ROLE_CEO)
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
                
                # 共通部門はdivision_id=None
                target_div = division_id if dept not in [gb.DEPT_HR, gb.DEPT_PR, gb.DEPT_ACCOUNTING] else None
                
                npc = generate_random_npc(company_id=company_id, division_id=target_div, department=dept, role=role)
                npc_data_list.append(tuple(npc[col] for col in NPC_COLUMNS))
        
        company_facilities_req[company_id] = c_fac_req
        return c_fac_req

    print("Generating Employees...")
    for mid in maker_ids:
        # 企業の業界を取得
        comp_info = db.fetch_one("SELECT industry FROM companies WHERE id = ?", (mid,))
        ind_key = comp_info['industry']
        ind_name = gb.INDUSTRIES[ind_key]['name']
        
        div_id = db.execute_query("INSERT INTO divisions (company_id, name, industry_key) VALUES (?, ?, ?)", (mid, f"{ind_name}事業部", ind_key))
        demand_qty = maker_share_map.get(mid, 100)
        req = add_employees(mid, div_id, 'maker', demand_qty, industry_key=ind_key)
        company_div_map[mid] = div_id
        total_factory_needs += req['factory']
        total_office_needs += req['office']

    for rid in retail_ids:
        # 小売にも事業部を作成 (販売事業部)
        comp_info = db.fetch_one("SELECT industry FROM companies WHERE id = ?", (rid,))
        ind_key = comp_info['industry']
        div_id = db.execute_query("INSERT INTO divisions (company_id, name, industry_key) VALUES (?, ?, ?)", (rid, "販売事業部", ind_key))
        req = add_employees(rid, div_id, 'retail', retail_share_map[rid], industry_key=ind_key)
        company_div_map[rid] = div_id
        total_store_needs += req['store']
        total_office_needs += req['office']

    # 失業者生成 (全体失業率5% -> 雇用者数 / 0.95 = 全体数)
    employed_count = len(npc_data_list)
    total_population = int(employed_count / 0.95)
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
    # 各業界の代表カテゴリ（最初のカテゴリ）のテンプレートを作成
    industry_templates = {}
    
    for ind_key, ind_val in gb.INDUSTRIES.items():
        # 最初のカテゴリを取得
        first_cat_key = list(ind_val['categories'].keys())[0]
        cat_def = ind_val['categories'][first_cat_key]
        markup_modifier = ind_val.get('price_markup_modifier', 1.0)
        
        parts_config = {}
        total_material_cost = 0
        
        for part in cat_def['parts']:
            # Standard Materials Inc. (score=3.0) を探す
            supplier = db.fetch_one("""
                SELECT id, trait_material_score, trait_cost_multiplier 
                FROM companies 
                WHERE type='system_supplier' AND part_category=? AND trait_material_score=3.0
            """, (part['key'],))
            
            # 見つからなければ適当なものを
            if not supplier:
                supplier = db.fetch_one("SELECT id, trait_material_score, trait_cost_multiplier FROM companies WHERE type='system_supplier' AND part_category=? LIMIT 1", (part['key'],))
            
            if supplier:
                cost = int(part['base_cost'] * supplier['trait_cost_multiplier'])
                parts_config[part['key']] = {
                    "supplier_id": supplier['id'],
                    "score": supplier['trait_material_score'],
                    "cost": cost
                }
                total_material_cost += cost
        
        # 基準価格
        base_price = int(total_material_cost * ((3.0 + 3.0) / 2.0) * markup_modifier) # concept=3.0
        
        industry_templates[ind_key] = {
            'cat_key': first_cat_key,
            'parts_config': json.dumps(parts_config),
            'base_price': base_price
        }

    # 全メーカー（プレイヤー含む）に設計書と在庫を付与
    maker_design_map = {} # {maker_id: design_id} 小売在庫生成用

    for mid in maker_ids:
        div_id = company_div_map[mid]
        comp_info = db.fetch_one("SELECT industry FROM companies WHERE id = ?", (mid,))
        ind_key = comp_info['industry']
        template = industry_templates.get(ind_key)

        if not template: continue

        p_name = name_generator.generate_product_name()
        design_id = db.execute_query("""
            INSERT INTO product_designs (company_id, division_id, category_key, name, material_score, concept_score, production_efficiency, base_price, sales_price, status, developed_week, parts_config)
            VALUES (?, ?, ?, ?, 3.0, 3.0, 1.0, ?, ?, 'completed', 0, ?)
        """, (mid, div_id, template['cat_key'], p_name, template['base_price'], template['base_price'], template['parts_config']))
        maker_design_map[mid] = design_id

        # 5. 初期在庫
        demand_qty = maker_share_map.get(mid, 100)
        initial_stock = int(demand_qty * 2)
        db.execute_query("INSERT INTO inventory (company_id, division_id, design_id, quantity, sales_price) VALUES (?, ?, ?, ?, ?)", (mid, div_id, design_id, initial_stock, template['base_price']))

    # プレイヤー企業用: 初期設計書のみ付与 (在庫なし)
    # これにより、ゲーム開始直後から生産活動が可能になる
    # 自動車
    t_auto = industry_templates['automotive']
    p_name_auto = name_generator.generate_product_name()
    db.execute_query("""
        INSERT INTO product_designs (company_id, division_id, category_key, name, material_score, concept_score, production_efficiency, base_price, sales_price, status, developed_week, parts_config)
        VALUES (?, ?, ?, ?, 3.0, 3.0, 1.0, ?, ?, 'completed', 0, ?)
    """, (player_id, p_div_auto, t_auto['cat_key'], p_name_auto, t_auto['base_price'], t_auto['base_price'], t_auto['parts_config']))

    # 家電
    if 'home_appliances' in industry_templates:
        t_home = industry_templates['home_appliances']
        p_name_home = name_generator.generate_product_name()
        db.execute_query("""
            INSERT INTO product_designs (company_id, division_id, category_key, name, material_score, concept_score, production_efficiency, base_price, sales_price, status, developed_week, parts_config)
            VALUES (?, ?, ?, ?, 3.0, 3.0, 1.0, ?, ?, 'completed', 0, ?)
        """, (player_id, p_div_home, t_home['cat_key'], p_name_home, t_home['base_price'], t_home['base_price'], t_home['parts_config']))

    # 5.5 初期在庫 (小売)
    # 小売も2週分の需要に対応できる在庫を持たせる
    # 各メーカーの商品を均等に取り扱うと仮定
    if retail_ids:
        for rid in retail_ids:
            div_id = company_div_map.get(rid)
            retail_comp = db.fetch_one("SELECT industry FROM companies WHERE id = ?", (rid,))
            retail_ind = retail_comp['industry']
            expected_demand = retail_share_map.get(rid, 100)

            for mid, did in maker_design_map.items():
                # メーカーの業界を確認 (自社と同じ業界の商品のみ扱う)
                maker_comp = db.fetch_one("SELECT industry FROM companies WHERE id = ?", (mid,))
                if maker_comp['industry'] != retail_ind:
                    continue

                # メーカーの設計書情報を取得して価格を参照
                design = db.fetch_one("SELECT sales_price FROM product_designs WHERE id = ?", (did,))
                price = design['sales_price'] if design else 0
                
                # メーカーのシェアに応じて在庫を持つ
                ind_total_demand = industry_demands.get(retail_ind, 1)
                maker_share_pct = maker_share_map.get(mid, 0) / ind_total_demand
                stock_qty = int(expected_demand * 2 * maker_share_pct)
                db.execute_query("INSERT INTO inventory (company_id, division_id, design_id, quantity, sales_price) VALUES (?, ?, ?, ?, ?)", 
                                 (rid, div_id, did, stock_qty, price))

    # 6. 施設生成
    # 各企業に必要な施設を生成して割り当てる
    facilities_data = []
    
    for cid, req in company_facilities_req.items():
        # 自社用 (余裕を持って1.2倍)
        f_data = generate_facilities_data(int(req['factory'] * 1.2), int(req['store'] * 1.2), int(req['office'] * 1.2))
        # company_idとis_ownedを設定
        # 事業部IDを取得 (NPCは1社1事業部前提)
        div_id = company_div_map.get(cid)
        
        for i in range(len(f_data)):
            ftype, size, rent, access, _, fname = f_data[i]
            # 本社機能(HR, PR, Accounting)用のオフィス以外は事業部に紐付けるべきだが、
            # 簡易化のため、NPCの施設はすべてその唯一の事業部に紐付ける
            # 賃貸契約済みとして登録
            db.execute_query("INSERT INTO facilities (type, size, rent, access_score, is_owned, company_id, division_id, name) VALUES (?, ?, ?, ?, 0, ?, ?, ?)", 
                             (ftype, size, rent, access, cid, div_id, fname))

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
    
    # 7. 資金・株価の再計算 (規模に応じた設定)
    print("Recalculating Company Funds & Valuation...")
    
    companies = db.fetch_all("SELECT id, type FROM companies WHERE type IN ('npc_maker', 'npc_retail')")
    
    for comp in companies:
        cid = comp['id']
        
        # 固定費計算
        labor_cost = db.fetch_one("SELECT SUM(salary) as total FROM npcs WHERE company_id = ?", (cid,))['total'] or 0
        weekly_labor = (labor_cost * gb.NPC_SCALE_FACTOR) / gb.WEEKS_PER_YEAR_REAL
        
        rent_cost = db.fetch_one("SELECT SUM(rent) as total FROM facilities WHERE company_id = ?", (cid,))['total'] or 0
        
        weekly_fixed = int(weekly_labor + rent_cost)
        
        # 初期資金: 固定費の26週分 (約6ヶ月)
        initial_funds = weekly_fixed * 26
        
        # 時価総額計算
        if comp['type'] == 'npc_maker':
            share_qty = maker_share_map.get(cid, 100)
            weekly_rev = share_qty * gb.MAKER_UNIT_SALES_PRICE
        else:
            share_qty = retail_share_map.get(cid, 100)
            weekly_rev = share_qty * gb.RETAIL_UNIT_SALES_PRICE_BASE
            
        # 予想利益 (営業利益率 5%と仮定)
        weekly_profit = weekly_rev * 0.05
        yearly_profit = weekly_profit * 52
        
        # PER 15倍
        market_cap = int(yearly_profit * 15)
        market_cap = max(market_cap, initial_funds) # 最低保証
        
        # 株価固定、発行済株式数を調整
        stock_price = 50000
        outstanding_shares = int(market_cap / stock_price)
        
        db.execute_query("""
            UPDATE companies 
            SET funds = ?, market_cap = ?, outstanding_shares = ?, stock_price = ?
            WHERE id = ?
        """, (initial_funds, market_cap, outstanding_shares, stock_price, cid))

    print("Seed data initialized.")

if __name__ == "__main__":
    run_seed()
