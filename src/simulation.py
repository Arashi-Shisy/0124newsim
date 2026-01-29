# c:\0124newSIm\simulation.py
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
        基本値: 部署配属NPCの能力平均
        補正: 部長(Manager/CxO)のマネジメント能力合計 * 0.1 を加算
        """
        # 必要なカラムのみ取得して高速化
        if employees is None:
            employees = db.fetch_all("""
                SELECT department, role, diligence, production, development, sales, hr, pr, accounting, store_ops, management 
                FROM npcs WHERE company_id = ?""", (company_id,))
        
        caps = {
            'production': 0, 'development': 0, 'sales': 0, 
            'hr': 0, 'pr': 0, 'accounting': 0, 'store_ops': 0,
            'stability': 0,
            'production_capacity': 0, 'development_capacity': 0, 'sales_capacity': 0,
            'hr_capacity': 0, 'pr_capacity': 0, 'accounting_capacity': 0, 'store_ops_capacity': 0,
            'store_throughput': 0
        }

        # 施設キャパシティの取得
        facilities = db.fetch_all("SELECT type, size FROM facilities WHERE company_id = ?", (company_id,))
        
        # 基礎キャパシティ (施設がなくても最低限活動できる場所: ガレージ/自宅など)
        # NPC_SCALE_FACTOR(8) * 1.5人分 = 12 程度確保しておく
        base_cap = int(gb.NPC_SCALE_FACTOR * 1.5)
        caps_limit = {'factory': base_cap, 'store': base_cap, 'office': base_cap}
        for f in facilities:
            if f['type'] in caps_limit:
                caps_limit[f['type']] += f['size']

        # 施設稼働状況の初期化 (従業員がいない場合用)
        caps['facilities'] = {
            'factory': {'name': '工場', 'usage': 0, 'npc_count': 0, 'limit': int(caps_limit['factory']), 'efficiency': 1.0},
            'store': {'name': '店舗', 'usage': 0, 'npc_count': 0, 'limit': int(caps_limit['store']), 'efficiency': 1.0},
            'office': {'name': 'オフィス', 'usage': 0, 'npc_count': 0, 'limit': int(caps_limit['office']), 'efficiency': 1.0}
        }
        
        if not employees:
            return caps

        # 勤勉さ平均 (安定性)
        if employees:
            caps['stability'] = sum(e['diligence'] for e in employees) / len(employees)
        else:
            caps['stability'] = 0

        # 部署ごとの集計
        dept_staff = {d: [] for d in gb.DEPARTMENTS}
        dept_managers = {d: [] for d in gb.DEPARTMENTS}
        dept_cxos = {d: [] for d in gb.DEPARTMENTS}

        for e in employees:
            d = e['department']
            if d in dept_staff:
                # 役員 (CxO, CEO) は現場人員(dept_staff)には含めない
                # ただし、マネジメントボーナス計算用に dept_cxos には追加する
                if e['role'] in [gb.ROLE_CXO, gb.ROLE_CEO]:
                    dept_cxos[d].append(e)
                else:
                    # 一般社員、部長、部長補佐
                    dept_staff[d].append(e)
                    if e['role'] == gb.ROLE_MANAGER:
                        dept_managers[d].append(e)
        
        # キャパシティ制限の適用 (あふれた人員は計算から除外、または効率低下)
        # 使用量記録 (補正前)
        prod_usage = len(dept_staff[gb.DEPT_PRODUCTION]) * gb.NPC_SCALE_FACTOR
        store_usage = len(dept_staff[gb.DEPT_STORE]) * gb.NPC_SCALE_FACTOR
        
        # 生産部 -> 工場
        prod_staff = dept_staff[gb.DEPT_PRODUCTION]
        if len(prod_staff) * gb.NPC_SCALE_FACTOR > caps_limit['factory']:
            # 能力が高い順に優先して施設に入れる
            prod_staff.sort(key=lambda x: x['production'], reverse=True)
            dept_staff[gb.DEPT_PRODUCTION] = prod_staff[:int(caps_limit['factory'] // gb.NPC_SCALE_FACTOR)]
        
        # 店舗部 -> 店舗
        store_staff = dept_staff[gb.DEPT_STORE]
        if len(store_staff) * gb.NPC_SCALE_FACTOR > caps_limit['store']:
            store_staff.sort(key=lambda x: x['store_ops'], reverse=True)
            dept_staff[gb.DEPT_STORE] = store_staff[:int(caps_limit['store'] // gb.NPC_SCALE_FACTOR)]
            
        # その他部署 -> オフィス
        office_depts = [gb.DEPT_SALES, gb.DEPT_DEV, gb.DEPT_HR, gb.DEPT_PR, gb.DEPT_ACCOUNTING]
        total_office_staff = sum(len(dept_staff[d]) for d in office_depts) * gb.NPC_SCALE_FACTOR
        
        office_efficiency = 1.0
        if total_office_staff > caps_limit['office'] and total_office_staff > 0:
            # オフィスは部署が混在しているため、一律で効率を下げる処理とする
            # (あふれた人数分だけ能力が発揮されない = 全体の平均能力に係数をかける)
            # 例: キャパ10人で20人いる場合、効率0.5
            office_efficiency = caps_limit['office'] / total_office_staff

        # 能力マッピング
        stat_map = {
            gb.DEPT_PRODUCTION: 'production',
            gb.DEPT_DEV: 'development',
            gb.DEPT_SALES: 'sales',
            gb.DEPT_HR: 'hr',
            gb.DEPT_PR: 'pr',
            gb.DEPT_ACCOUNTING: 'accounting',
            gb.DEPT_STORE: 'store_ops'
        }

        for dept, stat in stat_map.items():
            staff = dept_staff[dept]
            if staff:
                # オフィス系部署の場合、効率係数を適用
                efficiency = office_efficiency if dept in office_depts else 1.0
                
                avg_stat = sum(e[stat] for e in staff) / len(staff)
                sum_stat = sum(e[stat] for e in staff)
                
                # マネジメントボーナス: 部長は各部署1人のみ適用
                manager_mgmt = 0
                if dept_managers[dept]:
                    manager_mgmt = dept_managers[dept][0]['management']
                
                cxo_mgmt = 0
                if dept_cxos[dept]:
                    cxo_mgmt = dept_cxos[dept][0]['management']

                mgmt_bonus = (manager_mgmt * gb.MGMT_BONUS_MANAGER) + (cxo_mgmt * gb.MGMT_BONUS_CXO)
                
                # 能力値は100を超えないようにキャップ
                caps[stat] = min(100.0, (avg_stat + mgmt_bonus) * efficiency)
                
                # キャパシティ計算 (要件定義に基づく)
                if stat == 'store_ops':
                    # 店舗運営キャパシティ: 合計 + マネジメント
                    caps[f"{stat}_capacity"] = (sum_stat + mgmt_bonus) * efficiency * gb.NPC_SCALE_FACTOR
                else:
                    # その他: 合計
                    caps[f"{stat}_capacity"] = sum_stat * efficiency * gb.NPC_SCALE_FACTOR
        
        # 安定性によるデバフ (要件: 低い場合ランダムでデバフ発生)
        # 安定性が50未満の場合、確率で全能力ダウン (日によって調子が悪い)
        if caps['stability'] < 50:
            # 安定性0で最大50%ダウン、安定性50でデバフなし
            max_penalty = 0.5 * (1.0 - (caps['stability'] / 50.0))
            penalty_factor = 1.0 - random.uniform(0, max_penalty)
            
            for key in caps:
                if key != 'stability' and isinstance(caps[key], (int, float)): # 安定性自体は下げない。辞書型(facilities)も除外
                    caps[key] *= penalty_factor

        # 販売キャパシティ計算 (店舗スタッフ数 * 効率 * 能力補正)
        # process_b2cでの再クエリを避けるためここで計算 (名称をstore_throughputに変更)
        store_staff_count = len(dept_staff[gb.DEPT_STORE])
        caps['store_throughput'] = store_staff_count * gb.NPC_SCALE_FACTOR * gb.BASE_SALES_EFFICIENCY * (caps['store_ops'] / 50.0)
        
        # 施設稼働状況の記録
        caps['facilities'] = {
            'factory': {
                'name': '工場',
                'usage': int(prod_usage),
                'npc_count': len(dept_staff[gb.DEPT_PRODUCTION]),
                'limit': int(caps_limit['factory']),
                'efficiency': 1.0 if prod_usage <= caps_limit['factory'] else caps_limit['factory'] / prod_usage if prod_usage > 0 else 1.0
            },
            'store': {
                'name': '店舗',
                'usage': int(store_usage),
                'npc_count': len(dept_staff[gb.DEPT_STORE]),
                'limit': int(caps_limit['store']),
                'efficiency': 1.0 if store_usage <= caps_limit['store'] else caps_limit['store'] / store_usage if store_usage > 0 else 1.0
            },
            'office': {
                'name': 'オフィス',
                'usage': int(total_office_staff),
                'npc_count': sum(len(dept_staff[d]) for d in office_depts),
                'limit': int(caps_limit['office']),
                'efficiency': office_efficiency
            }
        }
        
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
                inventory_by_company[inv['company_id']].append(inv)

        # 全商品設計書
        all_designs_res = db.fetch_all("SELECT * FROM product_designs")
        designs_by_company = {c['id']: [] for c in all_companies}
        for design in all_designs_res:
            if design['company_id'] in designs_by_company:
                designs_by_company[design['company_id']].append(design)

        # 採用候補者プール
        candidates_pool = db.fetch_all("SELECT * FROM npcs WHERE company_id IS NULL LIMIT 500")

        # B2B注文 (保留中)
        pending_orders_res = db.fetch_all("SELECT * FROM b2b_orders WHERE status = 'pending'")
        orders_for_seller = {c['id']: [] for c in all_companies}
        for order in pending_orders_res:
            if order['seller_id'] in orders_for_seller:
                orders_for_seller[order['seller_id']].append(order)

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

            # 各メソッドに事前取得したデータを渡す
            logic.decide_financing(current_week)
            logic.decide_hiring(current_week, candidates_pool=candidates_pool)
            logic.decide_salary(current_week)
            logic.decide_promotion(current_week)
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
                my_inventory=company_inventory
            )
            logic.decide_order_fulfillment(
                current_week,
                orders=orders_for_seller.get(comp['id'], []),
                inventory=company_inventory
            )
            logic.decide_development(current_week, designs=company_designs)
            logic.decide_facilities(current_week)
            logic.decide_advertising(current_week)
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
        # 総需要計算
        # 景気指数を反映
        economic_index = db.fetch_one("SELECT economic_index FROM game_state")['economic_index']
        market_demand = int(gb.BASE_MARKET_DEMAND * economic_index * random.uniform(0.95, 1.05))
        
        # 需要を記録
        db.execute_query("INSERT INTO market_trends (week, b2c_demand) VALUES (?, ?)", (week, market_demand))
        
        # 前週のB2C販売数取得 (トレンド/バンドワゴン効果用)
        prev_b2c_sales = db.fetch_all("SELECT design_id, SUM(quantity) as total FROM transactions WHERE week = ? AND type = 'b2c' GROUP BY design_id", (week - 1,))
        prev_sales_map = {r['design_id']: r['total'] for r in prev_b2c_sales}

        # 小売在庫の取得
        retail_stocks = db.fetch_all("""
            SELECT i.id, i.company_id, i.quantity, i.sales_price as retail_price, i.design_id, d.name as product_name,
                   d.concept_score, d.base_price, d.sales_price as msrp, d.awareness, d.material_score, d.parts_config,
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

        # スコアリング
        scored_stocks = []
        total_score = 0
        for stock in retail_stocks:
            cid = stock['company_id']
            if cid not in comp_caps:
                caps = self.calculate_capabilities(cid)
                comp_caps[cid] = caps

            # 店舗スコア: 小売ブランド * 店舗運営力 * アクセス(簡易的に1.0)
            store_ops = comp_caps[cid]['store_ops']
            store_score = (1 + stock['retail_brand'] / 100.0) * (1 + store_ops / 100.0)

            # 製品スコア: (コンセプト * 材料 * (1 + メーカーブランド/100) * (1 + 認知度/100)) / 価格係数
            # 顧客は「基準価格(base_price)」と「実売価格(retail_price)」の差分を見る
            # base_price: その製品の価値に見合った価格
            base_price = stock['base_price']
            retail_price = stock['retail_price']
            
            # 価格係数: 基準価格に対して安ければスコアアップ、高ければダウン
            # 例: 基準300万で売値270万 -> 0.9 -> 割安 -> スコアアップ
            price_ratio = retail_price / base_price if base_price > 0 else 1.0
            price_factor = price_ratio ** 2 # 価格感度は強めに
            
            # 顧客の気まぐれ・トレンド (Weekly Trend): ±20%の揺らぎ
            trend_factor = random.uniform(0.8, 1.2)
            
            # バンドワゴン効果: 前週売れているものはさらに売れやすい (対数で緩やかに)
            prev_sold = prev_sales_map.get(stock['design_id'], 0)
            bandwagon_bonus = 1.0 + (math.log1p(prev_sold) * 0.15)
            
            # 顧客の多様性と気まぐれ (Preference Noise): 正規分布で揺らぎを持たせる
            preference_noise = random.gauss(1.0, 0.15)
            
            product_score = (stock['concept_score'] * stock['material_score'] * 
                             (1 + stock['maker_brand'] / 100.0) * (1 + stock['awareness'] / 100.0)) / price_factor
            
            # 総合スコア
            final_score = store_score * product_score * trend_factor * bandwagon_bonus * preference_noise
            # 後続の計算で小売価格と卸売価格を両方使うため、ここで保持しておく
            scored_stocks.append({**stock, 'score': final_score})
            total_score += final_score
            
        # 販売数記録用マップ (DB更新をまとめるため)
        sales_record = {s['id']: 0 for s in scored_stocks}

        # 需要分配
        # 在庫切れやキャパ不足で余った需要を再分配するため、最大3回ループする
        remaining_demand = market_demand
        
        for _ in range(3):
            if remaining_demand <= 0: break
            
            # 販売可能な在庫のみ抽出
            active_stocks = [s for s in scored_stocks if (s['quantity'] - sales_record[s['id']]) > 0 and comp_caps[s['company_id']]['store_throughput'] > 0]
            if not active_stocks: break
            
            current_total_score = sum(s['score'] for s in active_stocks)
            if current_total_score == 0: break
            
            round_demand = remaining_demand
            remaining_demand = 0 # リセット
            
            for stock in active_stocks:
                share = stock['score'] / current_total_score
                
                # 確率的丸め込み (端数切り捨てによる需要消失を防ぐ)
                float_demand = round_demand * share
                demand = int(float_demand)
                if random.random() < (float_demand - demand):
                    demand += 1
                
                # 現在の在庫数とキャパシティ
                current_qty = stock['quantity'] - sales_record[stock['id']]
                # キャパシティはfloatのままだと微小値が残るので、ここで整数化して扱う
                # ただし、確率的に切り上げることで、0.5人分の力でも運が良ければ1台売れるようにする
                cap_float = comp_caps[stock['company_id']]['store_throughput']
                capacity = int(cap_float)
                if random.random() < (cap_float - capacity):
                    capacity += 1
                
                sold = min(demand, current_qty, capacity)
                sold = int(sold) # 念のため整数化
                
                if sold > 0:
                    sales_record[stock['id']] += sold
                    comp_caps[stock['company_id']]['store_throughput'] -= sold
                
                # 満たせなかった需要を次へ回す
                if demand > sold:
                    remaining_demand += (demand - sold)

        # DB更新とログ記録
        # executemany用にデータを準備
        update_inventory = []
        update_funds = []
        insert_transactions = []
        insert_revenue = []
        insert_cogs = []
        b2c_sales_counts = {} # {company_id: count}

        for stock in scored_stocks:
            sold = sales_record[stock['id']]
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

                    cursor.execute("UPDATE npcs SET company_id = ?, department = ?, role = ?, salary = ?, desired_salary = ? WHERE id = ?",
                                   (best_offer['company_id'], target_dept, gb.ROLE_MEMBER, best_offer['offer_salary'], best_offer['offer_salary'], nid))
                    
                    cursor.execute("INSERT INTO news_logs (week, company_id, message, type) VALUES (?, ?, ?, ?)",
                                   (week, best_offer['company_id'], f"{npc['name']} を採用しました (年収: ¥{best_offer['offer_salary']:,})", 'info'))
        
        # オファーテーブルのクリーンアップ (今週分は処理済み)
        db.execute_query("DELETE FROM job_offers WHERE week <= ?", (week,))

        # 企業ごとの処理 (忠誠度、成長、給与)
        companies = db.fetch_all("SELECT id, type FROM companies WHERE is_active = 1")
        
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
                
                updates = []
                params = []

                # 部署ボーナス
                dept = npc['department']
                target_stat = None
                if dept == gb.DEPT_PRODUCTION: target_stat = 'production'
                elif dept == gb.DEPT_SALES: target_stat = 'sales'
                elif dept == gb.DEPT_DEV: target_stat = 'development'
                elif dept == gb.DEPT_HR: target_stat = 'hr'
                elif dept == gb.DEPT_PR: target_stat = 'pr'
                elif dept == gb.DEPT_ACCOUNTING: target_stat = 'accounting'
                elif dept == gb.DEPT_STORE: target_stat = 'store_ops'
                
                if target_stat:
                    new_stat = min(gb.ABILITY_MAX, npc[target_stat] + base_growth)
                    updates.append(f"{target_stat} = ?")
                    params.append(new_stat)
                
                # マネジメント (部長補佐以上)
                if npc['role'] in [gb.ROLE_ASSISTANT_MANAGER, gb.ROLE_MANAGER, gb.ROLE_CXO, gb.ROLE_CEO]:
                     new_mgmt = min(gb.ABILITY_MAX, npc['management'] + base_growth)
                     updates.append("management = ?")
                     params.append(new_mgmt)
                     
                # 役員適正 (部長以上)
                if npc['role'] in [gb.ROLE_MANAGER, gb.ROLE_CXO, gb.ROLE_CEO]:
                    # 適応力50なら0.1 -> base_growth * 2
                    new_exec = min(gb.ABILITY_MAX, npc['executive'] + (base_growth * 2))
                    updates.append("executive = ?")
                    params.append(new_exec)
                
                # 業界適性
                current_apt = npc['industry_aptitude']
                if current_apt < 2.0:
                    speed_factor = npc['adaptability'] / 50.0
                    if current_apt < 1.0:
                        apt_growth = (0.9 / 13.0) * speed_factor
                    else:
                        apt_growth = (1.0 / 260.0) * speed_factor
                    
                    new_apt = min(2.0, current_apt + apt_growth)
                    updates.append("industry_aptitude = ?")
                    params.append(new_apt)

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
                    updates.append("desired_salary = ?")
                    params.append(new_desired)
                    
                    if new_desired > npc['salary'] * 1.1:
                        self.log_news(week, comp['id'], f"{npc['name']} が昇給を希望しています (希望: ¥{new_desired:,})", 'info')

                # 4. 離職判定 (Turnover)
                # 忠誠度が40を下回ると離職リスク発生
                if new_loyalty < 40:
                    # 忠誠度 0 で 20%、40 で 0% の確率
                    resign_prob = (40 - new_loyalty) * 0.005
                    if random.random() < resign_prob:
                        # 離職実行 (会社ID等をNULLにして労働市場へ戻す)
                        updates.extend(["company_id = NULL", "department = NULL", "role = NULL", "loyalty = 50", "last_resigned_week = ?", "last_company_id = ?"])
                        params.extend([week, comp['id']])
                        self.log_news(week, comp['id'], f"従業員 {npc['name']} が退職しました。", 'warning')
                        db.log_file_event(week, comp['id'], "HR Resignation", f"{npc['name']} resigned")

                updates.append("loyalty = ?")
                params.append(new_loyalty)
                
                if updates:
                    sql = f"UPDATE npcs SET {', '.join(updates)} WHERE id = ?"
                    params.append(npc['id'])
                    updates_to_run.append((sql, tuple(params)))

                # 3. 給与支払い
                weekly_salary = (npc['salary'] * gb.NPC_SCALE_FACTOR) / gb.WEEKS_PER_YEAR_REAL
                
                dept = npc['department']
                cat = 'labor'
                if dept == gb.DEPT_PRODUCTION: cat = 'labor_production'
                elif dept == gb.DEPT_STORE: cat = 'labor_store'
                elif dept == gb.DEPT_SALES: cat = 'labor_sales'
                elif dept == gb.DEPT_DEV: cat = 'labor_dev'
                elif dept == gb.DEPT_HR: cat = 'labor_hr'
                elif dept == gb.DEPT_PR: cat = 'labor_pr'
                elif dept == gb.DEPT_ACCOUNTING: cat = 'labor_accounting'
                
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
                
                # 減衰率の計算: 基本値 + (能力による緩和)
                # PR 0: 0.90 (10%減)
                # PR 50: 0.90 + 0.05 = 0.95 (5%減)
                # PR 100: 0.90 + 0.10 = 1.00 (減衰なし)
                brand_decay = min(1.0, gb.BRAND_DECAY_BASE + (pr_power * gb.PR_MITIGATION_FACTOR))
                awareness_decay = min(1.0, gb.AWARENESS_DECAY_BASE + (pr_power * gb.PR_MITIGATION_FACTOR))
                
                cursor.execute("UPDATE companies SET brand_power = brand_power * ? WHERE id = ?", (brand_decay, cid))
                cursor.execute("UPDATE product_designs SET awareness = awareness * ? WHERE company_id = ?", (awareness_decay, cid))

    def process_development(self, week):
        """
        開発中のプロジェクトを進捗させ、完了時にステータスを確定する
        """
        developing_projects = db.fetch_all("SELECT * FROM product_designs WHERE status = 'developing'")
        
        for proj in developing_projects:
            start_week = proj['developed_week']
            if week - start_week >= gb.DEVELOPMENT_DURATION:
                # 開発完了処理
                company_id = proj['company_id']
                
                # 企業の開発力を計算
                caps = self.calculate_capabilities(company_id)
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
                        db.execute_query("UPDATE npcs SET company_id = NULL, department = NULL, role = NULL WHERE company_id = ?", (comp['id'],))
                        
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
