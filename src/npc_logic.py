# c:\0124newSIm\npc_logic.py
# NPC企業および個人の意思決定ロジック

import json
import math
import random
from database import db
import gamebalance as gb
import name_generator

class NPCLogic:
    def __init__(self, company_id, company_data=None, employees=None):
        self.company_id = company_id
        if company_data:
            self.company = company_data
        else:
            self.company = db.fetch_one("SELECT * FROM companies WHERE id = ?", (company_id,))

        if not self.company: 
            self.employees = []
            return

        if employees is not None:
            self.employees = employees
        else:
            # フォールバック
            self.employees = db.fetch_all("SELECT * FROM npcs WHERE company_id = ?", (self.company_id,))

    def _get_ceo_precision(self, stat_name):
        """
        CEOの能力値に基づいて、意思決定の精度（0.0 ~ 1.0）を返す。
        役員適正(executive)を重めに評価する (3:7)。
        CEO不在の場合は低精度(0.3)を返す。
        """
        ceo = next((e for e in self.employees if e['role'] == gb.ROLE_CEO), None)
        if not ceo:
            return 0.3
        
        # sqlite3.Rowは.get()を持たないため、辞書形式でアクセスする
        ability = ceo[stat_name] if stat_name in ceo.keys() else 0
        executive = ceo['executive'] if 'executive' in ceo.keys() else 0
        
        # 役員能力重め (3:7)
        weighted_score = (ability * 0.3) + (executive * 0.7)
        
        # 0-100 -> 0.0-1.0
        return min(1.0, max(0.0, weighted_score / 100.0))

    def _calculate_weekly_fixed_costs(self):
        """固定費（人件費、家賃、金利）の週次合計を算出"""
        # Labor
        labor = sum(e['salary'] * gb.NPC_SCALE_FACTOR for e in self.employees) / gb.WEEKS_PER_YEAR_REAL
        
        # Rent
        res_rent = db.fetch_one("SELECT SUM(rent) as total FROM facilities WHERE company_id = ? AND is_owned = 0", (self.company_id,))
        rent = res_rent['total'] or 0
        
        # Interest
        res_loan = db.fetch_one("SELECT SUM(amount * interest_rate) as total FROM loans WHERE company_id = ?", (self.company_id,))
        interest = (res_loan['total'] or 0) / 52.0
        
        return int(labor + rent + interest)

    def decide_financing(self, current_week):
        """
        資金調達: 運転資金が心許ない場合、借入を行う
        """
        # 安全マージン（例: 2億円）
        SAFETY_MARGIN = 200000000
        
        if self.company['funds'] < SAFETY_MARGIN:
            # 借入可能額を確認
            current_loans = db.fetch_one("SELECT SUM(amount) as total FROM loans WHERE company_id = ?", (self.company_id,))
            total_loans = current_loans['total'] if current_loans['total'] else 0
            
            limit = self.company['borrowing_limit']
            borrowable = limit - total_loans
            
            if borrowable > 0:
                # 不足分 + マージンを借りる
                target_amount = (SAFETY_MARGIN - self.company['funds']) + 100000000
                amount = min(borrowable, target_amount)
                
                # 金利決定 (格付けが高いほど低い)
                rating = self.company['credit_rating']
                rate = gb.INTEREST_RATE_MAX - ((rating / 100.0) * (gb.INTEREST_RATE_MAX - gb.INTEREST_RATE_MIN))
                rate = max(gb.INTEREST_RATE_MIN, rate)

                db.execute_query("INSERT INTO loans (company_id, amount, interest_rate, remaining_weeks) VALUES (?, ?, ?, ?)",
                                 (self.company_id, int(amount), rate, gb.LOAN_TERM_WEEKS))
                db.execute_query("UPDATE companies SET funds = funds + ? WHERE id = ?", (int(amount), self.company_id))
                db.log_file_event(current_week, self.company_id, "Financing", f"Borrowed {amount} yen")

    def decide_salary(self, current_week):
        """
        給与査定: 希望給与と現在給与に乖離がある従業員に対し、予算の範囲内で昇給を行う
        """
        # 予算に余裕があるか (運転資金として最低限確保したい額を除く)
        if self.company['funds'] < 50000000: return

        for emp in self.employees:
            if emp['desired_salary'] > emp['salary']:
                # 昇給判断ロジック
                # 1. 忠誠度が低い、または役職が高い場合は優先的に昇給
                # 2. 乖離が小さい場合は調整しやすい
                
                is_important = emp['role'] in [gb.ROLE_MANAGER, gb.ROLE_CXO, gb.ROLE_CEO]
                is_risk = emp['loyalty'] < 40
                
                if is_important or is_risk or random.random() < 0.3:
                    new_salary = emp['desired_salary']
                    db.execute_query("UPDATE npcs SET salary = ? WHERE id = ?", (new_salary, emp['id']))
                    db.log_file_event(current_week, self.company_id, "HR Salary", f"Increased salary for {emp['name']} to {new_salary}")

    def decide_hiring(self, current_week, candidates_pool=None):
        """
        採用計画: 資金に余裕があり、従業員が不足していればオファーを出す
        """
        if not self.company: return
        
        # 既にオファーを出している件数を確認
        current_offers_cnt = db.fetch_one("SELECT COUNT(*) as cnt FROM job_offers WHERE company_id = ?", (self.company_id,))['cnt']
        offers_to_make = 3 - current_offers_cnt
        
        if offers_to_make <= 0: return

        # 今回のループでオファーを出したNPCのIDを記録（重複防止）
        offered_npc_ids = set()
        
        # 1. 人事キャパシティと従業員数のチェック
        hr_employees = [e for e in self.employees if e['department'] == gb.DEPT_HR]
        total_hr_power = sum([e['hr'] for e in hr_employees])
        # 人事部が0人の場合でも最低限の採用活動ができるように補正
        hr_capacity = max(total_hr_power * gb.HR_CAPACITY_PER_PERSON, 5) # HR_CAPACITY_PER_PERSON is per NPC? No, per person.
        
        current_employees_count = len(self.employees)
        
        # 採用ターゲット部署の決定
        target_dept = None
        if current_employees_count >= hr_capacity * 0.9:
            # キャパシティ限界に近い場合は人事部を優先
            # Note: hr_capacity is raw number of people manageable. current_employees_count is NPCs.
            # We need to compare scaled count.
            if (current_employees_count * gb.NPC_SCALE_FACTOR) >= hr_capacity * 0.9:
                target_dept = gb.DEPT_HR
        else:
            # 業態ごとの優先順位
            if self.company['type'] == 'npc_maker':
                # メーカー: 生産 > 開発 > 営業
                prod_count = len([e for e in self.employees if e['department'] == gb.DEPT_PRODUCTION])
                if prod_count < 5: target_dept = gb.DEPT_PRODUCTION
                elif prod_count < 10: target_dept = gb.DEPT_DEV
                else: target_dept = gb.DEPT_SALES
            elif self.company['type'] == 'npc_retail':
                # 小売: 店舗 > 営業
                store_count = len([e for e in self.employees if e['department'] == gb.DEPT_STORE])
                total_count = len(self.employees)
                # 従業員の8割以上は店舗スタッフであるべき
                if store_count < total_count * 0.8: target_dept = gb.DEPT_STORE
                else: target_dept = gb.DEPT_SALES
        
        if not target_dept:
            target_dept = random.choice(gb.DEPARTMENTS)

        # 2. 予算チェック (年収の2倍程度の余裕があるか)
        if self.company['funds'] > gb.BASE_SALARY_YEARLY * gb.NPC_SCALE_FACTOR * 2:
            # 人事能力による誤差範囲の計算
            # 人事力100 -> 誤差4, 人事力0 -> 誤差40
            avg_hr = sum([e['hr'] for e in hr_employees]) / len(hr_employees) if hr_employees else 0
            error_range = 40 - (36 * (min(100, avg_hr) / 100.0))
            half_range = error_range / 2.0
            
            # CEOの判断精度 (人事能力 + 役員適正)
            ceo_precision = self._get_ceo_precision('hr')
            
            # 候補者リストが渡されていない場合はDBから取得 (フォールバック)
            if candidates_pool is None:
                candidates_pool = db.fetch_all("SELECT * FROM npcs WHERE company_id IS NULL LIMIT 100")

            # 自社に再雇用禁止期間中の候補者を除外
            filtered_candidates = [
                c for c in candidates_pool 
                if c['last_company_id'] != self.company_id or (current_week - c['last_resigned_week']) >= gb.REHIRE_PROHIBITION_WEEKS
            ]

            # 最大3回まで繰り返し採用を試みる
            for _ in range(offers_to_make):
                best_candidate = None
                best_score = -1

                for cand in filtered_candidates:
                    # 今回のループで既にオファー済みならスキップ
                    if cand['id'] in offered_npc_ids:
                        continue

                    # 能力値をファジー化して認知
                    # 毎回ランダムだと評価がぶれるので、週とIDでシード固定してもいいが、
                    # ここでは「面接のたびに印象が変わる」としてランダムにする
                    perceived_stats = {}
                    for stat in ['production', 'development', 'sales', 'hr', 'store_ops']:
                        perceived_stats[stat] = cand[stat] + random.uniform(-half_range, half_range)

                    # ターゲット部署の能力値で評価
                    stat_val = 0
                    if target_dept == gb.DEPT_PRODUCTION: stat_val = perceived_stats['production']
                    elif target_dept == gb.DEPT_DEV: stat_val = perceived_stats['development']
                    elif target_dept == gb.DEPT_SALES: stat_val = perceived_stats['sales']
                    elif target_dept == gb.DEPT_HR: stat_val = perceived_stats['hr']
                    elif target_dept == gb.DEPT_STORE: stat_val = perceived_stats['store_ops']
                    else: stat_val = max(perceived_stats.values())
                    
                    # ROI (能力/給与) でスコアリング
                    # 相手の希望給与を見る
                    desired = cand['desired_salary']
                    if desired == 0: desired = cand['salary'] # 未設定なら前職給与
                    if desired == 0: desired = gb.BASE_SALARY_YEARLY # それでもなければ基準値
                    
                    # コスパ計算のブレ: CEOの能力が低いと、実際のコスパを見誤る
                    # 精度が高いほどブレ幅(noise_range)は小さい
                    noise_range = 0.4 * (1.0 - ceo_precision) # 最大±40%
                    evaluation_noise = random.uniform(1.0 - noise_range, 1.0 + noise_range)
                    score = (stat_val / desired) * evaluation_noise
                    
                    if score > best_score:
                        best_score = score
                        best_candidate = cand
                
                if best_candidate:
                    # オファー発行
                    # 提示額は希望額通りとする（交渉ロジックは今後）
                    offer_salary = best_candidate['desired_salary'] if best_candidate['desired_salary'] > 0 else gb.BASE_SALARY_YEARLY
                    
                    db.execute_query("INSERT INTO job_offers (week, company_id, npc_id, offer_salary, target_dept) VALUES (?, ?, ?, ?, ?)",
                                     (current_week, self.company_id, best_candidate['id'], offer_salary, target_dept))
                    
                    db.log_file_event(current_week, self.company_id, "HR Hiring Offer", f"Offered {offer_salary} yen to {best_candidate['name']} (ID: {best_candidate['id']})")
                    offered_npc_ids.add(best_candidate['id'])
                else:
                    break # 候補者がいなければ終了

    def decide_promotion(self, current_week):
        """
        人事異動: 
        1. 部長(Manager)が不在の部署があれば、適任者を昇進させる
        2. CxOが不在で、優秀な部長がいれば昇進させる
        """
        if not self.employees: return

        # 部署ごとに整理
        dept_members = {d: [] for d in gb.DEPARTMENTS}
        dept_managers = {d: [] for d in gb.DEPARTMENTS}
        dept_cxos = {d: [] for d in gb.DEPARTMENTS}
        
        for e in self.employees:
            if e['department'] in dept_members:
                if e['role'] == gb.ROLE_MANAGER:
                    dept_managers[e['department']].append(e)
                elif e['role'] == gb.ROLE_CXO:
                    dept_cxos[e['department']].append(e)
                elif e['role'] == gb.ROLE_MEMBER:
                    dept_members[e['department']].append(e)
        
        for dept in gb.DEPARTMENTS:
            # 部長が不在かつ、メンバーがいる場合
            if not dept_managers[dept] and dept_members[dept]:
                # マネジメント能力と適応力の合計が高い順に候補選定
                candidates = dept_members[dept]
                candidates.sort(key=lambda x: x['management'] + x['adaptability'], reverse=True)
                
                best = candidates[0]
                db.execute_query("UPDATE npcs SET role = ? WHERE id = ?", (gb.ROLE_MANAGER, best['id']))
                db.log_file_event(current_week, self.company_id, "HR Promotion", f"Promoted {best['name']} to Manager")
            
            # CxOが不在かつ、部長がいる場合
            if not dept_cxos[dept] and dept_managers[dept]:
                # 役員適正が高い順
                candidates = dept_managers[dept]
                candidates.sort(key=lambda x: x['executive'], reverse=True)
                
                best = candidates[0]
                # 役員適正が一定以上ならCxOへ昇進
                if best['executive'] >= 40:
                    db.execute_query("UPDATE npcs SET role = ? WHERE id = ?", (gb.ROLE_CXO, best['id']))
                    db.log_file_event(current_week, self.company_id, "HR Promotion", f"Promoted {best['name']} to CxO")

    def decide_production(self, current_week, designs, inventory, b2b_sales_history, market_total_sales_4w, economic_index):
        """
        メーカー用: 生産計画
        在庫が少なければ生産を行う
        """
        if self.company['type'] != 'npc_maker': return

        # 完了済みの設計書のみフィルタ
        designs = [d for d in designs if d['status'] == 'completed']
        if not designs: return

        # 生産能力の算出 (週あたりの生産可能台数)
        # 1. 施設容量チェック
        facilities = db.fetch_all("SELECT size FROM facilities WHERE company_id = ? AND type = 'factory'", (self.company_id,))
        total_factory_size = sum(f['size'] for f in facilities)
        
        # フォールバック: 施設データがない場合でも、生産部員がいれば最低限(10)のキャパシティがあるとみなす
        if total_factory_size == 0:
            total_factory_size = 10

        # 2. 従業員取得と有効稼働数の計算
        prod_employees = [e for e in self.employees if e['department'] == gb.DEPT_PRODUCTION]
        
        # 能力が高い順に工場に入れる（あふれた従業員は生産に寄与しない）
        prod_employees.sort(key=lambda x: x['production'], reverse=True)
        effective_employees = prod_employees[:int(total_factory_size // gb.NPC_SCALE_FACTOR)]

        total_capacity = 0
        for emp in effective_employees:
            # 能力50で基準効率(0.17台)が出る計算
            total_capacity += (emp['production'] / 50.0) * gb.BASE_PRODUCTION_EFFICIENCY * gb.NPC_SCALE_FACTOR
        
        for design in designs:
            # キャパシティが1台分未満でも、確率的に1台作れるようにする（あるいは最低1台は作れるようにする）
            if total_capacity <= 0.1: break

            # 在庫確認
            stock_item = next((inv for inv in inventory if inv['design_id'] == design['id']), None)
            current_stock = stock_item['quantity'] if stock_item else 0

            # --- 生産意思決定ロジックの高度化 ---
            
            # 1. 自社の直近4週間のB2B出荷実績
            sales_history_item = next((s for s in b2b_sales_history if s['seller_id'] == self.company_id and s['design_id'] == design['id']), None)
            total_sales_4w = sales_history_item['total'] if sales_history_item else 0
            
            # 2. 市場環境の把握
            # アクティブなメーカー数
            maker_count = db.fetch_one("SELECT COUNT(*) as cnt FROM companies WHERE type IN ('player', 'npc_maker') AND is_active = 1")['cnt']
            maker_count = max(1, maker_count)
            
            # 3. シェア率と予測需要の算出
            if market_total_sales_4w > 0:
                current_share = total_sales_4w / market_total_sales_4w
            else:
                # 統計データがない初期は均等割と仮定
                current_share = 1.0 / maker_count
            
            # 需要予測のブレ: CEOの生産能力と役員適正に依存
            ceo_precision = self._get_ceo_precision('production')
            error_range = 0.3 * (1.0 - ceo_precision) # 最大±30%
            prediction_error = random.uniform(1.0 - error_range, 1.0 + error_range)
            estimated_market_demand = gb.BASE_MARKET_DEMAND * economic_index * prediction_error
            
            # 目標シェア: 現状維持～微増を目指す (最低でも5%は確保しようとする)
            target_share = max(current_share * 1.05, 0.05)
            
            # 予測週販
            predicted_weekly_sales = estimated_market_demand * target_share

            # 4. 目標在庫の設定 (予測週販の2.5週分を確保 - 過剰在庫抑制のため短縮)
            target_stock = int(predicted_weekly_sales * 2.5)
            
            # 最低在庫保証 (不測の事態に備えて最低でも需要の数%程度は持つ)
            min_stock = int(gb.BASE_MARKET_DEMAND / maker_count * 0.25)
            target_stock = max(target_stock, min_stock)
            
            # 最大在庫キャップ (市場総需要の50%を上限とする - 1社での抱え込み防止)
            max_stock_cap = int(estimated_market_demand * 0.5)
            target_stock = min(target_stock, max_stock_cap)

            # 売れ行き不振時の生産抑制: 在庫があるのに直近4週で売れていないなら生産しない
            # ただし、ゲーム開始直後(Week 8未満)は実績がなくて当然なのでスキップ
            if current_week > 8 and current_stock > 10 and total_sales_4w == 0:
                target_stock = 0
            
            if current_stock >= target_stock: continue
            
            needed = target_stock - current_stock
            
            # 設計書の生産効率係数を適用
            design_eff = design['production_efficiency']
            
            # 生産可能数: キャパシティ * 効率
            # 端数は確率的に切り上げ (例: 9.8台作れる能力なら80%の確率で10台、20%で9台)
            float_produce = total_capacity * design_eff
            max_produce = int(float_produce)
            if random.random() < (float_produce - max_produce):
                max_produce += 1
            
            to_produce = min(needed, max_produce)
            
            # 資金チェック (材料費)
            if design['parts_config']:
                p_conf = json.loads(design['parts_config'])
                material_cost = sum(p['cost'] for p in p_conf.values())
            else:
                material_cost = gb.TOTAL_MATERIAL_COST
            total_cost = to_produce * material_cost
            
            # 資金チェック: 固定費4週分は残す
            fixed_costs = self._calculate_weekly_fixed_costs()
            available_funds = max(0, self.company['funds'] - (fixed_costs * 4))
            
            if available_funds < total_cost:
                to_produce = int(available_funds / material_cost)
                total_cost = to_produce * material_cost
            
            if to_produce > 0:
                # 生産実行 (資金消費と在庫増加)
                db.execute_query("UPDATE companies SET funds = funds - ? WHERE id = ?", (total_cost, self.company_id))
                db.execute_query("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, 'material', ?)",
                                 (current_week, self.company_id, total_cost))
                
                if stock_item:
                    db.execute_query("UPDATE inventory SET quantity = quantity + ? WHERE id = ?", (to_produce, stock_item['id']))
                else:
                    db.execute_query("INSERT INTO inventory (company_id, design_id, quantity, sales_price) VALUES (?, ?, ?, ?)", 
                                     (self.company_id, design['id'], to_produce, design['sales_price']))
                
                # キャパシティ消費 (簡易的に、この製品に全力を注いだ分を減算)
                used_capacity = to_produce / design_eff if design_eff > 0 else 0
                total_capacity -= used_capacity
                db.log_file_event(current_week, self.company_id, "Production", f"Produced {to_produce} units of {design['name']}")
                db.increment_weekly_stat(current_week, self.company_id, 'production_ordered', to_produce)
                db.increment_weekly_stat(current_week, self.company_id, 'production_completed', to_produce)

    def decide_procurement(self, current_week, maker_stocks, my_capabilities, all_capabilities, my_inventory):
        """
        小売用: 仕入れ計画
        """
        if self.company['type'] != 'npc_retail': return
        
        # 予算設定: 固定費4週分を確保し、残りの90%を仕入れ予算とする
        fixed_costs = self._calculate_weekly_fixed_costs()
        reserved_funds = fixed_costs * 4
        budget = max(0, (self.company['funds'] - reserved_funds) * 0.9)

        if not maker_stocks: return

        # all_capabilities は事前計算済み

        # CEOの目利き精度 (営業能力 + 役員適正)
        ceo_precision = self._get_ceo_precision('sales')

        # 商品スコアリング (コンセプト * ブランド / 価格)
        scored_items = []
        for item in maker_stocks:
            # 営業力による価格補正の計算
            # メーカーの営業力が高いと、仕入れ値が高くなる（値引きを引き出せない）
            maker_caps = all_capabilities.get(item['maker_id'], {'sales': 50})
            sales_power = maker_caps['sales']
            
            # 営業力による「認知・信頼スコア」 (Visibility/Trust)
            # 営業力が低いと、そもそも商品を知ってもらえない、あるいは信頼されない
            # 0 -> 0.2 (激減), 50 -> 0.7, 100 -> 1.2 (ボーナス)
            sales_visibility = 0.2 + (sales_power / 100.0)

            # 営業力50を基準に、1ポイントあたり0.2%価格変動
            price_multiplier = 1.0 + (sales_power - 50) * 0.002
            # 卸値の基準はMSRPの90% (小売取り分10%)
            wholesale_base = max(1, item['sales_price'] * 0.9) # 0円防止
            actual_price = int(wholesale_base * price_multiplier)
            
            price_factor = actual_price / 3000000.0 # 300万を基準に正規化
            if price_factor <= 0: price_factor = 0.1 # ゼロ除算防止
            
            # 評価のブレ: CEOの能力が低いと商品の価値を見誤る
            noise_range = 0.3 * (1.0 - ceo_precision) # 最大±30%
            perception_noise = random.uniform(1.0 - noise_range, 1.0 + noise_range)
            
            # 直感・相性 (Gut Feeling): 数値化できない相性や営業担当の印象など
            gut_feeling = random.uniform(0.9, 1.1)
            
            score = ((item['concept_score'] * (1 + item['brand_power'] / 100.0)) / price_factor) * sales_visibility * perception_noise * gut_feeling
            scored_items.append({**item, 'score': score, 'actual_price': actual_price})
        
        # スコア順にソート
        # 同スコア時の順序をランダムにするため、先にシャッフルしておく
        random.shuffle(scored_items)
        scored_items.sort(key=lambda x: x['score'], reverse=True)

        # --- 仕入れロジック改善 ---
        # 1. 自社の販売キャパシティと現在庫の確認 (my_capabilitiesは事前計算済み)
        sales_capacity = my_capabilities['store_throughput']
        
        # 2. 目標在庫の設定 (販売キャパシティの6週分 - 機会損失を防ぐため多めに確保)
        target_stock_total = sales_capacity * 6
        current_total_stock = sum(inv['quantity'] for inv in my_inventory)
        
        # 3. 必要仕入れ数の計算
        needed_total = target_stock_total - current_total_stock
        if needed_total <= 0: return

        # 4. 予算と必要数に応じて仕入れ実行
        total_score = sum(i['score'] for i in scored_items)
        
        # シェア計算用に初期必要数を保持
        initial_needed_total = needed_total

        for item in scored_items:
            if budget <= 0 or needed_total <= 0: break

            # 買付数の決定
            # 人気(スコア)に応じて多めに仕入れる
            share = item['score'] / total_score if total_score > 0 else (1.0 / len(scored_items))
            # 必要数のシェア分を仕入れる (残数ではなく初期必要数をベースにする)
            float_buy_qty = initial_needed_total * share
            ideal_buy_qty = int(float_buy_qty)
            if random.random() < (float_buy_qty - ideal_buy_qty):
                ideal_buy_qty += 1
            
            # 予算から買える数を計算
            qty_by_budget = int(budget / item['actual_price']) if item['actual_price'] > 0 else 0
            
            # 最終的な購入数は、理想数、メーカー在庫、予算上限、残り必要数の最小値
            buy_qty = min(ideal_buy_qty, item['quantity'], qty_by_budget, int(needed_total))
            
            if buy_qty <= 0: continue
            
            cost = buy_qty * item['actual_price']
                
            # 発注 (B2B Orders)
            db.execute_query("""
                INSERT INTO b2b_orders (week, buyer_id, seller_id, design_id, quantity, amount, status)
                VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """, (current_week, self.company_id, item['maker_id'], item['design_id'], buy_qty, cost))
            
            db.log_file_event(current_week, self.company_id, "B2B Order", f"Ordered {buy_qty} units from Maker ID {item['maker_id']} for {cost} yen")
            
            # 売り手（プレイヤー等）にも通知を出す
            db.execute_query("INSERT INTO news_logs (week, company_id, message, type) VALUES (?, ?, ?, ?)",
                             (current_week, item['maker_id'], f"{self.company['name']} から {buy_qty}台 の注文が入りました (営業画面で確認してください)", 'info'))
            
            budget -= cost
            needed_total -= buy_qty

    def decide_development(self, current_week, designs):
        """
        メーカー用: 商品開発計画
        """
        if self.company['type'] != 'npc_maker': return

        # 開発中のプロジェクトがあるか確認
        is_developing = any(d['status'] == 'developing' for d in designs)
        if is_developing: return # 同時開発は1本まで（簡易化）

        # 既存の製品数を確認
        completed_count = sum(1 for d in designs if d['status'] == 'completed')
        
        # 製品が2つ未満、またはランダム（新陳代謝）で新規開発
        if completed_count < 2 or random.random() < 0.05:
            # コンセプト決定 (1.0 - 5.0)
            # 企業の得意分野などがまだないのでランダム
            # コンセプトスコア等は開発完了時にStrategyに基づいて決定するため、ここでは仮置き
            
            # サプライヤー選択
            parts_config = {}
            total_score = 0
            parts_def = gb.INDUSTRIES[gb.CURRENT_INDUSTRY]['parts']
            
            for part in parts_def:
                suppliers = db.fetch_all("SELECT id, trait_material_score, trait_cost_multiplier FROM companies WHERE type = 'system_supplier' AND part_category = ?", (part['key'],))
                supplier = random.choice(suppliers)
                
                # 部品調達のブレ (Quality/Cost Fluctuation): ロット差や交渉による変動 (±10%)
                quality_fluctuation = random.uniform(0.90, 1.10)
                cost_fluctuation = random.uniform(0.90, 1.10)
                p_score = supplier['trait_material_score'] * quality_fluctuation
                p_cost = int(part['base_cost'] * supplier['trait_cost_multiplier'] * cost_fluctuation)

                parts_config[part['key']] = {
                    "supplier_id": supplier['id'],
                    "score": p_score,
                    "cost": p_cost
                }
                total_score += p_score
            
            avg_material_score = total_score / len(parts_def)
            
            # 開発方針をランダムに決定
            strategy = random.choice(list(gb.DEV_STRATEGIES.keys()))

            name = name_generator.generate_product_name(strategy)
            
            # DBに登録 (status='developing')
            # base_price, sales_price は完成時に確定するため仮置き
            db.execute_query("""
                INSERT INTO product_designs 
                (company_id, name, material_score, concept_score, production_efficiency, base_price, sales_price, status, strategy, developed_week, parts_config)
                VALUES (?, ?, ?, 0, 0, 0, 0, 'developing', ?, ?, ?)
            """, (self.company_id, name, avg_material_score, strategy, current_week, json.dumps(parts_config)))
            db.log_file_event(current_week, self.company_id, "Development Start", f"Started development of {name}")
            db.increment_weekly_stat(current_week, self.company_id, 'development_ordered', 1)

    def decide_order_fulfillment(self, current_week, orders, inventory):
        """
        メーカー用: 受注処理
        届いている注文を確認し、在庫があれば受注(Accepted)する
        """
        if self.company['type'] != 'npc_maker': return

        if not orders: return

        # 在庫情報の取得 (処理中に減算していくため辞書で管理)
        inventory_map = {}
        for s in inventory:
            inventory_map[s['design_id']] = s['quantity']

        for order in orders:
            did = order['design_id']
            qty = order['quantity']
            
            current_stock = inventory_map.get(did, 0)
            
            if current_stock >= qty:
                # 受注可能
                db.execute_query("UPDATE b2b_orders SET status = 'accepted' WHERE id = ?", (order['id'],))
                # 仮押さえ (シミュレーションのB2B処理フェーズで正式に引かれるが、二重受注を防ぐためここでも計算上の在庫を減らす)
                inventory_map[did] -= qty
                db.log_file_event(current_week, self.company_id, "B2B Accept", f"Accepted Order ID {order['id']} ({qty} units)")
            else:
                # 在庫不足のため拒否 (または部分納品だが今回は拒否)
                # 待たせるとキリがないので即拒否
                db.execute_query("UPDATE b2b_orders SET status = 'rejected' WHERE id = ?", (order['id'],))
                db.log_file_event(current_week, self.company_id, "B2B Reject", f"Rejected Order ID {order['id']} (Insufficient Stock)")

    def decide_facilities(self, current_week):
        """
        施設管理: 従業員数に合わせて施設を確保する
        """
        # 部署ごとの従業員数を集計
        if not self.employees: return

        dept_counts = {}
        for emp in self.employees:
            d = emp['department']
            dept_counts[d] = dept_counts.get(d, 0) + 1

        # 必要な施設タイプと人数
        # 工場: 生産部
        factory_needs = dept_counts.get(gb.DEPT_PRODUCTION, 0)
        # 店舗: 店舗部
        store_needs = dept_counts.get(gb.DEPT_STORE, 0)
        # オフィス: その他全員
        office_needs = len(self.employees) - factory_needs - store_needs

        # 現在の施設容量を確認
        facilities = db.fetch_all("SELECT type, size FROM facilities WHERE company_id = ?", (self.company_id,))
        current_cap = {'factory': 0, 'store': 0, 'office': 0}
        for fac in facilities:
            if fac['type'] in current_cap:
                current_cap[fac['type']] += fac['size']

        # 不足分を計算して契約 (賃貸)
        def acquire_facility(ftype, needed, current, rent_unit_price):
            if needed > current:
                shortage = needed - current
                
                # 空き物件を探す (不足分を満たす最小の物件)
                available = db.fetch_one("""
                    SELECT id, rent, size FROM facilities 
                    WHERE company_id IS NULL AND type = ? AND size >= ? 
                    ORDER BY rent ASC LIMIT 1
                """, (ftype, shortage))
                
                if available:
                    # 購入判断: 資金に余裕があれば購入する
                    # 余裕基準: 購入後も資金が1億円以上残る
                    purchase_price = available['rent'] * gb.FACILITY_PURCHASE_MULTIPLIER
                    
                    if self.company['funds'] > purchase_price + 100000000:
                        # 購入
                        db.execute_query("UPDATE facilities SET company_id = ?, is_owned = 1 WHERE id = ?", (self.company_id, available['id']))
                        db.execute_query("UPDATE companies SET funds = funds - ? WHERE id = ?", (purchase_price, self.company_id))
                        db.execute_query("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, 'facility_purchase', ?)",
                                         (current_week, self.company_id, purchase_price))
                        db.log_file_event(current_week, self.company_id, "Facility", f"Purchased {ftype} (Size: {available['size']})")
                    else:
                        # 賃貸
                        db.execute_query("UPDATE facilities SET company_id = ?, is_owned = 0 WHERE id = ?", (self.company_id, available['id']))
                        db.log_file_event(current_week, self.company_id, "Facility", f"Rented {ftype} (Size: {available['size']})")

        acquire_facility('factory', factory_needs * gb.NPC_SCALE_FACTOR, current_cap['factory'], gb.RENT_FACTORY)
        acquire_facility('store', store_needs * gb.NPC_SCALE_FACTOR, current_cap['store'], gb.RENT_STORE_BASE)
        acquire_facility('office', office_needs * gb.NPC_SCALE_FACTOR, current_cap['office'], gb.RENT_OFFICE)

    def decide_advertising(self, current_week):
        """
        広告戦略: 資金に余裕があればブランド広告や商品広告を打つ
        """
        # 予算: 資金の5%程度
        budget = self.company['funds'] * 0.05
        if budget < gb.AD_COST_UNIT: return

        # 広報能力計算
        pr_employees = [e for e in self.employees if e['department'] == gb.DEPT_PR]
        if not pr_employees:
            pr_multiplier = 0.5 # 専門部署なしペナルティ
        else:
            avg_pr = sum(e['pr'] for e in pr_employees) / len(pr_employees)
            pr_multiplier = avg_pr / 50.0

        # 投資単位数
        units = int(budget / gb.AD_COST_UNIT)
        if units < 1: return
        
        spend_amount = units * gb.AD_COST_UNIT
        effect = units * gb.AD_EFFECT_BASE * pr_multiplier

        # 戦略決定
        # ブランド力が50未満ならブランド広告優先
        if self.company['brand_power'] < 50:
            db.execute_query("UPDATE companies SET funds = funds - ?, brand_power = brand_power + ? WHERE id = ?",
                             (spend_amount, effect, self.company_id))
            db.execute_query("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, 'ad', ?)",
                             (current_week, self.company_id, spend_amount))
            db.log_file_event(current_week, self.company_id, "Advertising", f"Brand Ad (Budget: {spend_amount})")
        else:
            # 認知度が低い最新商品をプッシュ
            target_product = db.fetch_one("""
                SELECT id, name FROM product_designs 
                WHERE company_id = ? AND status = 'completed' 
                ORDER BY developed_week DESC, awareness ASC LIMIT 1
            """, (self.company_id,))
            
            if target_product:
                db.execute_query("UPDATE companies SET funds = funds - ? WHERE id = ?", (spend_amount, self.company_id))
                db.execute_query("UPDATE product_designs SET awareness = awareness + ? WHERE id = ?", (effect * 2, target_product['id'])) # 商品広告は効果が出やすいとする
                db.execute_query("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, 'ad', ?)",
                                 (current_week, self.company_id, spend_amount))
                db.log_file_event(current_week, self.company_id, "Advertising", f"Product Ad for {target_product['name']} (Budget: {spend_amount})")

    def decide_pricing(self, current_week, designs, inventory, b2b_sales_history):
        """
        価格改定: 
        メーカー: MSRP(希望小売価格)を調整
        小売: 店頭販売価格を調整
        """
        if self.company['type'] == 'npc_maker':
            # 完了済みの設計書のみ
            completed_designs = [d for d in designs if d['status'] == 'completed' and d['company_id'] == self.company_id]
            
            # CEOの性格 (IDベースで固定の乱数シードを使用)
            # 1.0が標準。小さいほどせっかち（すぐ値下げ/値上げする）、大きいほどどっしり構える
            random.seed(self.company_id)
            patience = random.uniform(0.5, 1.5)
            # 価格改定の積極性 (1.0=標準, >1.0=大幅に変える)
            aggressiveness = random.uniform(0.8, 1.2)
            random.seed() # シードリセット

            for p in completed_designs:
                # 現在在庫
                stock_item = next((inv for inv in inventory if inv['design_id'] == p['id']), None)
                current_qty = stock_item['quantity'] if stock_item else 0
                
                # 直近4週間のB2B売上数
                sales_history_item = next((s for s in b2b_sales_history if s['seller_id'] == self.company_id and s['design_id'] == p['id']), None)
                sales_qty_4w = sales_history_item['total'] if sales_history_item else 0
                avg_sales_qty = sales_qty_4w / 4.0

                new_price = p['sales_price']
                
                # 閾値を性格で補正
                overstock_threshold = 50 * patience
                shortage_threshold = 10 / patience

                # ロジック: 在庫過多なら値下げ、品薄なら値上げ
                if current_qty > overstock_threshold and avg_sales_qty < (5 * patience):
                    # 基準価格(base_price)が原価ではないので、parts_configから原価を計算
                    p_conf = json.loads(p['parts_config']) if p['parts_config'] else {}
                    material_cost = sum(part['cost'] for part in p_conf.values()) if p_conf else 0
                    min_price = int(material_cost * gb.MIN_PROFIT_MARGIN)

                    # 値下げ幅に性格(aggressiveness)と揺らぎを加える
                    drop_rate = gb.PRICE_ADJUST_RATE * aggressiveness * random.uniform(0.8, 1.2)
                    proposed_price = int(p['sales_price'] * (1.0 - drop_rate))
                    new_price = max(min_price, proposed_price)
                elif current_qty < shortage_threshold and avg_sales_qty > (10 / patience):
                    raise_rate = gb.PRICE_ADJUST_RATE * aggressiveness * random.uniform(0.8, 1.2)
                    new_price = int(p['sales_price'] * (1.0 + raise_rate))
                
                if new_price != p['sales_price']:
                    db.execute_query("UPDATE product_designs SET sales_price = ? WHERE id = ?", (new_price, p['id']))
                    db.log_file_event(current_week, self.company_id, "Pricing", f"Changed MSRP of {p['name']} to {new_price}")

        elif self.company['type'] == 'npc_retail':
            # 小売: inventory の sales_price を更新
            # designsテーブルは全社分持っているので、design_idで引けるように辞書化
            all_designs_map = {d['id']: d for d in designs}

            for s in inventory:
                design = all_designs_map.get(s['design_id'])
                if not design: continue
                msrp = design['sales_price']

                # 基本戦略: MSRP通りに売る
                # 売れ残りが多い場合は値下げするなどのロジックをここに追加可能
                # 現状はMSRPに合わせる (メーカーが価格改定した場合に追従)
                if s['sales_price'] != msrp:
                    db.execute_query("UPDATE inventory SET sales_price = ? WHERE id = ?", (msrp, s['id']))
