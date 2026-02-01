# c:\0124newSIm\src\npc_logic.py
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
            
        # 事業部情報の取得
        self.divisions = db.fetch_all("SELECT * FROM divisions WHERE company_id = ?", (self.company_id,))
        
        self.phase = 'STABLE' # Default

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
        rent = db.fetch_one("SELECT SUM(rent) as total FROM facilities WHERE company_id = ? AND is_owned = 0", (self.company_id,))['total'] or 0
        # Interest
        interest = (db.fetch_one("SELECT SUM(amount * interest_rate) as total FROM loans WHERE company_id = ?", (self.company_id,))['total'] or 0) / 52.0
        return int(labor + rent + interest)

    def update_phase(self, current_week):
        """企業の現状分析を行い、フェーズを決定する"""
        old_phase = self.phase
        funds = self.company['funds']
        fixed_costs = self._calculate_weekly_fixed_costs()
        
        # 借入余力
        loans = db.fetch_one("SELECT SUM(amount) as total FROM loans WHERE company_id = ?", (self.company_id,))
        current_debt = loans['total'] or 0
        borrowing_limit = self.company['borrowing_limit']
        credit_room = borrowing_limit - current_debt
        
        # 直近の収益性 (4週間)
        recent_pl = db.fetch_one("""
            SELECT 
                SUM(CASE WHEN category = 'revenue' THEN amount ELSE 0 END) as revenue,
                SUM(CASE WHEN category IN ('cogs', 'labor', 'rent', 'ad', 'interest') THEN amount ELSE 0 END) as expenses
            FROM account_entries 
            WHERE company_id = ? AND week >= ?
        """, (self.company_id, current_week - 4))
        
        revenue = recent_pl['revenue'] or 0
        expenses = recent_pl['expenses'] or 0
        profit = revenue - expenses

        # フェーズ判定ロジック
        # CRISIS: 資金が固定費6週分未満、または借入余力がなく赤字
        if (funds + credit_room) < (fixed_costs * 6) or (funds < fixed_costs * 4 and profit < 0):
            self.phase = 'CRISIS'
        # GROWTH: 黒字かつ、資金に余裕がある (固定費12週分以上)
        elif profit > 0 and funds > (fixed_costs * 12):
            self.phase = 'GROWTH'
        else:
            self.phase = 'STABLE'
            
        if self.phase != old_phase:
             db.log_file_event(current_week, self.company_id, "Phase Change", f"Changed phase from {old_phase} to {self.phase}")

        # フェーズを統計情報として保存
        db.set_weekly_stat(current_week, self.company_id, 'phase', self.phase)

    def decide_financing(self, current_week):
        """
        資金調達: 運転資金が心許ない場合、借入を行う
        """
        fixed_costs = self._calculate_weekly_fixed_costs()
        
        # 目標とする手元資金 (フェーズによって変える)
        target_funds = fixed_costs * 12 # 標準は3ヶ月分
        if self.phase == 'CRISIS':
            target_funds = fixed_costs * 24 # 危機時は半年分確保したい
        elif self.phase == 'GROWTH':
            target_funds = fixed_costs * 8 # 成長期は投資に回すので手元は少なめで攻める

        # 最低ライン (これ割ったら絶対借りる)
        min_funds = fixed_costs * 4
        if self.phase == 'CRISIS':
            min_funds = fixed_costs * 8

        if self.company['funds'] < target_funds:
            # 借入可能額を確認
            current_loans = db.fetch_one("SELECT SUM(amount) as total FROM loans WHERE company_id = ?", (self.company_id,))
            total_loans = current_loans['total'] if current_loans['total'] else 0
            
            limit = self.company['borrowing_limit']
            borrowable = limit - total_loans
            borrow_threshold = 10000000 # 1000万単位
            
            # 借りるべき額
            needed = target_funds - self.company['funds']
            
            # 資金が最低ラインを割っている、またはCRISISなら積極的に借りる
            should_borrow = False
            if self.company['funds'] < min_funds: should_borrow = True
            if self.phase == 'CRISIS': should_borrow = True
            if self.phase == 'GROWTH' and needed > 0: should_borrow = True # 成長投資用

            if should_borrow and borrowable > borrow_threshold:
                amount = max(min(borrowable, needed), borrow_threshold)
                
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
        if self.phase == 'CRISIS': return # 危機時は昇給凍結

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
        
        # CRISISフェーズでは採用凍結
        if self.phase == 'CRISIS': return

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

        # ターゲット業界の特定 (最初の事業部の業界とする)
        target_industry = self.divisions[0]['industry_key'] if self.divisions else 'automotive'

        # GROWTHフェーズなら採用枠を増やす
        if self.phase == 'GROWTH':
            offers_to_make = min(5, offers_to_make + 2)

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

                    # 業界適性の取得
                    apts = json.loads(cand['aptitudes']) if cand['aptitudes'] else {}
                    apt_val = apts.get(target_industry, 0.1)
                    
                    # ターゲット部署の能力値 * 適性 で評価
                    stat_val = 0
                    if target_dept == gb.DEPT_PRODUCTION: stat_val = perceived_stats['production']
                    elif target_dept == gb.DEPT_DEV: stat_val = perceived_stats['development']
                    elif target_dept == gb.DEPT_SALES: stat_val = perceived_stats['sales']
                    elif target_dept == gb.DEPT_HR: stat_val = perceived_stats['hr']
                    elif target_dept == gb.DEPT_STORE: stat_val = perceived_stats['store_ops']
                    else: stat_val = max(perceived_stats.values())
                    
                    # 適性を反映
                    stat_val *= apt_val
                    
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

    def decide_restructuring(self, current_week):
        """
        リストラ策: CRISISフェーズで赤字の場合、人員削減や施設解約を行う
        """
        if self.phase != 'CRISIS': return
        
        # 従業員解雇 (能力が低く、給与が高い順)
        # 役員は除く
        candidates = [e for e in self.employees if e['role'] not in [gb.ROLE_CEO, gb.ROLE_CXO]]
        if not candidates: return

        # スコアリング (給与 / 能力平均) -> コスパが悪い順
        # 能力平均
        scored_candidates = []
        for emp in candidates:
            avg_stat = (emp['production'] + emp['sales'] + emp['development']) / 3
            fire_score = emp['salary'] / max(1, avg_stat)
            scored_candidates.append((fire_score, emp))
        
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        
        # 1週間に最大1人解雇
        target = scored_candidates[0][1]
        # 解雇実行
        db.execute_query("""
            UPDATE npcs SET company_id = NULL, department = NULL, role = NULL, 
            last_resigned_week = ?, last_company_id = ?, loyalty = 50 
            WHERE id = ?
        """, (current_week, self.company_id, target['id']))
        db.log_file_event(current_week, self.company_id, "Restructuring", f"Fired {target['name']} to cut costs")

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

        # 事業部ごとに処理
        for div in self.divisions:
            self._decide_production_for_division(current_week, designs, inventory, b2b_sales_history, market_total_sales_4w, economic_index, div)

    def _decide_production_for_division(self, current_week, designs, inventory, b2b_sales_history, market_total_sales_4w, economic_index, division):
        # 生産能力の算出 (週あたりの生産可能台数)
        # 1. 施設容量チェック
        facilities = db.fetch_all("SELECT size FROM facilities WHERE company_id = ? AND division_id = ? AND type = 'factory'", (self.company_id, division['id']))
        total_factory_size = sum(f['size'] for f in facilities)
        
        # フォールバック: 施設データがない場合でも、生産部員がいれば最低限(10)のキャパシティがあるとみなす
        if total_factory_size == 0:
            total_factory_size = 10

        # 2. 従業員取得と有効稼働数の計算
        prod_employees = [e for e in self.employees if e['department'] == gb.DEPT_PRODUCTION and e['division_id'] == division['id']]
        
        # 能力が高い順に工場に入れる（あふれた従業員は生産に寄与しない）
        prod_employees.sort(key=lambda x: x['production'], reverse=True)
        effective_employees = prod_employees[:int(total_factory_size // gb.NPC_SCALE_FACTOR)]

        total_capacity = 0
        for emp in effective_employees:
            # 能力50で基準効率(0.17台)が出る計算
            total_capacity += (emp['production'] / 50.0) * gb.BASE_PRODUCTION_EFFICIENCY * gb.NPC_SCALE_FACTOR
        
        # この事業部の製品のみ対象
        div_designs = [d for d in designs if d['division_id'] == division['id']]
        
        for design in div_designs:
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
            
            # カテゴリ需要の取得
            cat_key = design['category_key']
            
            # 市場トレンドから直近の需要を取得
            trend_data = db.fetch_one("SELECT b2c_demand FROM market_trends WHERE category_key = ? ORDER BY week DESC LIMIT 1", (cat_key,))
            if trend_data:
                base_demand_val = trend_data['b2c_demand']
            else:
                # データがない場合は定義値から計算
                base_demand_val = 1000 # fallback
                for ind in gb.INDUSTRIES.values():
                    if cat_key in ind['categories']:
                        base_demand_val = ind['categories'][cat_key]['base_demand']
                        break
                base_demand_val = int(base_demand_val * economic_index)
            
            estimated_market_demand = base_demand_val * prediction_error
            
            # 目標シェア: 現状維持～微増を目指す (最低でも5%は確保しようとする)
            # ブランド力による補正: 平均(50)より高ければ強気、低ければ弱気
            brand_factor = max(0.5, min(2.0, self.company['brand_power'] / 50.0))
            
            if current_share == 0:
                # 新規参入: 競合数で割ったシェア * ブランド力 * 0.5 (慎重に開始)
                target_share = (1.0 / max(1, maker_count)) * brand_factor * 0.5
            else:
                # 既存: 現状 * 成長係数
                growth_rate = 1.0 + (0.05 * brand_factor)
                target_share = current_share * growth_rate
            
            # シェア上限キャップ (過剰生産防止)
            target_share = min(0.4, max(0.01, target_share))
            
            # 予測週販
            predicted_weekly_sales = estimated_market_demand * target_share
            
            # 利益率による生産意欲の補正 (Profit Margin Motivation)
            # 利益率を計算
            if design['parts_config']:
                p_conf = json.loads(design['parts_config'])
                material_cost = sum(p['cost'] for p in p_conf.values())
            else:
                material_cost = design['sales_price'] * 0.7 # Fallback estimate
            
            profit_margin = (design['sales_price'] - material_cost) / design['sales_price'] if design['sales_price'] > 0 else 0
            # 利益率 20% を基準に、高ければ増産、低ければ減産 (0.5倍 ~ 1.5倍)
            profit_factor = max(0.5, min(1.5, 1.0 + (profit_margin - 0.2) * 2.5))
            
            predicted_weekly_sales = int(predicted_weekly_sales * profit_factor)

            # 4. 目標在庫の設定 (フェーズに応じて在庫水準を変える)
            # STABLE: 4週分, CRISIS: 2週分, GROWTH: 6週分
            weeks_stock = 4
            if self.phase == 'CRISIS': weeks_stock = 2
            elif self.phase == 'GROWTH': weeks_stock = 6
            
            target_stock = int(predicted_weekly_sales * weeks_stock)
            
            # 最低在庫保証 (不測の事態に備えて最低でも需要の数%程度は持つ)
            min_stock = int(estimated_market_demand / maker_count * 0.25)
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
                material_cost = design['sales_price'] * 0.7
            total_cost = to_produce * material_cost
            
            # 資金チェック: 固定費4週分は残す
            fixed_costs = self._calculate_weekly_fixed_costs()
            
            # 資金計算の改善: CRISIS時や在庫切れ時は、借入枠も含めて全力で生産する
            available_funds = max(0, self.company['funds'] - (fixed_costs * 4)) 
            if self.phase == 'CRISIS' or current_stock == 0:
                credit_room = self.company['borrowing_limit'] - (db.fetch_one("SELECT SUM(amount) as total FROM loans WHERE company_id = ?", (self.company_id,))['total'] or 0)
                available_funds = self.company['funds'] + credit_room

            if available_funds < total_cost and total_cost > 0:
                to_produce = int(available_funds / material_cost)
                total_cost = to_produce * material_cost
            
            if to_produce > 0:
                # 生産実行 (資金消費と在庫増加)
                db.execute_query("UPDATE companies SET funds = funds - ? WHERE id = ?", (total_cost, self.company_id))
                db.execute_query("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, 'material', ?)",
                                 (current_week, self.company_id, total_cost))
                
                # 資金がマイナスになった場合、即座に借入を実行して埋める (キャッシュ不足による倒産判定回避のため)
                if self.company['funds'] - total_cost < 0:
                    deficit = abs(self.company['funds'] - total_cost) + 10000000
                    db.execute_query("INSERT INTO loans (company_id, amount, interest_rate, remaining_weeks) VALUES (?, ?, ?, ?)",
                                     (self.company_id, deficit, 0.15, gb.LOAN_TERM_WEEKS)) # 緊急借入は金利高め
                    db.execute_query("UPDATE companies SET funds = funds + ? WHERE id = ?", (deficit, self.company_id))
                
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

    def decide_procurement(self, current_week, maker_stocks, my_capabilities, all_capabilities, my_inventory, on_order=None):
        """
        小売用: 仕入れ計画
        """
        if self.company['type'] != 'npc_retail': return
        
        # 予算設定
        fixed_costs = self._calculate_weekly_fixed_costs()
        reserved_funds = fixed_costs * 4
        budget = max(0, (self.company['funds'] - reserved_funds) * 0.9)

        if not maker_stocks: return

        # CRISIS時は予算制限を緩和 (売るものがないと死ぬ)
        if self.phase == 'CRISIS' and sum(i['quantity'] for i in my_inventory) < 10:
             loans = db.fetch_one("SELECT SUM(amount) as total FROM loans WHERE company_id = ?", (self.company_id,))
             credit_room = self.company['borrowing_limit'] - (loans['total'] or 0)
             budget = self.company['funds'] + credit_room

        # all_capabilities は事前計算済み

        # CEOの目利き精度 (営業能力 + 役員適正)
        ceo_precision = self._get_ceo_precision('sales')

        # カテゴリ需要のキャッシュ (DBアクセス削減)
        trends = db.fetch_all("SELECT category_key, b2c_demand FROM market_trends WHERE week = ?", (current_week - 1,))
        demand_map = {t['category_key']: t['b2c_demand'] for t in trends}

        # 商品スコアリング (コンセプト * ブランド / 価格)
        scored_items = []
        for item in maker_stocks:
            # 業界チェック: 自社の業界に含まれるカテゴリの商品のみ対象とする
            my_industry = self.company['industry']
            if my_industry in gb.INDUSTRIES:
                if item['category_key'] not in gb.INDUSTRIES[my_industry]['categories']:
                    continue

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
            # 価格正規化の基準を動的に設定 (カテゴリ平均価格などがあればベストだが、ここではMSRPベース)
            # ただし、同カテゴリ内での比較がしたいので、カテゴリごとの基準値が必要。
            # 簡易的に、item['base_price'] (価値基準) を使用する。
            base_val = item['base_price'] if item['base_price'] > 0 else actual_price
            
            price_factor = actual_price / base_val
            if price_factor <= 0: price_factor = 0.1 # ゼロ除算防止
            
            # 評価のブレ: CEOの能力が低いと商品の価値を見誤る
            noise_range = 0.3 * (1.0 - ceo_precision) # 最大±30%
            perception_noise = random.uniform(1.0 - noise_range, 1.0 + noise_range)
            
            # 直感・相性 (Gut Feeling): 数値化できない相性や営業担当の印象など
            gut_feeling = random.uniform(0.9, 1.1)
            
            # 利益率 (Retailer Margin)
            # 小売価格(MSRP) - 仕入れ値(actual_price)
            retail_margin = (item['sales_price'] - actual_price) / item['sales_price'] if item['sales_price'] > 0 else 0
            # 利益率によるスコア補正 (10%基準)
            margin_score = max(0.1, 1.0 + (retail_margin - 0.1) * 5.0)

            # カテゴリ需要 (Category Demand)
            cat_demand = demand_map.get(item['category_key'], 1000)
            # 需要1000を基準に正規化 (対数で緩やかに)
            demand_score = math.log10(max(10, cat_demand)) / 3.0 # log10(1000)=3 -> 1.0
            
            base_score = ((item['concept_score'] * (1 + item['brand_power'] / 100.0)) / price_factor)
            
            score = base_score * sales_visibility * perception_noise * gut_feeling * margin_score * demand_score
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
        
        # 3. 発注残の考慮
        on_order_qty = 0
        if on_order:
            on_order_qty = sum(o['quantity'] for o in on_order)
            
        # 4. 必要仕入れ数の計算 (目標 - 現在庫 - 発注残)
        needed_total = target_stock_total - current_total_stock - on_order_qty
        if needed_total <= 0: return

        # 5. 予算と必要数に応じて仕入れ実行
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

        if self.phase == 'CRISIS': return # 危機時は新規開発凍結

        # 事業部ごとに処理
        for div in self.divisions:
            self._decide_development_for_division(current_week, designs, div)

    def _decide_development_for_division(self, current_week, designs, division):
        # 開発中のプロジェクトがあるか確認
        is_developing = any(d['status'] == 'developing' and d['division_id'] == division['id'] for d in designs)
        if is_developing: return # 同時開発は1本まで（簡易化）

        # 既存の製品数を確認
        completed_count = sum(1 for d in designs if d['status'] == 'completed' and d['division_id'] == division['id'])
        
        # 製品が2つ未満、またはランダム（新陳代謝）で新規開発
        if completed_count < 2 or random.random() < 0.05:
            # コンセプト決定 (1.0 - 5.0)
            # 企業の得意分野などがまだないのでランダム
            # コンセプトスコア等は開発完了時にStrategyに基づいて決定するため、ここでは仮置き
            
            # サプライヤー選択
            # 事業部の業界・カテゴリ定義を取得
            ind_key = division['industry_key']
            
            # カテゴリ選定: 需給バランスと利益率に基づいて選定
            categories = gb.INDUSTRIES[ind_key]['categories']
            
            # 市場トレンド（需要）の取得
            trends = db.fetch_all("SELECT category_key, b2c_demand FROM market_trends WHERE week = ?", (current_week - 1,))
            demand_map = {t['category_key']: t['b2c_demand'] for t in trends}
            
            # 競合製品数（供給）の取得
            supply_counts = db.fetch_all("SELECT category_key, COUNT(*) as cnt FROM product_designs WHERE status = 'completed' GROUP BY category_key")
            supply_map = {s['category_key']: s['cnt'] for s in supply_counts}
            
            # 平均価格の取得（利益率計算用）
            avg_prices = db.fetch_all("SELECT category_key, AVG(sales_price) as avg_price FROM product_designs WHERE status = 'completed' GROUP BY category_key")
            price_map = {p['category_key']: p['avg_price'] for p in avg_prices}

            cat_keys = []
            weights = []

            for k, cat_def in categories.items():
                # 1. 需給バランス (Demand / Supply)
                demand = demand_map.get(k, cat_def['base_demand'])
                supply = supply_map.get(k, 0)
                # 供給が少ないほどチャンス。供給0ならボーナス(0.5で割る=2倍)。
                supply_factor = max(0.5, supply) 
                supply_demand_ratio = demand / supply_factor
                
                # 2. 推定利益率
                est_cost = sum(p['base_cost'] for p in cat_def['parts'])
                mkt_price = price_map.get(k, est_cost * 2.0) # 市場価格がない場合は原価の2倍と仮定（初期参入のインセンティブ）
                
                profit_margin = (mkt_price - est_cost) / mkt_price if mkt_price > 0 else 0
                # 利益率が高いほどスコアアップ。赤字(マイナス)の場合は極小スコア。
                profit_factor = max(0.01, profit_margin)

                # 総合スコア
                score = supply_demand_ratio * profit_factor
                
                cat_keys.append(k)
                weights.append(score)
            
            if not cat_keys: return

            cat_key = random.choices(cat_keys, weights=weights, k=1)[0]
            cat_def = categories[cat_key]
            
            parts_config = {}
            total_score = 0
            total_cost = 0
            parts_def = cat_def['parts']
            
            # 経営方針に基づくサプライヤー選定基準
            # luxury: 品質重視 (score高い順)
            # value: コスト重視 (cost低い順)
            # standard: バランス
            if 'orientation' in self.company.keys():
                orientation = self.company['orientation']
            else:
                orientation = 'standard'
            
            for part in parts_def:
                suppliers = db.fetch_all("SELECT id, trait_material_score, trait_cost_multiplier FROM companies WHERE type = 'system_supplier' AND part_category = ?", (part['key'],))
                if not suppliers:
                    return # サプライヤーが見つからない場合は開発を中止
                
                if orientation == 'luxury':
                    supplier = sorted(suppliers, key=lambda x: x['trait_material_score'], reverse=True)[0]
                elif orientation == 'value':
                    supplier = sorted(suppliers, key=lambda x: x['trait_cost_multiplier'])[0]
                else:
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
                total_cost += p_cost
            
            avg_material_score = total_score / len(parts_def)
            
            # 開発方針の決定
            if orientation == 'luxury':
                # コンセプト特化で高付加価値を狙う
                strategy = gb.DEV_STRATEGY_CONCEPT_SPECIALIZED
            elif orientation == 'value':
                # 生産効率特化で大量生産・コストダウン
                strategy = gb.DEV_STRATEGY_EFFICIENCY_SPECIALIZED
            else:
                # バランス
                strategy = gb.DEV_STRATEGY_BALANCED

            name = name_generator.generate_product_name(strategy)
            
            # DBに登録 (status='developing')
            # base_price, sales_price は完成時に確定するため仮置き
            db.execute_query("""
                INSERT INTO product_designs
                (company_id, division_id, category_key, name, material_score, concept_score, production_efficiency, base_price, sales_price, status, strategy, developed_week, parts_config)
                VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 'developing', ?, ?, ?)
            """, (self.company_id, division['id'], cat_key, name, avg_material_score, strategy, current_week, json.dumps(parts_config)))
            db.log_file_event(current_week, self.company_id, "Development Start", f"Started development of {name}")
            db.increment_weekly_stat(current_week, self.company_id, 'development_ordered', 1)

    def decide_order_fulfillment(self, current_week, orders, inventory):
        """
        メーカー用: 受注処理
        届いている注文を確認し、在庫があれば受注(Accepted)する
        """
        if self.company['type'] != 'npc_maker': return

        if not orders: return

        # 在庫情報のマッピング (リスト内の辞書オブジェクトを直接参照する)
        inv_dict = {item['design_id']: item for item in inventory}

        for order in orders:
            did = order['design_id']
            qty = order['quantity']
            
            item = inv_dict.get(did)
            
            if item and item['quantity'] >= qty:
                # 受注可能
                db.execute_query("UPDATE b2b_orders SET status = 'accepted' WHERE id = ?", (order['id'],))
                # メモリ上の在庫を即座に減らす
                # これにより、後続の decide_production が「在庫が減った」ことを認識して生産できるようになる
                item['quantity'] -= qty
                
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

        # CRISIS時は拡張しない
        if self.phase == 'CRISIS': return

        # 現在の施設容量を確認
        facilities = db.fetch_all("SELECT type, size FROM facilities WHERE company_id = ?", (self.company_id,))
        current_cap = {'factory': 0, 'store': 0, 'office': 0}
        for fac in facilities:
            if fac['type'] in current_cap:
                current_cap[fac['type']] += fac['size']
        
        # ターゲット事業部 (NPCは単一事業部と仮定)
        target_div_id = self.divisions[0]['id'] if self.divisions else None

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
                    # 施設割り当て: NPCは単一事業部制を基本とするため、すべての施設をターゲット事業部に割り当てる
                    assign_div_id = target_div_id

                    # 余裕基準: 購入後も資金が1億円以上残る
                    purchase_price = available['rent'] * gb.FACILITY_PURCHASE_MULTIPLIER
                    
                    if self.company['funds'] > purchase_price + 100000000:
                        # 購入
                        db.execute_query("UPDATE facilities SET company_id = ?, division_id = ?, is_owned = 1 WHERE id = ?", 
                                         (self.company_id, assign_div_id, available['id']))
                        db.execute_query("UPDATE companies SET funds = funds - ? WHERE id = ?", (purchase_price, self.company_id))
                        db.execute_query("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, 'facility_purchase', ?)",
                                         (current_week, self.company_id, purchase_price))
                        db.log_file_event(current_week, self.company_id, "Facility", f"Purchased {ftype} (Size: {available['size']})")
                    else:
                        # 賃貸
                        db.execute_query("UPDATE facilities SET company_id = ?, division_id = ?, is_owned = 0 WHERE id = ?", 
                                         (self.company_id, assign_div_id, available['id']))
                        db.log_file_event(current_week, self.company_id, "Facility", f"Rented {ftype} (Size: {available['size']})")

        acquire_facility('factory', factory_needs * gb.NPC_SCALE_FACTOR, current_cap['factory'], gb.RENT_FACTORY)
        acquire_facility('store', store_needs * gb.NPC_SCALE_FACTOR, current_cap['store'], gb.RENT_STORE_BASE)
        acquire_facility('office', office_needs * gb.NPC_SCALE_FACTOR, current_cap['office'], gb.RENT_OFFICE)

    def decide_advertising(self, current_week):
        """
        広告戦略: 資金に余裕があればブランド広告や商品広告を打つ
        """
        if self.phase == 'CRISIS': return

        # 予算: 資金の2% または 5000万円 の小さい方 (過剰投資防止)
        budget = min(self.company['funds'] * 0.02, 50000000)
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
                
                # 競合価格の調査
                cat_key = p['category_key']
                competitor_prices = db.fetch_all("SELECT sales_price FROM product_designs WHERE category_key = ? AND status='completed' AND company_id != ?", (cat_key, self.company_id))
                if competitor_prices:
                    avg_market_price = sum(c['sales_price'] for c in competitor_prices) / len(competitor_prices)
                else:
                    avg_market_price = p['sales_price']

                new_price = p['sales_price']
                
                # 閾値を性格で補正
                overstock_threshold = 50 * patience
                shortage_threshold = 10 / patience
                
                # CRISIS時は在庫処分を急ぐ
                if self.phase == 'CRISIS':
                    overstock_threshold *= 0.5

                # ロジック: 在庫過多なら値下げ、品薄なら値上げ
                # さらに市場価格との乖離も考慮する
                if current_qty > overstock_threshold and avg_sales_qty < (5 * patience):
                    # 在庫過多
                    # 基準価格(base_price)が原価ではないので、parts_configから原価を計算
                    p_conf = json.loads(p['parts_config']) if p['parts_config'] else {}
                    material_cost = sum(part['cost'] for part in p_conf.values()) if p_conf else 0
                    # CRISIS時は原価割れでも現金化する
                    min_margin = 0.8 if self.phase == 'CRISIS' else gb.MIN_PROFIT_MARGIN
                    min_price = int(material_cost * min_margin)

                    # 方針による値下げ圧力の違い
                    # 高級ブランドは安易な値下げを嫌う（ブランド毀損防止）
                    if 'orientation' in self.company.keys():
                        orientation = self.company['orientation']
                    else:
                        orientation = 'standard'
                    resistance = 1.0
                    if orientation == 'luxury': resistance = 0.5
                    elif orientation == 'value': resistance = 1.5 # 廉価メーカーは積極的に下げる

                    if p['sales_price'] > avg_market_price * 1.1: # 市場より1割以上高い
                        drop_rate = gb.PRICE_ADJUST_RATE * 2.0 * aggressiveness * resistance
                    else:
                        drop_rate = gb.PRICE_ADJUST_RATE * aggressiveness * resistance
                    
                    proposed_price = int(p['sales_price'] * (1.0 - drop_rate * random.uniform(0.8, 1.2)))
                    new_price = max(min_price, proposed_price)
                    
                elif current_qty < shortage_threshold and avg_sales_qty > (10 / patience):
                    # 品薄・好調
                    # 市場価格より安いなら、市場価格に近づける（利益確保）
                    # 高級ブランドは強気に値上げする
                    if 'orientation' in self.company.keys():
                        orientation = self.company['orientation']
                    else:
                        orientation = 'standard'
                    boost = 1.0
                    if orientation == 'luxury': boost = 1.5
                    
                    if p['sales_price'] < avg_market_price:
                        raise_rate = gb.PRICE_ADJUST_RATE * 1.5 * aggressiveness * boost
                    else:
                        raise_rate = gb.PRICE_ADJUST_RATE * 0.5 * aggressiveness * boost
                    
                    new_price = int(p['sales_price'] * (1.0 + raise_rate * random.uniform(0.8, 1.2)))
                
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

    def decide_stock_action(self, current_week):
        """
        株式関連の意思決定 (IPO, 増資, 自社株買い)
        """
        # 1. IPO申請 (未上場の場合)
        if self.company['listing_status'] == 'private':
            # IPO要件チェック (簡易)
            funds = self.company['funds']
            inv_val = db.fetch_one("SELECT SUM(quantity * sales_price * 0.5) as val FROM inventory WHERE company_id = ?", (self.company_id,))['val'] or 0
            fac_val = db.fetch_one("SELECT SUM(rent * 100) as val FROM facilities WHERE company_id = ? AND is_owned = 1", (self.company_id,))['val'] or 0
            debt = db.fetch_one("SELECT SUM(amount) as val FROM loans WHERE company_id = ?", (self.company_id,))['val'] or 0
            
            net_assets = funds + inv_val + fac_val - debt
            
            # 黒字要件
            profit_res = db.fetch_one("""
                SELECT SUM(CASE WHEN category = 'revenue' THEN amount ELSE 0 END) - 
                       SUM(CASE WHEN category NOT IN ('revenue', 'material', 'stock_purchase', 'facility_purchase', 'facility_sell', 'equity_finance') THEN amount ELSE 0 END) as profit
                FROM account_entries WHERE company_id = ? AND week >= ?
            """, (self.company_id, current_week - gb.IPO_MIN_PROFIT_WEEKS))
            recent_profit = profit_res['profit'] or 0
            
            is_eligible = (
                net_assets >= gb.IPO_MIN_NET_ASSETS and
                recent_profit > 0 and
                self.company['credit_rating'] >= gb.IPO_MIN_CREDIT_RATING
            )
            
            # 申請判断: GROWTHフェーズ または 資金調達が必要
            if is_eligible and (self.phase == 'GROWTH' or self.company['funds'] < net_assets * 0.2):
                db.execute_query("UPDATE companies SET listing_status = 'applying' WHERE id = ?", (self.company_id,))
                db.log_file_event(current_week, self.company_id, "IPO Application", "Applied for IPO")

        # 2. 上場企業のアクション
        elif self.company['listing_status'] == 'public':
            stock_price = self.company['stock_price']
            shares = self.company['outstanding_shares']
            
            # BPS/PBR計算 (簡易)
            funds = self.company['funds']
            # ... (資産計算はIPO時と同様だが省略し、fundsベースで判断)
            # PBR代用として、時価総額と資金の比率を見る (資金リッチなら自社株買い、資金不足なら増資)
            
            # A. 公募増資 (Public Offering)
            # 資金不足 (CRISIS) または 成長投資 (GROWTH) で資金が足りない
            if (self.phase == 'CRISIS' or (self.phase == 'GROWTH' and funds < 500000000)):
                new_shares = int(shares * 0.1) # 10%増資
                issue_price = int(stock_price * 0.95) # 5%ディスカウント
                raised_amount = new_shares * issue_price
                
                db.execute_query("UPDATE companies SET funds = funds + ?, outstanding_shares = outstanding_shares + ? WHERE id = ?", (raised_amount, new_shares, self.company_id))
                db.execute_query("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, 'equity_finance', ?)", (current_week, self.company_id, raised_amount))
                db.log_file_event(current_week, self.company_id, "Public Offering", f"Issued {new_shares} shares, raised {raised_amount}")
                db.execute_query("INSERT INTO news_logs (week, company_id, message, type) VALUES (?, ?, ?, ?)", (current_week, self.company_id, f"公募増資を実施し、{raised_amount:,}円を調達しました。", 'market'))

            # B. 自社株買い (Buyback)
            # 資金余剰 (STABLE/GROWTH) かつ 資金が潤沢 (20億円以上)
            elif self.phase in ['STABLE', 'GROWTH'] and funds > 2000000000:
                budget = int(funds * 0.05) # 資金の5%を使う
                buy_price = int(stock_price * 1.05)
                buy_shares = int(budget / buy_price)
                if buy_shares > 0:
                    db.execute_query("UPDATE companies SET funds = funds - ?, outstanding_shares = outstanding_shares - ? WHERE id = ?", (budget, buy_shares, self.company_id))
                    db.execute_query("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, 'equity_finance', ?)", (current_week, self.company_id, -budget))
                    db.log_file_event(current_week, self.company_id, "Stock Buyback", f"Bought back {buy_shares} shares, cost {budget}")
