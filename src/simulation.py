# c:\0124newSIm\src\simulation.py
# 週次シミュレーションのメインループ処理

import json
import random
import math
from database import db
import gamebalance as gb
from npc_logic import NPCLogic
import name_generator
from seed import generate_random_npc

class Simulation:
    def __init__(self):
        pass

    def get_current_week(self):
        res = db.fetch_one("SELECT week FROM game_state")
        return res['week'] if res else 0
    
    def log_news(self, week, company_id, message, type='info'):
        db.execute_query("INSERT INTO news_logs (week, company_id, message, type) VALUES (?, ?, ?, ?)",
                         (week, company_id, message, type))
    
    def calculate_capabilities(self, company_id, employees=None):
        """
        企業の能力値を計算する
        事業部制に対応: 共通部門は全社、直接部門は事業部ごとに計算
        """
        # 必要なカラムのみ取得して高速化
        if employees is None:
            employees = db.fetch_all("""
                SELECT department, role, diligence, production, development, sales, hr, pr, accounting, store_ops, management, division_id, aptitudes
                FROM npcs WHERE company_id = ?""", (company_id,))
        
        # 初期化
        keys = ['production', 'development', 'sales', 'hr', 'pr', 'accounting', 'store_ops']
        caps = {k: 0 for k in keys}
        caps.update({f"{k}_capacity": 0 for k in keys})
        caps.update({'stability': 0, 'store_throughput': 0})
        
        # 要求値と充足率の記録用
        caps['requirements'] = {
            'development': 0, 'sales': 0, 'pr': 0, 'hr': 0, 'accounting': 0
        }

        # 事業部情報の取得
        divisions = db.fetch_all("SELECT id, name, industry_key FROM divisions WHERE company_id = ?", (company_id,))
        div_map = {d['id']: {'name': d['name'], 'industry': d['industry_key'] or 'automotive'} for d in divisions}
        
        # 戻り値の構造拡張
        caps['divisions'] = {}
        for div_id, div_info in div_map.items():
            caps['divisions'][div_id] = {
                'name': div_info['name'],
                'industry': div_info['industry'],
                'production': 0, 'production_capacity': 0,
                'development': 0, 'development_capacity': 0,
                'sales': 0, 'sales_capacity': 0,
                'store_ops': 0, 'store_ops_capacity': 0, 'store_throughput': 0,
                'requirements': {'development': 0, 'sales': 0}
            }

        # 施設キャパシティの取得
        facilities = db.fetch_all("SELECT type, size, division_id FROM facilities WHERE company_id = ?", (company_id,))
        
        # 基礎キャパシティ (施設がなくても最低限活動できる場所: ガレージ/自宅など)
        # NPC_SCALE_FACTOR(8) * 1.5人分 = 12 程度確保しておく
        base_cap = int(gb.NPC_SCALE_FACTOR * 1.5)
        # 全社共通および事業部ごとの施設リミット
        caps_limit = {'office': base_cap} # 本社
        div_caps_limit = {div_id: {'factory': base_cap, 'store': base_cap, 'office': base_cap} for div_id in div_map}

        for f in facilities:
            if f['division_id'] and f['division_id'] in div_caps_limit:
                if f['type'] in div_caps_limit[f['division_id']]:
                    div_caps_limit[f['division_id']][f['type']] += f['size']
            elif f['type'] == 'office':
                caps_limit['office'] += f['size']

        # 施設稼働状況の初期化
        caps['facilities'] = {'office': {'name': '本社オフィス', 'usage': 0, 'limit': caps_limit['office'], 'efficiency': 1.0, 'npc_count': 0}}
        
        if not employees:
            return caps

        # 勤勉さ平均 (安定性)
        if employees:
            caps['stability'] = sum(e['diligence'] for e in employees) / len(employees)
        else:
            caps['stability'] = 0

        # 部署ごとの集計
        # 共通部門
        corp_depts = [gb.DEPT_HR, gb.DEPT_PR, gb.DEPT_ACCOUNTING]
        corp_staff = {d: [] for d in corp_depts}
        
        # 事業部部門
        div_depts = [gb.DEPT_PRODUCTION, gb.DEPT_DEV, gb.DEPT_SALES, gb.DEPT_STORE]
        div_staff = {div_id: {d: [] for d in div_depts} for div_id in div_map}
        
        # マネージャー・CxO
        managers = []
        cxos = []

        for e in employees:
            d = e['department']
            div_id = e['division_id']
            
            if e['role'] == gb.ROLE_MANAGER: managers.append(e)
            if e['role'] in [gb.ROLE_CXO, gb.ROLE_CEO]: cxos.append(e)
            
            if e['role'] in [gb.ROLE_CXO, gb.ROLE_CEO]: continue # 役員は現場人員に含めない
            
            if d in corp_depts:
                corp_staff[d].append(e)
            elif d in div_depts and div_id in div_staff:
                div_staff[div_id][d].append(e)
        
        # キャパシティ制限の適用 (あふれた人員は計算から除外、または効率低下)
        # 1. 共通部門 (本社オフィス)
        total_corp_staff = sum(len(corp_staff[d]) for d in corp_depts) * gb.NPC_SCALE_FACTOR
        corp_efficiency = 1.0
        if total_corp_staff > caps_limit['office'] and total_corp_staff > 0:
            corp_efficiency = caps_limit['office'] / total_corp_staff
        
        caps['facilities']['office']['usage'] = total_corp_staff
        caps['facilities']['office']['efficiency'] = corp_efficiency
        caps['facilities']['office']['npc_count'] = sum(len(corp_staff[d]) for d in corp_depts)

        # 能力マッピング
        corp_stat_map = {
            gb.DEPT_HR: 'hr',
            gb.DEPT_PR: 'pr',
            gb.DEPT_ACCOUNTING: 'accounting'
        }

        # 共通部門の能力計算
        for dept, stat in corp_stat_map.items():
            staff = corp_staff[dept]
            if staff:
                avg_stat = sum(e[stat] for e in staff) / len(staff)
                sum_stat = sum(e[stat] for e in staff)
                
                # マネジメントボーナス (全社の該当部署マネージャーを探す)
                dept_mgrs = [m for m in managers if m['department'] == dept]
                dept_cxos_list = [c for c in cxos if c['department'] == dept]
                
                manager_mgmt = dept_mgrs[0]['management'] if dept_mgrs else 0
                cxo_mgmt = dept_cxos_list[0]['management'] if dept_cxos_list else 0
                mgmt_bonus = (manager_mgmt * gb.MGMT_BONUS_MANAGER) + (cxo_mgmt * gb.MGMT_BONUS_CXO)
                
                caps[stat] = min(100.0, (avg_stat + mgmt_bonus) * corp_efficiency)
                caps[f"{stat}_capacity"] = sum_stat * corp_efficiency * gb.NPC_SCALE_FACTOR

        # 事業部ごとの能力計算
        div_stat_map = {
            gb.DEPT_PRODUCTION: 'production',
            gb.DEPT_DEV: 'development',
            gb.DEPT_SALES: 'sales',
            gb.DEPT_STORE: 'store_ops'
        }

        for div_id, d_staff in div_staff.items():
            d_caps = caps['divisions'][div_id]
            d_limit = div_caps_limit[div_id]
            industry_key = div_map[div_id]['industry']
            
            # 施設制限チェック
            # 工場
            prod_staff = d_staff[gb.DEPT_PRODUCTION]
            prod_usage = len(prod_staff) * gb.NPC_SCALE_FACTOR
            prod_eff = 1.0
            if prod_usage > d_limit['factory']:
                prod_staff.sort(key=lambda x: x['production'], reverse=True)
                # あふれた分は計算対象外にする
                d_staff[gb.DEPT_PRODUCTION] = prod_staff[:int(d_limit['factory'] // gb.NPC_SCALE_FACTOR)]
            
            # 店舗
            store_staff = d_staff[gb.DEPT_STORE]
            store_usage = len(store_staff) * gb.NPC_SCALE_FACTOR
            if store_usage > d_limit['store']:
                store_staff.sort(key=lambda x: x['store_ops'], reverse=True)
                d_staff[gb.DEPT_STORE] = store_staff[:int(d_limit['store'] // gb.NPC_SCALE_FACTOR)]
            
            # 事業部オフィス (営業・開発)
            div_office_staff = (len(d_staff[gb.DEPT_SALES]) + len(d_staff[gb.DEPT_DEV])) * gb.NPC_SCALE_FACTOR
            div_office_eff = 1.0
            if div_office_staff > d_limit['office'] and div_office_staff > 0:
                div_office_eff = d_limit['office'] / div_office_staff

            # 施設稼働状況の記録 (事業部)
            caps['facilities'][f'factory_{div_id}'] = {
                'name': f"{d_caps['name']} 工場",
                'usage': int(prod_usage),
                'limit': int(d_limit['factory']),
                'efficiency': prod_eff,
                'npc_count': len(prod_staff)
            }
            caps['facilities'][f'store_{div_id}'] = {
                'name': f"{d_caps['name']} 店舗",
                'usage': int(store_usage),
                'limit': int(d_limit['store']),
                'efficiency': 1.0,
                'npc_count': len(store_staff)
            }
            caps['facilities'][f'office_{div_id}'] = {
                'name': f"{d_caps['name']} オフィス",
                'usage': int(div_office_staff),
                'limit': int(d_limit['office']),
                'efficiency': div_office_eff,
                'npc_count': len(d_staff[gb.DEPT_SALES]) + len(d_staff[gb.DEPT_DEV])
            }

            # 計算
            for dept, stat in div_stat_map.items():
                staff = d_staff[dept]
                if staff:
                    eff = div_office_eff if dept in [gb.DEPT_SALES, gb.DEPT_DEV] else 1.0
                    
                    # 業界適性を適用 (能力値 * 適性値)
                    weighted_stats = []
                    for e in staff:
                        apts = json.loads(e['aptitudes']) if e['aptitudes'] else {}
                        apt = apts.get(industry_key, 0.1)
                        weighted_stats.append(e[stat] * apt)

                    avg_stat = sum(weighted_stats) / len(staff)
                    sum_stat = sum(weighted_stats)
                    
                    # 事業部マネージャー
                    dept_mgrs = [m for m in managers if m['department'] == dept and m['division_id'] == div_id]
                    # CxOは全社共通だが、事業部にも影響すると仮定（あるいは事業部担当役員）
                    # ここでは簡易的に全社CxOが全事業部を見る
                    dept_cxos_list = [c for c in cxos if c['department'] == dept]
                    
                    manager_mgmt = dept_mgrs[0]['management'] if dept_mgrs else 0
                    cxo_mgmt = dept_cxos_list[0]['management'] if dept_cxos_list else 0
                    mgmt_bonus = (manager_mgmt * gb.MGMT_BONUS_MANAGER) + (cxo_mgmt * gb.MGMT_BONUS_CXO)
                    
                    d_caps[stat] = min(100.0, (avg_stat + mgmt_bonus) * eff)
                    
                    if stat == 'store_ops':
                        d_caps[f"{stat}_capacity"] = (sum_stat + mgmt_bonus) * eff * gb.NPC_SCALE_FACTOR
                    else:
                        d_caps[f"{stat}_capacity"] = sum_stat * eff * gb.NPC_SCALE_FACTOR
            
            # 店舗スループット
            store_staff_count = len(d_staff[gb.DEPT_STORE])
            d_caps['store_throughput'] = store_staff_count * gb.NPC_SCALE_FACTOR * gb.BASE_SALES_EFFICIENCY * (d_caps['store_ops'] / 50.0)
            
            # 後方互換性のため、全社合計にも加算
            for k in ['production', 'development', 'sales', 'store_ops']:
                # 平均値の加重平均をとるべきだが、簡易的に最大値または合計をとる
                # ここでは合計キャパシティを加算、能力値は最大値を採用
                caps[f"{k}_capacity"] += d_caps[f"{k}_capacity"]
                caps[k] = max(caps[k], d_caps[k])
            caps['store_throughput'] += d_caps['store_throughput']
        
        # 安定性によるデバフ (要件: 低い場合ランダムでデバフ発生)
        # 安定性が50未満の場合、確率で全能力ダウン (日によって調子が悪い)
        if caps['stability'] < 50:
            # 安定性0で最大50%ダウン、安定性50でデバフなし
            max_penalty = 0.5 * (1.0 - (caps['stability'] / 50.0))
            penalty_factor = 1.0 - random.uniform(0, max_penalty)
            
            for key in caps:
                if key != 'stability' and isinstance(caps[key], (int, float)): # 安定性自体は下げない。辞書型(facilities)も除外
                    caps[key] *= penalty_factor

        # --- 仕事量とキャパシティ不足によるペナルティ計算 ---
        # 1. 開発部 (Development)
        # 事業部ごとに計算
        for div_id, d_caps in caps['divisions'].items():
            dev_projects = db.fetch_one("SELECT COUNT(*) as cnt FROM product_designs WHERE company_id = ? AND division_id = ? AND status = 'developing'", (company_id, div_id))
            dev_count = dev_projects['cnt'] if dev_projects else 0
            req_dev = dev_count * gb.REQ_CAPACITY_DEV_PROJECT
            d_caps['requirements']['development'] = req_dev
            
            if req_dev > 0:
                sufficiency = min(1.0, d_caps['development_capacity'] / req_dev)
                d_caps['development'] *= sufficiency
                # 全社値にも反映（簡易）
                caps['requirements']['development'] += req_dev

        # 2. 営業部 (Sales)
        # 事業部ごとに計算
        current_week = self.get_current_week()
        # 統計データは全社合算なので、ここでは簡易的に在庫数から推測するか、全社一括で計算して配分する
        # 今回は全社在庫数と取引数で計算し、各事業部の営業力に一律ペナルティを与える
        prev_stats = db.fetch_one("SELECT b2b_sales, b2c_sales, inventory_count FROM weekly_stats WHERE company_id = ? AND week = ?", (company_id, current_week - 1))
        tx_count = prev_stats['b2b_sales'] if prev_stats else 0
        stock_count = prev_stats['inventory_count'] if prev_stats else 0
        req_sales = (tx_count * gb.REQ_CAPACITY_SALES_TRANSACTION) + (stock_count * gb.REQ_CAPACITY_SALES_STOCK)
        caps['requirements']['sales'] = req_sales
        
        # 全事業部の営業キャパ合計
        total_sales_cap = sum(d['sales_capacity'] for d in caps['divisions'].values())
        if req_sales > 0 and total_sales_cap > 0:
            sufficiency = min(1.0, total_sales_cap / req_sales)
            caps['sales'] *= sufficiency
            for d in caps['divisions'].values():
                d['sales'] *= sufficiency

        # 3. 広報部 (PR)
        # 仕事量: ブランド力 + 全商品認知度
        comp_info = db.fetch_one("SELECT brand_power FROM companies WHERE id = ?", (company_id,))
        brand = comp_info['brand_power'] if comp_info else 0
        
        awareness_res = db.fetch_one("SELECT SUM(awareness) as total FROM product_designs WHERE company_id = ?", (company_id,))
        awareness = awareness_res['total'] if awareness_res and awareness_res['total'] else 0
        
        req_pr = (brand + awareness) * gb.REQ_CAPACITY_PR_POINT
        caps['requirements']['pr'] = req_pr
        
        if req_pr > 0:
            sufficiency = min(1.0, caps['pr_capacity'] / req_pr)
            # ペナルティ: 広報能力低下 -> 広告効果ダウン
            caps['pr'] *= sufficiency
            # 追加ペナルティ: 減衰率悪化は process_advertising で処理するために充足率を保存したいが、
            # ここでは能力値を下げることで間接的に影響させる。
            # さらに process_advertising でキャパシティを再確認して減衰を加速させる。

        # 4. 人事部 (HR)
        # 仕事量: 全従業員数 (NPC数 * SCALE)
        # process_hr のロジック: required = 50 * (scaled_count / 7.0)
        total_employees = len(employees)
        total_employees_scaled = total_employees * gb.NPC_SCALE_FACTOR
        req_hr = 50 * (total_employees_scaled / 7.0)
        caps['requirements']['hr'] = int(req_hr)
        # HRのペナルティは process_hr で忠誠度低下として適用されるため、ここでは能力値を下げない

        # 5. 経理部 (Accounting)
        # 仕事量: 取引数 + 従業員数
        # process_stock_market のロジック参照
        # 取引数は前週の実績を使用 (B2B + B2C)
        b2c_count = prev_stats['b2c_sales'] if prev_stats else 0
        total_tx = tx_count + b2c_count
        req_acc = (total_tx * gb.ACCOUNTING_LOAD_PER_TRANSACTION) + (total_employees * gb.ACCOUNTING_LOAD_PER_EMPLOYEE)
        caps['requirements']['accounting'] = int(req_acc)

        
        return caps

    def proceed_week(self):
      with db.transaction():
        current_week = self.get_current_week()
        print(f"[Week {current_week}] Simulation Start")

        # 0. B2B注文の自動取り下げ (前週以前の未承認注文を期限切れにする)
        # 発注から受注まで1週間の猶予を持たせるため、2週以上前のものを期限切れにする
        db.execute_query("UPDATE b2b_orders SET status = 'expired' WHERE status = 'pending' AND week < ?", (current_week - 1,))

        # 1. NPC意思決定
        # --- パフォーマンス改善: 意思決定に必要なデータを一括で事前取得 ---

        # 全アクティブ企業とNPCを取得
        all_companies = db.fetch_all("SELECT * FROM companies WHERE is_active = 1")
        all_npcs = db.fetch_all("SELECT * FROM npcs WHERE company_id IS NOT NULL")

        # 企業IDをキーにした辞書を作成
        companies_map = {c['id']: c for c in all_companies}
        npcs_by_company = {c['id']: [] for c in all_companies}
        for npc in all_npcs:
            if npc['company_id'] in npcs_by_company:
                npcs_by_company[npc['company_id']].append(npc)

        # 全企業の能力値を一括計算
        all_caps = {}
        for comp in all_companies:
            all_caps[comp['id']] = self.calculate_capabilities(comp['id'], employees=npcs_by_company.get(comp['id'], []))

        # 意思決定で共通して利用する市場データを取得
        economic_index = db.fetch_one("SELECT economic_index FROM game_state")['economic_index']

        # 直近4週間のB2B販売実績 (全社)
        market_b2b_sales_history = db.fetch_all("""
            SELECT seller_id, design_id, SUM(quantity) as total
            FROM transactions
            WHERE week >= ? AND type = 'b2b'
            GROUP BY seller_id, design_id
        """, (current_week - 4,))

        # 市場全体のB2B販売規模（直近4週）
        market_stats_res = db.fetch_one("SELECT SUM(b2b_sales) as total FROM weekly_stats WHERE week >= ?", (current_week - 4,))
        market_total_sales_4w = market_stats_res['total'] if market_stats_res and market_stats_res['total'] else 0

        # 全在庫情報
        all_inventory_res = db.fetch_all("SELECT * FROM inventory")
        inventory_by_company = {c['id']: [] for c in all_companies}
        for inv in all_inventory_res:
            if inv['company_id'] in inventory_by_company:
                inventory_by_company[inv['company_id']].append(dict(inv))

        # 全商品設計書
        all_designs_res = db.fetch_all("SELECT * FROM product_designs")
        designs_by_company = {c['id']: [] for c in all_companies}
        for design in all_designs_res:
            if design['company_id'] in designs_by_company:
                designs_by_company[design['company_id']].append(design)

        # 採用候補者プール
        candidates_pool = db.fetch_all("SELECT * FROM npcs WHERE company_id IS NULL LIMIT 500")

        # B2B注文 (保留中・承認済み未納品)
        # メーカー(Seller)は 'pending' のみを処理対象とする
        # 小売(Buyer)は 'pending' と 'accepted' を発注残としてカウントする
        active_orders_res = db.fetch_all("SELECT * FROM b2b_orders WHERE status IN ('pending', 'accepted')")
        orders_for_seller = {c['id']: [] for c in all_companies}
        orders_for_buyer = {c['id']: [] for c in all_companies}
        for order in active_orders_res:
            if order['status'] == 'pending' and order['seller_id'] in orders_for_seller:
                orders_for_seller[order['seller_id']].append(order)
            if order['buyer_id'] in orders_for_buyer:
                orders_for_buyer[order['buyer_id']].append(order)

        # 市場のメーカー在庫 (小売の仕入れ判断用)
        # 修正: 在庫の所有者(i.company_id)がメーカーまたはプレイヤーであるものを対象とする
        maker_stocks = db.fetch_all("""
            SELECT i.quantity, i.design_id, d.sales_price, d.concept_score, i.company_id as maker_id, c.brand_power
            FROM inventory i
            JOIN product_designs d ON i.design_id = d.id
            JOIN companies c ON i.company_id = c.id
            WHERE c.type IN ('player', 'npc_maker') AND c.is_active = 1 AND i.quantity > 0
        """)

        # --- NPC意思決定ループ ---
        npc_companies = [c for c in all_companies if c['type'].startswith('npc_')]

        for comp in npc_companies:
            company_employees = npcs_by_company.get(comp['id'], [])
            company_designs = designs_by_company.get(comp['id'], [])
            company_inventory = inventory_by_company.get(comp['id'], [])

            logic = NPCLogic(comp['id'], company_data=comp, employees=company_employees)

            # フェーズ更新とリストラ判断 (最初に行う) - 経営状態の確認
            logic.update_phase(current_week)
            logic.decide_restructuring(current_week)

            # 各メソッドに事前取得したデータを渡す
            logic.decide_financing(current_week)
            logic.decide_hiring(current_week, candidates_pool=candidates_pool)
            logic.decide_salary(current_week)
            logic.decide_promotion(current_week)
            
            # 受注処理を先に実行 (在庫を引き当てるため)
            logic.decide_order_fulfillment(
                current_week,
                orders=orders_for_seller.get(comp['id'], []),
                inventory=company_inventory
            )
            
            logic.decide_production(
                current_week,
                designs=company_designs,
                inventory=company_inventory,
                b2b_sales_history=market_b2b_sales_history,
                market_total_sales_4w=market_total_sales_4w,
                economic_index=economic_index
            )
            logic.decide_procurement(
                current_week,
                maker_stocks=maker_stocks,
                my_capabilities=all_caps.get(comp['id']),
                all_capabilities=all_caps,
                my_inventory=company_inventory,
                on_order=orders_for_buyer.get(comp['id'], [])
            )
            logic.decide_development(current_week, designs=company_designs)
            logic.decide_facilities(current_week)
            logic.decide_advertising(current_week)
            logic.decide_stock_action(current_week)
            logic.decide_pricing(
                current_week,
                designs=all_designs_res,
                inventory=company_inventory,
                b2b_sales_history=market_b2b_sales_history
            )
        print(f"[Week {current_week}] Phase 1: NPC Decisions Finished")

        # 2. 能力確定 (各フェーズで calculate_capabilities を呼び出して使用)

        # 3. B2B取引 (受注分の納品処理)
        self.process_b2b(current_week)
        print(f"[Week {current_week}] Phase 3: B2B Processing Finished")

        # 4. B2C取引 (需要と供給のマッチング)
        self.process_b2c(current_week)
        print(f"[Week {current_week}] Phase 4: B2C Processing Finished")

        # 5. 人事処理 (成長、給与支払い)
        self.process_hr(current_week)
        print(f"[Week {current_week}] Phase 5: HR Processing Finished")

        # 6. 開発進捗処理
        self.process_development(current_week)
        print(f"[Week {current_week}] Phase 6: Development Processing Finished")

        # 6. 製品陳腐化処理
        self.process_product_obsolescence(current_week)
        print(f"[Week {current_week}] Phase 6: Product Obsolescence Finished")

        # 6. 加齢・引退処理
        self.process_aging(current_week)

        # 6. 広告効果減衰
        self.process_advertising(current_week, all_caps)

        # 6. その他 (固定費支払い)
        self.process_financials(current_week, all_caps)
        print(f"[Week {current_week}] Phase 6+: Misc Processing Finished")

        # 7. 銀行処理 (金利、格付け更新)
        self.process_banking(current_week)
        print(f"[Week {current_week}] Phase 7: Banking Processing Finished")

        # 8. 倒産判定
        self.check_bankruptcy(current_week)
        print(f"[Week {current_week}] Phase 8: Bankruptcy Check Finished")
        
        # 9. 株式市場・決算処理
        self.process_stock_market(current_week, all_caps)
        print(f"[Week {current_week}] Phase 9: Stock Market Processing Finished")

        # 7. 週更新
        new_week = current_week + 1
        economic_index = 1.0 + random.uniform(-0.05, 0.05) # ランダム変動
        db.execute_query("UPDATE game_state SET week = ?, economic_index = ?", (new_week, economic_index))
        
        # 週次統計のスナップショット保存 (在庫数、施設サイズ)
        active_companies = db.fetch_all("SELECT id, funds FROM companies WHERE is_active = 1")
        for comp in active_companies:
            cid = comp['id']
            # 在庫数
            inv = db.fetch_one("SELECT SUM(quantity) as qty FROM inventory WHERE company_id = ?", (cid,))
            qty = inv['qty'] if inv and inv['qty'] else 0
            db.set_weekly_stat(current_week, cid, 'inventory_count', qty)
            
            # 施設サイズ
            fac = db.fetch_one("SELECT SUM(size) as sz FROM facilities WHERE company_id = ?", (cid,))
            sz = fac['sz'] if fac and fac['sz'] else 0
            db.set_weekly_stat(current_week, cid, 'facility_size', sz)
            
            # 借入残高
            loan = db.fetch_one("SELECT SUM(amount) as total FROM loans WHERE company_id = ?", (cid,))
            balance = loan['total'] if loan and loan['total'] else 0
            db.set_weekly_stat(current_week, cid, 'loan_balance', balance)
            
            # 現金残高
            db.set_weekly_stat(current_week, cid, 'funds', comp['funds'])

        # 財務フロー集計 (Revenue, Expenses, Labor, Facility)
        # account_entriesから集計
        financials = db.fetch_all("""
            SELECT company_id, category, SUM(amount) as total 
            FROM account_entries 
            WHERE week = ? 
            GROUP BY company_id, category
        """, (current_week,))
        
        comp_fin = {}
        for f in financials:
            cid = f['company_id']
            if cid not in comp_fin: comp_fin[cid] = {'revenue': 0, 'expenses': 0, 'labor': 0, 'facility': 0}
            
            cat = f['category']
            amt = f['total']
            
            if cat == 'revenue':
                comp_fin[cid]['revenue'] += amt
            elif cat != 'cogs': # COGSは非現金支出(会計上の費用)なのでキャッシュフローとしての支出からは除外
                comp_fin[cid]['expenses'] += amt
                
            if 'labor' in cat:
                comp_fin[cid]['labor'] += amt
            if 'rent' in cat or cat == 'facility_purchase':
                comp_fin[cid]['facility'] += amt

        for cid, data in comp_fin.items():
            db.set_weekly_stat(current_week, cid, 'total_revenue', data['revenue'])
            db.set_weekly_stat(current_week, cid, 'total_expenses', data['expenses'])
            db.set_weekly_stat(current_week, cid, 'labor_costs', data['labor'])
            db.set_weekly_stat(current_week, cid, 'facility_costs', data['facility'])

        print(f"[Week {current_week}] Simulation End")
        return new_week

    def process_b2b(self, week):
        """
        B2B取引処理: ステータスが 'accepted' の注文を処理し、在庫と資金を移動させる
        """
        accepted_orders = db.fetch_all("SELECT * FROM b2b_orders WHERE status = 'accepted'")
        
        if not accepted_orders:
            return

        b2b_sales_counts = {} # {seller_id: count}
        with db.transaction() as conn:
            cursor = conn.cursor()
            for order in accepted_orders:
                # 在庫確認 (念のため) - トランザクション内での読み取りはcursorを使用
                cursor.execute("SELECT quantity FROM inventory WHERE company_id = ? AND design_id = ?", 
                               (order['seller_id'], order['design_id']))
                stock = cursor.fetchone()
                
                if stock and stock['quantity'] >= order['quantity']:
                    # 1. メーカー在庫減
                    cursor.execute("UPDATE inventory SET quantity = quantity - ? WHERE company_id = ? AND design_id = ?", 
                                   (order['quantity'], order['seller_id'], order['design_id']))
                    
                    # 2. 小売在庫増 (なければ作成)
                    cursor.execute("SELECT id FROM inventory WHERE company_id = ? AND design_id = ?", 
                                   (order['buyer_id'], order['design_id']))
                    buyer_stock = cursor.fetchone()
                    
                    # 小売在庫の販売価格は、メーカーのMSRP (product_designs.sales_price) を初期値とする
                    # order['design_id'] から MSRP を取得する必要があるが、ここでは簡易的に
                    # メーカー在庫の sales_price (もしあれば) か、別途取得が必要。
                    # 効率のため、b2b_orders作成時にMSRPをスナップショットするか、ここでJOINして取得する。
                    # ここでは、inventoryテーブル更新時にMSRPを取得してセットする。
                    cursor.execute("SELECT sales_price, parts_config FROM product_designs WHERE id = ?", (order['design_id'],))
                    design_info = cursor.fetchone()
                    msrp = design_info['sales_price'] if design_info else 0

                    if buyer_stock:
                        cursor.execute("UPDATE inventory SET quantity = quantity + ? WHERE id = ?", 
                                       (order['quantity'], buyer_stock['id']))
                    else:
                        cursor.execute("INSERT INTO inventory (company_id, design_id, quantity, sales_price) VALUES (?, ?, ?, ?)", 
                                       (order['buyer_id'], order['design_id'], order['quantity'], msrp))
                    
                    # 3. 資金移動
                    cursor.execute("UPDATE companies SET funds = funds + ? WHERE id = ?", (order['amount'], order['seller_id']))
                    cursor.execute("UPDATE companies SET funds = funds - ? WHERE id = ?", (order['amount'], order['buyer_id']))

                    # 4. 会計ログ
                    cursor.execute("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, 'stock_purchase', ?)",
                                   (week, order['buyer_id'], order['amount']))
                    cursor.execute("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, 'revenue', ?)",
                                   (week, order['seller_id'], order['amount']))
                    
                    # メーカー原価計算 (材料費ベース)
                    unit_material_cost = 0
                    if design_info and design_info['parts_config']:
                        p_conf = json.loads(design_info['parts_config'])
                        unit_material_cost = sum(p['cost'] for p in p_conf.values())
                    
                    maker_cogs = unit_material_cost * order['quantity']
                    cursor.execute("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, 'cogs', ?)",
                                   (week, order['seller_id'], maker_cogs))
                    
                    # 5. 取引履歴 (Transactions)
                    cursor.execute("INSERT INTO transactions (week, type, buyer_id, seller_id, design_id, quantity, amount) VALUES (?, 'b2b', ?, ?, ?, ?, ?)",
                                   (week, order['buyer_id'], order['seller_id'], order['design_id'], order['quantity'], order['amount']))

                    # 6. ステータス更新
                    cursor.execute("UPDATE b2b_orders SET status = 'completed' WHERE id = ?", (order['id'],))
                    
                    # ログは別途書き込むか、ここでもcursorを使う（log_newsはdb.execute_queryを使うので注意）
                    # ここでは簡易的に直接INSERT
                    cursor.execute("INSERT INTO news_logs (week, company_id, message, type) VALUES (?, ?, ?, ?)",
                                   (week, order['buyer_id'], f"発注ID {order['id']} が納品されました。", 'info'))
                    
                    b2b_sales_counts[order['seller_id']] = b2b_sales_counts.get(order['seller_id'], 0) + order['quantity']
                    
                    # ファイルログ (トランザクション外で実行するか、ここで実行するか。ファイル書き込みはDBトランザクションと無関係なのでここでOK)
                    # ただしdb.log_file_eventは内部でSELECTを行うため、トランザクション中のconnを使わないとロックする可能性があるが、
                    # SQLiteのデフォルト設定では読み取りはブロックされないことが多い。
                    # 安全のため、必要な情報はここで取得して後でログ出力するか、単純な文字列構築にする。
                    # ここでは簡易的に、db.log_file_eventを使わず直接ファイルに書くか、db.log_file_eventが別コネクションを使うことを許容する。
                    # db.log_file_eventは get_connection() で新しい接続を作るので、WALモードでないとロックするかも。
                    # 今回はシンプルに、ループ後にまとめてログ出力する形はとらず、都度出力するが、
                    # ロック回避のため、必要な情報を集めておいてトランザクション後にログ出力する。
            
            # トランザクション終了後にログ出力
            for order in accepted_orders:
                db.log_file_event(week, order['buyer_id'], "B2B Delivery", f"Received {order['quantity']} units (Order ID: {order['id']})")
                db.log_file_event(week, order['seller_id'], "B2B Shipment", f"Shipped {order['quantity']} units (Order ID: {order['id']})")
        
        # 統計更新
        for seller_id, count in b2b_sales_counts.items():
            db.increment_weekly_stat(week, seller_id, 'b2b_sales', count)

    def process_b2c(self, week):
        # カテゴリごとの需要計算とマッチング
        economic_index = db.fetch_one("SELECT economic_index FROM game_state")['economic_index']
        
        # 全カテゴリの需要を計算
        categories = []
        for ind_key, ind_val in gb.INDUSTRIES.items():
            for cat_key, cat_val in ind_val['categories'].items():
                base = cat_val['base_demand']
                demand = int(base * economic_index * random.uniform(0.95, 1.05))
                categories.append({'key': cat_key, 'demand': demand})
                db.execute_query("INSERT INTO market_trends (week, category_key, b2c_demand) VALUES (?, ?, ?)", (week, cat_key, demand))
        
        # 前週のB2C販売数取得 (トレンド/バンドワゴン効果用)
        prev_b2c_sales = db.fetch_all("SELECT design_id, SUM(quantity) as total FROM transactions WHERE week = ? AND type = 'b2c' GROUP BY design_id", (week - 1,))
        prev_sales_map = {r['design_id']: r['total'] for r in prev_b2c_sales}

        # 小売在庫の取得
        retail_stocks = db.fetch_all("""
            SELECT i.id, i.company_id, i.division_id, i.quantity, i.sales_price as retail_price, i.design_id, d.name as product_name,
                   d.concept_score, d.base_price, d.sales_price as msrp, d.awareness, d.material_score, d.parts_config, d.category_key,
                   c.brand_power as retail_brand, c.type as company_type,
                   m.brand_power as maker_brand, m.id as creator_id
            FROM inventory i
            JOIN product_designs d ON i.design_id = d.id
            JOIN companies c ON i.company_id = c.id
            JOIN companies m ON d.company_id = m.id
            WHERE c.type IN ('player', 'npc_retail') AND c.is_active = 1 AND i.quantity > 0
        """)

        if not retail_stocks:
            return

        # 企業ごとの能力キャッシュ
        comp_caps = {}
        
        # カテゴリごとに処理
        for cat in categories:
            cat_key = cat['key']
            market_demand = cat['demand']
            
            # このカテゴリの在庫のみ抽出
            cat_stocks = [s for s in retail_stocks if s['category_key'] == cat_key]
            if not cat_stocks: continue

            # スコアリング
            scored_stocks = []
            total_score = 0
            for stock in cat_stocks:
                cid = stock['company_id']
                if cid not in comp_caps:
                    comp_caps[cid] = self.calculate_capabilities(cid)

                # 事業部ごとの店舗運営力を使用すべきだが、小売は通常1事業部または全社共通で売る
                # ここでは全社合計の store_ops を使用
                store_ops = comp_caps[cid]['store_ops']
                store_score = (1 + stock['retail_brand'] / 100.0) * (1 + store_ops / 100.0)

                base_price = stock['base_price']
                retail_price = stock['retail_price']
                price_ratio = retail_price / base_price if base_price > 0 else 1.0
                price_factor = price_ratio ** 2
                
                trend_factor = random.uniform(0.8, 1.2)
                prev_sold = prev_sales_map.get(stock['design_id'], 0)
                bandwagon_bonus = 1.0 + (math.log1p(prev_sold) * 0.15)
                preference_noise = random.gauss(1.0, 0.15)
                
                product_score = (stock['concept_score'] * stock['material_score'] * 
                                (1 + stock['maker_brand'] / 100.0) * (1 + stock['awareness'] / 100.0)) / price_factor
                
                final_score = store_score * product_score * trend_factor * bandwagon_bonus * preference_noise
                scored_stocks.append({**stock, 'score': final_score})
                total_score += final_score
            
            # 需要分配
            sales_record = {s['id']: 0 for s in scored_stocks}
            remaining_demand = market_demand
            
            for _ in range(3):
                if remaining_demand <= 0: break
                
                active_stocks = [s for s in scored_stocks if (s['quantity'] - sales_record[s['id']]) > 0 and comp_caps[s['company_id']]['store_throughput'] > 0]
                if not active_stocks: break
                
                current_total_score = sum(s['score'] for s in active_stocks)
                if current_total_score == 0: break
                
                round_demand = remaining_demand
                remaining_demand = 0
                
                for stock in active_stocks:
                    share = stock['score'] / current_total_score
                    float_demand = round_demand * share
                    demand = int(float_demand)
                    if random.random() < (float_demand - demand): demand += 1
                    
                    current_qty = stock['quantity'] - sales_record[stock['id']]
                    cap_float = comp_caps[stock['company_id']]['store_throughput']
                    capacity = int(cap_float)
                    if random.random() < (cap_float - capacity): capacity += 1
                    
                    sold = min(demand, current_qty, capacity)
                    sold = int(sold)
                    
                    if sold > 0:
                        sales_record[stock['id']] += sold
                        comp_caps[stock['company_id']]['store_throughput'] -= sold
                    
                    if demand > sold:
                        remaining_demand += (demand - sold)
            
            # DB更新用リストに追加 (カテゴリごとの結果を統合)
            # (ループ外で定義したリストに追加していく処理は元のコードと同じ構造にするため省略し、
            #  元のコードの update_inventory 等のリスト構築部分をこのループ内で行うか、
            #  sales_record を全カテゴリ分統合してから一括処理する)
            # ここでは sales_record を全カテゴリ分マージする形をとる
            if 'all_sales_record' not in locals(): all_sales_record = {}
            all_sales_record.update(sales_record)
            if 'all_scored_stocks' not in locals(): all_scored_stocks = []
            all_scored_stocks.extend(scored_stocks)

        # DB更新とログ記録
        # executemany用にデータを準備
        update_inventory = []
        update_funds = []
        insert_transactions = []
        insert_revenue = []
        insert_cogs = []
        b2c_sales_counts = {} # {company_id: count}

        for stock in all_scored_stocks:
            sold = all_sales_record.get(stock['id'], 0)
            if sold > 0:
                # ★バグ修正: 収益と原価を正しく計上する
                # 収益: 販売数 * 小売価格
                revenue = sold * stock['retail_price']
                
                # 売上原価(COGS)の計算
                if stock['company_id'] == stock['creator_id']:
                    # 自社製造 (Maker/Player as Maker) の場合: 原価は材料費
                    p_conf = json.loads(stock['parts_config']) if stock['parts_config'] else {}
                    unit_cost = sum(p['cost'] for p in p_conf.values()) if p_conf else 0
                    cogs = sold * unit_cost
                else:
                    # 小売販売の場合: 原価は仕入れ値 (MSRPの90%と仮定)
                    cogs = int(sold * stock['msrp'] * 0.9)

                update_inventory.append((sold, stock['id']))
                update_funds.append((revenue, stock['company_id']))
                insert_transactions.append((week, 'b2c', stock['company_id'], stock['design_id'], sold, revenue))
                insert_revenue.append((week, stock['company_id'], 'revenue', revenue))
                insert_cogs.append((week, stock['company_id'], 'cogs', cogs))
                b2c_sales_counts[stock['company_id']] = b2c_sales_counts.get(stock['company_id'], 0) + sold

        with db.transaction() as conn:
            cursor = conn.cursor()
            if update_inventory:
                cursor.executemany("UPDATE inventory SET quantity = quantity - ? WHERE id = ?", update_inventory)
                cursor.executemany("UPDATE companies SET funds = funds + ? WHERE id = ?", update_funds)
                cursor.executemany("INSERT INTO transactions (week, type, seller_id, design_id, quantity, amount) VALUES (?, ?, ?, ?, ?, ?)", insert_transactions)
                cursor.executemany("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, ?, ?)", insert_revenue)
                cursor.executemany("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, ?, ?)", insert_cogs)
        
        # ログ出力
        for stock in scored_stocks:
            sold = sales_record[stock['id']]
            if sold > 0:
                db.log_file_event(week, stock['company_id'], "Retail Sales", f"Sold {sold} units of {stock['product_name']}")
        
        # 統計更新
        for cid, count in b2c_sales_counts.items():
            db.increment_weekly_stat(week, cid, 'b2c_sales', count)

    def process_hr(self, week):
        # 0. 採用オファーの処理 (受諾判定)
        offers = db.fetch_all("SELECT * FROM job_offers WHERE week = ?", (week,))
        
        # NPCごとにオファーをまとめる
        npc_offers = {}
        for offer in offers:
            nid = offer['npc_id']
            if nid not in npc_offers: npc_offers[nid] = []
            npc_offers[nid].append(offer)
        
        hired_counts = {} # {company_id: count}
        with db.transaction() as conn:
            cursor = conn.cursor()
            for nid, offer_list in npc_offers.items():
                cursor.execute("SELECT * FROM npcs WHERE id = ?", (nid,))
                npc = cursor.fetchone()
                if not npc or npc['company_id'] is not None:
                    # 既に就職済みならオファー無効
                    continue
                
                # 最も条件の良いオファーを選ぶ
                # 基準: 給与 + 企業ブランド力ボーナス
                best_offer = None
                best_val = -1
                
                for offer in offer_list:
                    cursor.execute("SELECT brand_power FROM companies WHERE id = ?", (offer['company_id'],))
                    comp = cursor.fetchone()
                    brand = comp['brand_power'] if comp else 0
                    
                    # 評価値 = 提示給与 * (1 + ブランド/200)
                    val = offer['offer_salary'] * (1 + brand / 200.0)
                    if val > best_val:
                        best_val = val
                        best_offer = offer
                
                # 受諾判定
                # 希望給与を満たしていればほぼ受諾
                threshold = npc['desired_salary'] if npc['desired_salary'] > 0 else gb.BASE_SALARY_YEARLY
                if best_offer['offer_salary'] >= threshold * 0.9: # 9割以上なら妥協して受諾
                    # 採用成立
                    # オファー時のターゲット部署に配属する
                    target_dept = best_offer['target_dept']
                    if not target_dept: target_dept = gb.DEPT_PRODUCTION # フォールバック

                    # 事業部IDの決定 (直接部門の場合は最初の事業部に割り当てる)
                    division_id = None
                    if target_dept in [gb.DEPT_PRODUCTION, gb.DEPT_SALES, gb.DEPT_DEV, gb.DEPT_STORE]:
                        # 簡易的に最初の事業部を取得
                        div = db.fetch_one("SELECT id FROM divisions WHERE company_id = ? LIMIT 1", (best_offer['company_id'],))
                        if div:
                            division_id = div['id']

                    cursor.execute("UPDATE npcs SET company_id = ?, division_id = ?, department = ?, role = ?, salary = ?, desired_salary = ?, loyalty = 50 WHERE id = ?",
                                   (best_offer['company_id'], division_id, target_dept, gb.ROLE_MEMBER, best_offer['offer_salary'], best_offer['offer_salary'], nid))
                    
                    cursor.execute("INSERT INTO news_logs (week, company_id, message, type) VALUES (?, ?, ?, ?)",
                                   (week, best_offer['company_id'], f"{npc['name']} を採用しました (年収: ¥{best_offer['offer_salary']:,})", 'info'))
                    
                    cid = best_offer['company_id']
                    hired_counts[cid] = hired_counts.get(cid, 0) + 1

        for cid, count in hired_counts.items():
            db.increment_weekly_stat(week, cid, 'hired_count', count)

        # オファーテーブルのクリーンアップ (今週分は処理済み)
        db.execute_query("DELETE FROM job_offers WHERE week <= ?", (week,))

        # 企業ごとの処理 (忠誠度、成長、給与)
        companies = db.fetch_all("SELECT id, type FROM companies WHERE is_active = 1")
        
        # 事業部情報のキャッシュ (ID -> Industry)
        all_divisions = db.fetch_all("SELECT id, industry_key FROM divisions")
        div_industry_map = {d['id']: d['industry_key'] for d in all_divisions}

        for comp in companies:
            if comp['type'] == 'system_supplier': continue

            # 従業員取得
            employees = db.fetch_all("SELECT * FROM npcs WHERE company_id = ?", (comp['id'],))
            if not employees: continue
            
            # HR能力計算
            caps = self.calculate_capabilities(comp['id'], employees=employees)
            
            # 供給キャパシティ: 人事部員の能力合計(スケール済み) + 経営者分の基礎キャパシティ
            # 経営者分として、能力50のNPC1人分(スケール済み)を常に加算する（小規模組織の救済）
            base_hr_capacity = 50 * gb.NPC_SCALE_FACTOR
            hr_power_sum = caps['hr_capacity'] + base_hr_capacity

            # 必要HRキャパシティ: 50 * (全従業員数(実数) / 7)
            # つまり、人事能力50の担当者1人で7人(実数)を見れる計算
            required_capacity = 50 * ((len(employees) * gb.NPC_SCALE_FACTOR) / 7.0)
            
            # 忠誠度変化
            loyalty_delta = 0
            if hr_power_sum >= required_capacity:
                loyalty_delta = 1 # 充足
            else:
                # 不足 (最大-5)
                if required_capacity > 0:
                    ratio = hr_power_sum / required_capacity
                    loyalty_delta = -5 * (1.0 - ratio)
            
            updates_to_run = []
            labor_costs = {}

            # 部署と給与カテゴリのマッピング
            dept_labor_map = {
                gb.DEPT_PRODUCTION: 'labor_production',
                gb.DEPT_STORE: 'labor_store',
                gb.DEPT_SALES: 'labor_sales',
                gb.DEPT_DEV: 'labor_dev',
                gb.DEPT_HR: 'labor_hr',
                gb.DEPT_PR: 'labor_pr',
                gb.DEPT_ACCOUNTING: 'labor_accounting'
            }

            for npc in employees:
                # 個別の忠誠度変動要因
                individual_loyalty_delta = loyalty_delta
                
                # 給与不満による忠誠度低下
                if npc['desired_salary'] > npc['salary']:
                    gap = (npc['desired_salary'] - npc['salary']) / npc['desired_salary']
                    if gap > 0.05: # 5%以上の乖離で不満発生
                        individual_loyalty_delta -= int(gap * 10) # 乖離が大きいほど下がる

                # 1. 忠誠度更新
                new_loyalty = max(0, min(100, npc['loyalty'] + individual_loyalty_delta))
                
                # 2. 能力成長
                # 基本成長率: 0.025 * 2^(Adaptability/50)
                base_growth = 0.025 * (2 ** (npc['adaptability'] / 50.0))
                
                changes = {}

                # 部署ボーナス
                dept = npc['department']
                stat_map = {
                    gb.DEPT_PRODUCTION: 'production', gb.DEPT_SALES: 'sales', gb.DEPT_DEV: 'development',
                    gb.DEPT_HR: 'hr', gb.DEPT_PR: 'pr', gb.DEPT_ACCOUNTING: 'accounting', gb.DEPT_STORE: 'store_ops'
                }
                target_stat = stat_map.get(dept)
                
                if target_stat:
                    changes[target_stat] = min(gb.ABILITY_MAX, npc[target_stat] + base_growth)
                
                # マネジメント (部長補佐以上)
                if npc['role'] in [gb.ROLE_ASSISTANT_MANAGER, gb.ROLE_MANAGER, gb.ROLE_CXO, gb.ROLE_CEO]:
                     changes['management'] = min(gb.ABILITY_MAX, npc['management'] + base_growth)
                     
                # 役員適正 (部長以上)
                if npc['role'] in [gb.ROLE_MANAGER, gb.ROLE_CXO, gb.ROLE_CEO]:
                    # 適応力50なら0.1 -> base_growth * 2
                    changes['executive'] = min(gb.ABILITY_MAX, npc['executive'] + (base_growth * 2))
                
                # 業界適性 (所属している事業部の業界のみ成長)
                if npc['division_id'] and npc['division_id'] in div_industry_map:
                    ind_key = div_industry_map[npc['division_id']]
                    apts = json.loads(npc['aptitudes']) if npc['aptitudes'] else {}
                    current_apt = apts.get(ind_key, 0.1)
                    
                    if current_apt < 2.0:
                        speed_factor = npc['adaptability'] / 50.0
                        apt_growth = (0.9 / 13.0) * speed_factor if current_apt < 1.0 else (1.0 / 260.0) * speed_factor
                        apts[ind_key] = min(2.0, current_apt + apt_growth)
                        changes['aptitudes'] = json.dumps(apts)

                # 3. 希望給与の更新 (年に1回)
                if week % 52 == 0:
                    # 能力に基づく適正給与を計算
                    max_stat = max(
                        npc['production'], npc['sales'], npc['development'], 
                        npc['hr'], npc['pr'], npc['accounting'], npc['store_ops']
                    )
                    base_req = int(gb.BASE_SALARY_YEARLY * (max_stat / 50.0))
                    
                    # 多少の揺らぎを持たせて希望給与を設定 (0.95 ~ 1.1倍)
                    new_desired = int(base_req * random.uniform(0.95, 1.1))
                    changes['desired_salary'] = new_desired
                    
                    if new_desired > npc['salary'] * 1.1:
                        self.log_news(week, comp['id'], f"{npc['name']} が昇給を希望しています (希望: ¥{new_desired:,})", 'info')

                # 4. 離職判定 (Turnover)
                # 忠誠度が40を下回ると離職リスク発生
                if new_loyalty < 40:
                    # 忠誠度 0 で 20%、40 で 0% の確率
                    resign_prob = (40 - new_loyalty) * 0.005
                    if random.random() < resign_prob:
                        # 離職実行 (会社ID等をNULLにして労働市場へ戻す)
                        changes.update({
                            "company_id": None, "department": None, "role": None, "loyalty": 50,
                            "last_resigned_week": week, "last_company_id": comp['id']
                        })
                        self.log_news(week, comp['id'], f"従業員 {npc['name']} が退職しました。", 'warning')
                        db.log_file_event(week, comp['id'], "HR Resignation", f"{npc['name']} resigned")

                changes['loyalty'] = new_loyalty
                
                if changes:
                    set_clause = ", ".join(f"{k} = ?" for k in changes.keys())
                    params = list(changes.values()) + [npc['id']]
                    updates_to_run.append((f"UPDATE npcs SET {set_clause} WHERE id = ?", tuple(params)))

                # 3. 給与支払い
                weekly_salary = int((npc['salary'] * gb.NPC_SCALE_FACTOR) / gb.WEEKS_PER_YEAR_REAL)
                
                cat = dept_labor_map.get(npc['department'], 'labor')
                
                labor_costs[cat] = labor_costs.get(cat, 0) + weekly_salary

            total_salary_deduction = sum(labor_costs.values())
            if total_salary_deduction > 0:
                updates_to_run.append(("UPDATE companies SET funds = funds - ? WHERE id = ?", (total_salary_deduction, comp['id'])))
                for cat, amount in labor_costs.items():
                    if amount > 0:
                        updates_to_run.append(("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, ?, ?)", (week, comp['id'], cat, amount)))

            if updates_to_run:
                with db.transaction() as conn:
                    cursor = conn.cursor()
                    for sql, p in updates_to_run:
                        cursor.execute(sql, p)

    def process_aging(self, week):
        # 13週で1歳
        if week % gb.WEEKS_PER_AGE == 0:
            db.execute_query("UPDATE npcs SET age = age + 1")
            
            # 定年 (66歳) -> 引退処理
            retirees = db.fetch_all("SELECT id FROM npcs WHERE age >= ?", (gb.RETIREMENT_AGE,))
            if retirees:
                for r in retirees:
                    db.execute_query("DELETE FROM npcs WHERE id = ?", (r['id'],))
                    
                    # 補充
                    new_npc = generate_random_npc(age=22)
                    keys = new_npc.keys()
                    placeholders = ','.join(['?'] * len(keys))
                    columns = ','.join(keys)
                    values = tuple(new_npc.values())
                    db.execute_query(f"INSERT INTO npcs ({columns}) VALUES ({placeholders})", values)

    def process_financials(self, week, all_caps=None):
        # 施設賃料支払い
        facilities = db.fetch_all("SELECT * FROM facilities WHERE is_owned = 0")
        if facilities:
            with db.transaction() as conn:
                cursor = conn.cursor()
                for fac in facilities:
                    cat = 'rent'
                    if fac['type'] == 'factory': cat = 'rent_factory'
                    elif fac['type'] == 'store': cat = 'rent_store'
                    elif fac['type'] == 'office': cat = 'rent_office'

                    cursor.execute("UPDATE companies SET funds = funds - ? WHERE id = ?", (fac['rent'], fac['company_id']))
                    cursor.execute("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, ?, ?)",
                                   (week, fac['company_id'], cat, fac['rent']))

    def process_advertising(self, week, all_caps=None):
        """
        ブランド力と商品認知度の自然減衰 (広報能力依存)
        """
        companies = db.fetch_all("SELECT id, type FROM companies WHERE is_active = 1")
        
        with db.transaction() as conn:
            cursor = conn.cursor()
            for comp in companies:
                if comp['type'] == 'system_supplier': continue
                
                cid = comp['id']
                pr_power = 0
                if all_caps and cid in all_caps:
                    pr_power = all_caps[cid].get('pr', 0)
                
                # キャパシティ不足チェック
                # calculate_capabilities で計算済みの値を使いたいが、all_capsには能力値しか入っていない場合があるため再計算はコストが高い。
                # 簡易的にここで再取得するか、all_capsの構造に依存する。
                # all_caps は calculate_capabilities の戻り値そのものなので、requirements も入っているはず。
                caps_data = all_caps.get(cid, {})
                pr_cap = caps_data.get('pr_capacity', 0)
                req_pr = caps_data.get('requirements', {}).get('pr', 0)
                
                sufficiency = 1.0
                if req_pr > 0:
                    sufficiency = min(1.0, pr_cap / req_pr)

                # 減衰率の計算: 基本値 + (能力による緩和)
                # キャパシティ不足の場合、緩和効果が消えるだけでなく、基本減衰率自体が悪化するペナルティ
                penalty_decay = 0.05 * (1.0 - sufficiency) # 最大5%追加減衰
                
                brand_decay = min(1.0, gb.BRAND_DECAY_BASE + (pr_power * gb.PR_MITIGATION_FACTOR))
                awareness_decay = min(1.0, gb.AWARENESS_DECAY_BASE + (pr_power * gb.PR_MITIGATION_FACTOR))
                
                # ペナルティ適用
                brand_decay -= penalty_decay
                awareness_decay -= penalty_decay
                
                cursor.execute("UPDATE companies SET brand_power = brand_power * ? WHERE id = ?", (brand_decay, cid))
                cursor.execute("UPDATE product_designs SET awareness = awareness * ? WHERE company_id = ?", (awareness_decay, cid))

    def process_development(self, week):
        """
        開発中のプロジェクトを進捗させ、完了時にステータスを確定する
        """
        developing_projects = db.fetch_all("SELECT * FROM product_designs WHERE status = 'developing'")
        
        for proj in developing_projects:
            start_week = proj['developed_week']
            company_id = proj['company_id']
            
            # 開発キャパシティチェック
            # 毎回計算するのは重いが、週次処理なので許容
            caps = self.calculate_capabilities(company_id)
            dev_cap = caps['development_capacity']
            req_dev = caps['requirements']['development'] # calculate_capabilities内で計算済み
            
            # 充足率
            sufficiency = 1.0
            if req_dev > 0:
                sufficiency = min(1.0, dev_cap / req_dev)
            
            # キャパシティ不足の場合、開始週を後ろ倒しにして期間を延長する
            # 充足率 0.5 なら、1週間進むところを 0.5週間しか進まない -> start_week を 0.5 増やす
            # 整数管理のため、確率的に +1 する
            delay_prob = 1.0 - sufficiency
            if random.random() < delay_prob:
                # 遅延発生
                db.execute_query("UPDATE product_designs SET developed_week = developed_week + 1 WHERE id = ?", (proj['id'],))
                # ログは出しすぎるとうるさいので、著しい遅延の場合のみ出すなどの調整が必要だが今回は割愛
            
            # 完了判定 (現在週 - 開始週 >= 期間)
            # 遅延により start_week が増えているため、完了が遅れる
            current_start_week = start_week # DB更新前または更新後の値を取得すべきだが、ここでは簡易的に判定
            # 正確には再取得が必要
            updated_proj = db.fetch_one("SELECT developed_week FROM product_designs WHERE id = ?", (proj['id'],))
            if updated_proj:
                current_start_week = updated_proj['developed_week']

            # 開発期間の取得 (カテゴリ依存)
            duration = 26 # Default fallback
            if proj['division_id'] and proj['category_key']:
                div = db.fetch_one("SELECT industry_key FROM divisions WHERE id = ?", (proj['division_id'],))
                if div:
                    ind_key = div['industry_key']
                    cat_key = proj['category_key']
                    if ind_key in gb.INDUSTRIES and cat_key in gb.INDUSTRIES[ind_key]['categories']:
                        duration = gb.INDUSTRIES[ind_key]['categories'][cat_key]['development_duration']

            if week - current_start_week >= duration:
                # 開発完了処理
                
                # 企業の開発力を計算
                # caps は上で計算済み
                company_data = db.fetch_one("SELECT dev_knowhow FROM companies WHERE id = ?", (company_id,))
                total_dev_power = caps['development']
                if total_dev_power == 0: total_dev_power = 20 # 最低保証

                # 開発方針による補正
                strategy = proj['strategy']
                strat_mods = gb.DEV_STRATEGIES.get(strategy, gb.DEV_STRATEGIES[gb.DEV_STRATEGY_BALANCED])
                
                # ステータス確定
                # 基準値: Concept 3.0, Efficiency 1.0
                # 開発力が高いと、そこから上振れする
                # 開発ノウハウによるボーナス
                knowhow_bonus = company_data['dev_knowhow'] * gb.DEV_KNOWHOW_EFFECT if company_data else 0

                quality_bonus = (total_dev_power - 40) / 100.0 # 40を基準に±
                
                # 開発の揺らぎ (Innovation/Bug): 予期せぬ成功や失敗
                # 正規分布で自然なバラつきを持たせる
                innovation_luck = random.gauss(0, 0.3)
                efficiency_luck = random.gauss(0, 0.15)
                
                base_concept = 3.0 * strat_mods['c_mod']
                base_efficiency = 1.0 * strat_mods['e_mod']
                
                final_concept = min(5.0, max(1.0, base_concept + quality_bonus + knowhow_bonus + innovation_luck))
                final_efficiency = min(2.0, max(0.5, base_efficiency + (quality_bonus * 0.5) + efficiency_luck))
                
                # 価格決定 (原価積み上げ + 利益)
                # 材料費係数
                # パーツ構成からコスト合計を算出
                parts_config = json.loads(proj['parts_config'])
                material_cost = sum(p['cost'] for p in parts_config.values())
                
                # 基準価格 (Base Price): 顧客が感じる価値の金銭換算
                # 材料費 * (品質スコア + コンセプトスコア) / 2 程度をベースにする
                base_price = int(material_cost * ((final_concept + 3.0) / 2.0))
                
                # メーカー希望小売価格 (MSRP): 原価 + 利益 + マージン
                # 原価の約2倍程度を定価とする
                sales_price = max(1, base_price) # 0円防止

                with db.transaction() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE product_designs 
                        SET status = 'completed', concept_score = ?, production_efficiency = ?, base_price = ?, sales_price = ?
                        WHERE id = ?
                    """, (final_concept, final_efficiency, base_price, sales_price, proj['id']))

                    # 開発ノウハウの蓄積
                    cursor.execute("UPDATE companies SET dev_knowhow = dev_knowhow + ? WHERE id = ?", (gb.DEV_KNOWHOW_GAIN, company_id))
                    
                    cursor.execute("INSERT INTO news_logs (week, company_id, message, type) VALUES (?, ?, ?, ?)",
                                   (week, company_id, f"新製品 '{proj['name']}' の開発が完了しました。", 'info'))
            
                db.log_file_event(week, company_id, "Development Complete", f"Completed {proj['name']}")
                db.increment_weekly_stat(week, company_id, 'development_completed', 1)

    def process_product_obsolescence(self, week):
        """
        既存製品の陳腐化: 毎週少しずつコンセプトスコアを減衰させる
        """
        db.execute_query(f"UPDATE product_designs SET concept_score = concept_score * {gb.CONCEPT_DECAY_RATE} WHERE status = 'completed' AND concept_score > 1.0")

    def process_banking(self, week):
        """
        金利支払いと信用格付けの更新
        """
        companies = db.fetch_all("SELECT * FROM companies WHERE is_active = 1")
        if companies:
            with db.transaction() as conn:
                cursor = conn.cursor()
                for comp in companies:
                    # 1. 金利支払い
                    # トランザクション内で読み取るためcursorを使用
                    cursor.execute("SELECT * FROM loans WHERE company_id = ?", (comp['id'],))
                    loans = cursor.fetchall()
                    total_debt = 0
                    for loan in loans:
                        # 週次利払い (年利 / 52)
                        interest = int(loan['amount'] * loan['interest_rate'] / 52)
                        cursor.execute("UPDATE companies SET funds = funds - ? WHERE id = ?", (interest, comp['id']))
                        cursor.execute("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, 'interest', ?)",
                                       (week, comp['id'], interest))
                        total_debt += loan['amount']
                    
                    # 2. 格付け更新
                    base_score = 50
                    fund_score = min(20, comp['funds'] // 100000000)
                    debt_penalty = 0
                    if comp['funds'] > 0 and total_debt > comp['funds'] * 2:
                        debt_penalty = 20
                    
                    new_rating = max(1, min(100, base_score + fund_score - debt_penalty))
                    new_limit = new_rating * gb.CREDIT_LIMIT_MULTIPLIER
                    
                    cursor.execute("UPDATE companies SET credit_rating = ?, borrowing_limit = ? WHERE id = ?", 
                                   (new_rating, new_limit, comp['id']))

    def check_bankruptcy(self, week):
        """
        倒産判定: 資金がマイナス かつ 追加借入不可
        """
        companies = db.fetch_all("SELECT * FROM companies WHERE type != 'system_supplier' AND is_active = 1")
        for comp in companies:
            if comp['funds'] < 0:
                # 借入余力を確認
                loans = db.fetch_one("SELECT SUM(amount) as total FROM loans WHERE company_id = ?", (comp['id'],))
                current_debt = loans['total'] if loans['total'] else 0
                
                if current_debt >= comp['borrowing_limit']:
                    # 倒産処理 (今回はログ出力と社名変更のみ)
                    if comp['type'] == 'player':
                        print(f"GAME OVER: Player went bankrupt in week {week}.")
                        db.execute_query("UPDATE companies SET name = name || ' (倒産)', is_active = 0 WHERE id = ?", (comp['id'],))
                        self.log_news(week, comp['id'], "資金繰りが悪化し、倒産しました。", 'error')
                    else:
                        # NPC企業の新陳代謝
                        print(f"METABOLISM: {comp['name']} went bankrupt. Dissolving and creating new company.")
                        self.log_news(week, comp['id'], f"{comp['name']} が倒産しました。", 'market')
                        
                        # 1. 従業員の解雇
                        db.execute_query("UPDATE npcs SET company_id = NULL, department = NULL, role = NULL, loyalty = 50 WHERE company_id = ?", (comp['id'],))
                        
                        # 2. 資産・負債の消滅 (簡易処理)
                        db.execute_query("DELETE FROM inventory WHERE company_id = ?", (comp['id'],))
                        db.execute_query("DELETE FROM product_designs WHERE company_id = ?", (comp['id'],))
                        # 施設は市場へ解放 (所有者なしの状態にする)
                        db.execute_query("UPDATE facilities SET company_id = NULL WHERE company_id = ?", (comp['id'],))
                        db.execute_query("DELETE FROM loans WHERE company_id = ?", (comp['id'],))
                        
                        # 3. 企業データの論理削除
                        old_type = comp['type']
                        db.execute_query("UPDATE companies SET is_active = 0 WHERE id = ?", (comp['id'],))
                        
                        # 4. 新企業の設立
                        new_name = name_generator.generate_company_name(old_type)
                        initial_funds = gb.INITIAL_FUNDS_MAKER if old_type == 'npc_maker' else gb.INITIAL_FUNDS_RETAIL
                        
                        new_id = db.execute_query("INSERT INTO companies (name, type, funds) VALUES (?, ?, ?)", (new_name, old_type, initial_funds))
                        
                        # 5. CEOの就任 (労働市場から役員適正の高い人材を抜擢)
                        candidate = db.fetch_one("SELECT id FROM npcs WHERE company_id IS NULL ORDER BY executive DESC LIMIT 1")
                        if candidate:
                            db.execute_query("UPDATE npcs SET company_id = ?, role = ?, department = ? WHERE id = ?", 
                                             (new_id, gb.ROLE_CEO, gb.DEPT_HR, candidate['id'])) # CEOは一旦HR所属扱いにしておく

    def check_ipo_eligibility(self, company_id):
        """IPO条件を満たしているかチェック"""
        company = db.fetch_one("SELECT * FROM companies WHERE id = ?", (company_id,))
        if not company: return False, ["企業が存在しません"]
        
        reasons = []
        is_eligible = True
        
        # 1. 純資産チェック (簡易: 資金 + 在庫評価 + 施設評価 - 負債)
        funds = company['funds']
        # 在庫評価 (原価ベースが望ましいが、簡易的にsales_price * 0.5程度で評価)
        inv_val = db.fetch_one("""
            SELECT SUM(i.quantity * d.sales_price * 0.5) as val 
            FROM inventory i JOIN product_designs d ON i.design_id = d.id 
            WHERE i.company_id = ?
        """, (company_id,))['val'] or 0
        # 施設 (購入価格ベース)
        fac_val = db.fetch_one("""
            SELECT SUM(rent * 100) as val FROM facilities 
            WHERE company_id = ? AND is_owned = 1
        """, (company_id,))['val'] or 0
        
        total_assets = funds + inv_val + fac_val
        
        # 負債
        debt = db.fetch_one("SELECT SUM(amount) as val FROM loans WHERE company_id = ?", (company_id,))['val'] or 0
        
        net_assets = total_assets - debt
        
        if net_assets < gb.IPO_MIN_NET_ASSETS:
            is_eligible = False
            reasons.append(f"純資産不足 (現在: {net_assets/100000000:.1f}億円 / 必要: {gb.IPO_MIN_NET_ASSETS/100000000:.1f}億円)")
            
        # 2. 黒字要件 (直近4週間の純利益合計 > 0)
        current_week = self.get_current_week()
        profit_res = db.fetch_one("""
            SELECT SUM(CASE WHEN category = 'revenue' THEN amount ELSE 0 END) - 
                   SUM(CASE WHEN category NOT IN ('revenue', 'material', 'stock_purchase', 'facility_purchase', 'facility_sell', 'equity_finance') THEN amount ELSE 0 END) as profit
            FROM account_entries WHERE company_id = ? AND week >= ?
        """, (company_id, current_week - gb.IPO_MIN_PROFIT_WEEKS))
        recent_profit = profit_res['profit'] or 0
        
        if recent_profit <= 0:
            is_eligible = False
            reasons.append("直近4週間の累積赤字")
            
        # 3. 格付け要件
        if company['credit_rating'] < gb.IPO_MIN_CREDIT_RATING:
            is_eligible = False
            reasons.append(f"信用格付け不足 (現在: {company['credit_rating']} / 必要: {gb.IPO_MIN_CREDIT_RATING})")
            
        # 4. 既に上場していないか
        if company['listing_status'] != 'private' and company['listing_status'] != 'applying':
            is_eligible = False
            reasons.append("既に上場済み")

        return is_eligible, reasons

    def process_stock_market(self, week, all_caps):
        """
        株式市場の処理: 株価更新、決算発表、経理キャパシティ判定
        """
        companies = db.fetch_all("SELECT * FROM companies WHERE type != 'system_supplier' AND is_active = 1")
        
        with db.transaction() as conn:
            cursor = conn.cursor()
            
            for comp in companies:
                cid = comp['id']
                
                # Rowオブジェクトをdictに変換して変更可能にする
                comp_dict = dict(comp)
                
                # --- 1. 決算処理 (Accounting) ---
                # 四半期ごとの締め処理
                is_quarter_end = (week % gb.QUARTER_WEEKS == 0)
                
                if is_quarter_end:
                    # 四半期データの集計
                    start_week = week - gb.QUARTER_WEEKS + 1
                    
                    # PL集計
                    cursor.execute("""
                        SELECT category, SUM(amount) as total 
                        FROM account_entries 
                        WHERE company_id = ? AND week BETWEEN ? AND ?
                        GROUP BY category
                    """, (cid, start_week, week))
                    entries = cursor.fetchall()
                    
                    revenue = 0
                    expenses = 0
                    for e in entries:
                        if e['category'] == 'revenue': revenue += e['total']
                        elif e['category'] not in ['material', 'stock_purchase', 'facility_purchase', 'facility_sell']:
                            expenses += e['total']
                    
                    net_profit = revenue - expenses
                    
                    # BS集計 (簡易)
                    total_assets = comp['funds'] # + 在庫 + 施設 (今回は簡易化)
                    # 負債
                    cursor.execute("SELECT SUM(amount) as total FROM loans WHERE company_id = ?", (cid,))
                    debt_res = cursor.fetchone()
                    debt = debt_res['total'] if debt_res and debt_res['total'] else 0
                    net_assets = total_assets - debt
                    
                    # 決算書作成 (Status: draft)
                    year = 2025 + (week - 1) // 52
                    q = ((week - 1) // 13) % 4 + 1
                    period_str = f"{year} Q{q}"
                    
                    cursor.execute("""
                        INSERT INTO financial_reports (company_id, week, period_str, revenue, net_profit, total_assets, net_assets, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'draft')
                    """, (cid, week, period_str, revenue, net_profit, total_assets, net_assets))
                
                # --- 2. 経理キャパシティと発表判定 ---
                # 未発表の決算があるか確認
                cursor.execute("SELECT * FROM financial_reports WHERE company_id = ? AND status = 'draft'", (cid,))
                draft_report = cursor.fetchone()
                
                accounting_penalty = 1.0 # 株価への影響係数
                
                if draft_report:
                    # 経理負荷の計算
                    # 取引数
                    cursor.execute("SELECT COUNT(*) as cnt FROM transactions WHERE (seller_id = ? OR buyer_id = ?) AND week = ?", (cid, cid, week))
                    tx_count = cursor.fetchone()['cnt']
                    
                    # 従業員数
                    cursor.execute("SELECT COUNT(*) as cnt FROM npcs WHERE company_id = ?", (cid,))
                    emp_count = cursor.fetchone()['cnt']
                    
                    load = (tx_count * gb.ACCOUNTING_LOAD_PER_TRANSACTION) + (emp_count * gb.ACCOUNTING_LOAD_PER_EMPLOYEE)
                    
                    # 経理キャパシティ
                    acc_cap = all_caps[cid]['accounting_capacity'] if cid in all_caps else 0
                    
                    # 判定: キャパが負荷を上回っていれば発表
                    # 不足している場合、確率で遅延
                    publish_prob = 1.0
                    if load > 0 and acc_cap < load:
                        publish_prob = max(0.1, acc_cap / load)
                    
                    # 締め後、翌週から発表可能。最大4週遅れると強制発表（ただし信頼失墜）
                    weeks_since_close = week - draft_report['week']
                    
                    if weeks_since_close > 0:
                        if random.random() < publish_prob or weeks_since_close >= 4:
                            status = 'published'
                            if weeks_since_close >= 2:
                                status = 'delayed'
                                accounting_penalty = 1.0 - (weeks_since_close * gb.REPORT_PUBLISH_DELAY_PENALTY)
                                self.log_news(week, cid, f"決算発表を行いました (遅延: {weeks_since_close}週)", 'warning')
                            else:
                                self.log_news(week, cid, f"決算発表を行いました ({draft_report['period_str']})", 'info')
                                
                            cursor.execute("UPDATE financial_reports SET status = ?, published_week = ? WHERE id = ?", 
                                           (status, week, draft_report['id']))
                
                # --- 2.5 株価計算用パラメータの準備 ---
                # IPO時の公募価格計算でも使うため、ここで計算しておく
                
                shares = comp_dict['outstanding_shares']
                
                # --- 3. 株価計算 (Valuation) ---
                # 理論株価 = (EPS * PER + BPS * PBR) / 2
                
                # 予想EPS: 直近4週の利益 * 13 / 株式数
                cursor.execute("""
                    SELECT SUM(CASE WHEN category = 'revenue' THEN amount ELSE 0 END) - 
                           SUM(CASE WHEN category NOT IN ('revenue', 'material', 'stock_purchase', 'facility_purchase', 'facility_sell') THEN amount ELSE 0 END) as profit
                    FROM account_entries WHERE company_id = ? AND week >= ?
                """, (cid, week - 4))
                recent_profit = cursor.fetchone()['profit'] or 0
                annual_profit_forecast = recent_profit * 13
                
                eps = annual_profit_forecast / shares
                
                # BPS: 純資産 / 株式数 (より正確な資産評価)
                funds = comp_dict['funds']
                
                # 在庫評価 (販売価格の50%で評価)
                cursor.execute("""
                    SELECT SUM(i.quantity * d.sales_price * 0.5) as val 
                    FROM inventory i JOIN product_designs d ON i.design_id = d.id 
                    WHERE i.company_id = ?
                """, (cid,))
                inv_res = cursor.fetchone()
                inv_val = inv_res['val'] if inv_res and inv_res['val'] else 0
                
                # 施設評価 (所有物件の購入価格相当)
                cursor.execute("""
                    SELECT SUM(rent * 100) as val FROM facilities 
                    WHERE company_id = ? AND is_owned = 1
                """, (cid,))
                fac_res = cursor.fetchone()
                fac_val = fac_res['val'] if fac_res and fac_res['val'] else 0
                
                # 負債
                cursor.execute("SELECT SUM(amount) as total FROM loans WHERE company_id = ?", (cid,))
                debt_res = cursor.fetchone()
                debt = debt_res['total'] if debt_res and debt_res['total'] else 0
                
                net_assets = funds + inv_val + fac_val - debt
                bps = max(1, net_assets / shares)
                
                # PER, PBR基準
                target_per = gb.PER_BASE
                target_pbr = gb.PBR_BASE + (comp_dict['brand_power'] / 100.0) # ブランドプレミアム
                
                # 理論株価
                if eps > 0:
                    # 黒字: 収益価値と資産価値の併用
                    theoretical_price = ((eps * target_per) + (bps * target_pbr)) / 2.0
                else:
                    # 赤字: 資産価値(PBR)のみで評価 (赤字で株価がマイナスになるのを防ぐ)
                    theoretical_price = bps * target_pbr
                
                theoretical_price = max(1, theoretical_price) # 1円以上
                
                # --- IPO処理 (審査・上場) ---
                if comp_dict['listing_status'] == 'applying':
                    is_ok, reasons = self.check_ipo_eligibility(cid)
                    if is_ok:
                        # 上場承認 & 公募増資
                        offering_price = int(theoretical_price * gb.IPO_DISCOUNT_RATE)
                        new_shares = int(shares * gb.IPO_NEW_SHARE_RATIO)
                        raised_funds = offering_price * new_shares
                        fees = int(raised_funds * gb.IPO_FEE_RATE)
                        net_proceeds = raised_funds - fees
                        
                        # DB更新
                        cursor.execute("""
                            UPDATE companies 
                            SET listing_status = 'public', funds = funds + ?, outstanding_shares = outstanding_shares + ?, stock_price = ? 
                            WHERE id = ?
                        """, (net_proceeds, new_shares, offering_price, cid))
                        
                        # 資金調達ログ (PL外)
                        cursor.execute("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, 'equity_finance', ?)",
                                       (week, cid, net_proceeds))
                        
                        self.log_news(week, cid, f"祝！新規上場(IPO)を果たしました！ 公募価格: {offering_price:,}円, 調達額: {net_proceeds:,}円", 'info')
                        
                        # ブランド力ボーナス
                        cursor.execute("UPDATE companies SET brand_power = brand_power + 20 WHERE id = ?", (cid,))
                        
                        # メモリ上の値を更新して、後続の履歴保存に反映させる
                        comp_dict['listing_status'] = 'public'
                        comp_dict['outstanding_shares'] += new_shares
                        comp_dict['stock_price'] = offering_price
                        # fundsは株価計算に使わないので更新省略
                        
                        # 初値がついたので、この週の株価計算はスキップ（公募価格＝終値とする）
                        # ただし履歴には残したいので後続へ進む
                    else:
                        # 審査落ち
                        cursor.execute("UPDATE companies SET listing_status = 'private' WHERE id = ?", (cid,))
                        self.log_news(week, cid, f"IPO審査に落ちました。理由: {', '.join(reasons)}", 'error')

                # 現在株価からの遷移
                current_price = comp_dict['stock_price']
                alpha = 0.1 # 織り込み係数 (急激な変動を抑えるため0.2->0.1へ変更)
                
                # 変動
                volatility = random.uniform(1.0 - gb.STOCK_VOLATILITY, 1.0 + gb.STOCK_VOLATILITY)
                
                proposed_price = int(((theoretical_price * alpha) + (current_price * (1 - alpha))) * volatility * accounting_penalty)
                
                # ストップ高・ストップ安 (週次変動制限 ±20%)
                max_price = int(current_price * 1.2)
                min_price = int(current_price * 0.8)
                
                new_price = max(min_price, min(max_price, proposed_price))
                new_price = max(1, new_price)
                
                # --- 株式分割 (Stock Split) ---
                # 株価が10万円を超えた場合、5000円を目安に分割する
                # IPO等で株式数が変動している可能性があるため、最新の値を参照
                shares = comp_dict['outstanding_shares']
                
                if new_price > 100000:
                    split_ratio = int(new_price / 5000)
                    if split_ratio >= 2:
                        new_shares = shares * split_ratio
                        new_price = int(new_price / split_ratio)
                        
                        cursor.execute("UPDATE companies SET outstanding_shares = ? WHERE id = ?", (new_shares, cid))
                        self.log_news(week, cid, f"株式分割を実施しました (1:{split_ratio})。株価は {new_price:,}円 に調整されました。", 'market')
                        
                        shares = new_shares
                        comp_dict['outstanding_shares'] = shares

                market_cap = new_price * shares
                
                # 更新
                cursor.execute("UPDATE companies SET stock_price = ?, market_cap = ? WHERE id = ?", (new_price, market_cap, cid))
                
                # 履歴保存
                real_per = new_price / eps if eps > 0 else 0
                real_pbr = new_price / bps
                cursor.execute("""
                    INSERT INTO stock_history (week, company_id, stock_price, market_cap, eps, bps, per, pbr)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (week, cid, new_price, market_cap, eps, bps, real_per, real_pbr))

    def get_financial_report(self, company_id, current_week, period='weekly', target=0):
        """指定された期間の財務諸表(PL/BS)データを生成して返す"""
        # デフォルトターゲットの設定
        if target == 0:
            if period == 'weekly':
                target = max(1, current_week - 1)
            elif period == 'quarterly':
                current_q = (current_week - 1) // 13 + 1
                target = max(1, current_q if (current_week - 1) % 13 != 0 else current_q - 1)
            elif period == 'yearly':
                current_y = (current_week - 1) // 52 + 1
                target = max(1, current_y if (current_week - 1) % 52 != 0 else current_y - 1)

        # 期間計算
        start_week, end_week = 1, 1
        label = ""
        
        if period == 'weekly':
            start_week = end_week = target
            y = 2025 + (target - 1) // 52
            w = (target - 1) % 52 + 1
            label = f"{y}年 Week {w}"
        elif period == 'quarterly':
            start_week = (target - 1) * 13 + 1
            end_week = target * 13
            y = 2025 + (target - 1) // 4
            q = (target - 1) % 4 + 1
            s_w = (start_week - 1) % 52 + 1
            e_w = (end_week - 1) % 52 + 1
            label = f"{y}年 第{q}四半期 (Week {s_w}-{e_w})"
        elif period == 'yearly':
            start_week = (target - 1) * 52 + 1
            end_week = target * 52
            y = 2025 + (target - 1)
            label = f"{y}年 (第{target}期)"

        # PL集計
        entries = db.fetch_all("""
            SELECT category, SUM(amount) as total 
            FROM account_entries 
            WHERE company_id = ? AND week BETWEEN ? AND ?
            GROUP BY category
        """, (company_id, start_week, end_week))
        
        pl = {k: 0 for k in ['revenue', 'cogs', 'gross_profit', 'labor', 'rent', 'ad', 'other_sga', 'operating_profit', 'interest', 'net_profit']}
        for e in entries:
            cat, amt = e['category'], int(e['total'])
            if cat in pl: pl[cat] += amt
            elif 'labor' in cat: pl['labor'] += amt
            elif 'rent' in cat: pl['rent'] += amt
        
        pl['gross_profit'] = pl['revenue'] - pl['cogs']
        total_sga = pl['labor'] + pl['rent'] + pl['ad'] + pl['other_sga']
        pl['operating_profit'] = pl['gross_profit'] - total_sga
        pl['net_profit'] = pl['operating_profit'] - pl['interest']
        
        # BS集計
        company = db.fetch_one("SELECT funds FROM companies WHERE id = ?", (company_id,))
        bs = {'cash': company['funds'], 'inventory': 0, 'fixed_assets': 0, 'total_assets': 0, 'debt': 0, 'equity': 0}
        
        # 在庫評価
        inv_items = db.fetch_all("SELECT i.quantity, d.company_id as maker_id, d.parts_config, d.sales_price FROM inventory i JOIN product_designs d ON i.design_id = d.id WHERE i.company_id = ?", (company_id,))
        for item in inv_items:
            if item['maker_id'] == company_id:
                p_conf = json.loads(item['parts_config']) if item['parts_config'] else {}
                unit_cost = sum(p['cost'] for p in p_conf.values()) if p_conf else 0
                bs['inventory'] += item['quantity'] * unit_cost
            else:
                bs['inventory'] += item['quantity'] * int(item['sales_price'] * 0.7)
        
        bs['fixed_assets'] = db.fetch_one("SELECT SUM(rent * 100) as val FROM facilities WHERE company_id = ? AND is_owned = 1", (company_id,))['val'] or 0
        bs['total_assets'] = bs['cash'] + bs['inventory'] + bs['fixed_assets']
        bs['debt'] = db.fetch_one("SELECT SUM(amount) as val FROM loans WHERE company_id = ?", (company_id,))['val'] or 0
        bs['equity'] = bs['total_assets'] - bs['debt']
        
        return {'pl': pl, 'bs': bs, 'label': label, 'target': target, 'prev_target': target - 1 if target > 1 else None, 'next_target': target + 1}
