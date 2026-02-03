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
            self.company = dict(company_data)
        else:
            row = db.fetch_one("SELECT * FROM companies WHERE id = ?", (company_id,))
            self.company = dict(row) if row else None

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
        
        # 週次目標・計画データ (decide_weekly_targetsで設定)
        self.plan = {
            'target_production': {}, # {design_id: quantity}
            'target_procurement': {}, # {design_id: quantity}
            'required_capacity': {'production': 0, 'sales': 0, 'store': 0, 'development': 0},
            'required_facility': {'factory': 0, 'store': 0, 'office': 0}
        }
        # レポート用統計データ
        self.plan['stats'] = {
            'current_share': 0.0,
            'target_share': 0.0,
            'target_sales': 0,
            'fair_share': 0.0
        }

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
        profit_margin = profit / revenue if revenue > 0 else -1.0

        # フェーズ判定ロジック
        # 修正: 資金が潤沢(固定費52週分以上)なら、赤字でもSTABLEを維持する (投資フェーズとみなす)
        is_cash_rich = funds > (fixed_costs * 52)

        # CRISIS: 資金が固定費6週分未満、または借入余力がなく赤字
        # ただし、Cash RichならCRISISにはならない
        if not is_cash_rich and ((funds + credit_room) < (fixed_costs * 6) or (funds < fixed_costs * 4 and profit < 0)):
            self.phase = 'CRISIS'
        elif not is_cash_rich and profit < -fixed_costs: # 赤字額が固定費を超える(大赤字)なら資金があっても危機
            self.phase = 'CRISIS'
        # GROWTH: 利益率5%以上 かつ 資金に余裕がある (固定費12週分以上)
        elif profit_margin > 0.05 and funds > (fixed_costs * 12):
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

    def decide_hiring(self, current_week, candidates_pool=None, all_caps=None):
        """
        採用計画: 目標達成に必要なキャパシティと現状を比較し、不足分を採用する
        """
        if not self.company: return
        
        # 修正: CRISISフェーズでも、従業員が極端に少ない(3人未満)場合は採用を試みる (再建のため)
        if self.phase == 'CRISIS' and len(self.employees) >= 3: return

        # 採用枠の設定
        max_offers = 3
        if self.phase == 'GROWTH':
            max_offers = 15

        # 既にオファーを出している件数を確認
        current_offers_cnt = db.fetch_one("SELECT COUNT(*) as cnt FROM job_offers WHERE company_id = ?", (self.company_id,))['cnt']
        
        # 今回使える枠 (人事は別枠で追加可能とするため、ここではベース枠)
        available_offers = max_offers - current_offers_cnt
        if available_offers < 0: available_offers = 0

        # 今回のループでオファーを出したNPCのIDを記録（重複防止）
        offered_npc_ids = set()
        
        # 候補者リストが渡されていない場合はDBから取得 (フォールバック)
        if candidates_pool is None:
            candidates_pool = db.fetch_all("SELECT * FROM npcs WHERE company_id IS NULL LIMIT 100")

        # 自社に再雇用禁止期間中の候補者を除外
        filtered_candidates = [
            c for c in candidates_pool 
            if c['last_company_id'] != self.company_id or (current_week - c['last_resigned_week']) >= gb.REHIRE_PROHIBITION_WEEKS
        ]

        # ---------------------------------------------------------
        # 1. 人事部 (HR) の優先採用 (別枠判定)
        # ---------------------------------------------------------
        hr_employees = [e for e in self.employees if e['department'] == gb.DEPT_HR]
        total_hr_power = sum([e['hr'] for e in hr_employees])
        
        # 供給キャパシティ (経営者補正込み)
        hr_supply = (total_hr_power * gb.NPC_SCALE_FACTOR) + (50 * gb.NPC_SCALE_FACTOR)
        
        # 将来の増員を見越したHR需要予測 (現在の従業員数 + 最大採用数)
        projected_employees_scaled = (len(self.employees) + max_offers) * gb.NPC_SCALE_FACTOR
        projected_hr_demand = 50 * (projected_employees_scaled / gb.HR_CAPACITY_PER_PERSON)
        
        # 目標: 要求の1.2倍
        target_hr_supply = projected_hr_demand * 1.2
        
        if hr_supply < target_hr_supply or not hr_employees:
            # 不足キャパシティ
            shortage_cap = target_hr_supply - hr_supply
            # 必要人数 (能力50と仮定)
            needed_hr = math.ceil(shortage_cap / (50 * gb.NPC_SCALE_FACTOR))
            if needed_hr < 1: needed_hr = 1
            
            # 人事採用実行 (別枠として実行)
            self._process_hiring_round(
                current_week, gb.DEPT_HR, needed_hr, filtered_candidates, offered_npc_ids
            )

        # ---------------------------------------------------------
        # 2. 通常採用 (事業計画に基づく)
        # ---------------------------------------------------------
        if available_offers <= 0: return

        target_dept = None

        # 販売効率の取得 (小売用) - キャパシティ単位合わせのため
        sales_eff = 1.0
        if self.company['type'] == 'npc_retail':
             if self.company['industry'] in gb.INDUSTRIES:
                sales_eff = gb.INDUSTRIES[self.company['industry']].get('sales_efficiency_base', gb.BASE_SALES_EFFICIENCY)

        # 計画に基づく必要キャパシティとのギャップを埋める
        if not target_dept:
            # 現在のキャパシティ (all_capsから取得、なければ概算)
            current_caps = all_caps.get(self.company_id, {}) if all_caps else {}
            
            # 各部門の不足率(Required/Current)を計算し、最も不足している部署を優先する
            shortage_scores = {}

            # 生産 (メーカーのみ)
            if self.company['type'] == 'npc_maker':
                req_prod = self.plan['required_capacity'].get('production', 0)
                cur_prod = current_caps.get('production_capacity', 1)
                if req_prod > cur_prod:
                    shortage_scores[gb.DEPT_PRODUCTION] = req_prod / max(1, cur_prod)

            # 店舗 (小売のみ)
            if self.company['type'] == 'npc_retail':
                # req_store は能力値ベース。sales_effを掛けてスループットベースに変換して比較
                req_store_throughput = self.plan['required_capacity'].get('store', 0) * sales_eff
                cur_store_throughput = current_caps.get('store_throughput', 1)
                if req_store_throughput > cur_store_throughput:
                    shortage_scores[gb.DEPT_STORE] = req_store_throughput / max(0.1, cur_store_throughput)

            # 営業
            req_sales = self.plan['required_capacity'].get('sales', 0)
            cur_sales = current_caps.get('sales_capacity', 1)
            
            # 小売の場合、営業人員が店舗人員を超えないように抑制するキャップ (店舗人員の50%まで)
            if self.company['type'] == 'npc_retail':
                store_emp_count = len([e for e in self.employees if e['department'] == gb.DEPT_STORE])
                sales_emp_count = len([e for e in self.employees if e['department'] == gb.DEPT_SALES])
                # 店舗人員が少ないうちは営業を増やしすぎない (最低2人は許容)
                if sales_emp_count > max(2, store_emp_count * 0.5):
                    req_sales = 0 # 採用抑制

            if req_sales > cur_sales:
                shortage_scores[gb.DEPT_SALES] = req_sales / max(1, cur_sales)

            # 開発 (メーカーのみ)
            if self.company['type'] == 'npc_maker':
                req_dev = self.plan['required_capacity'].get('development', 0)
                cur_dev = current_caps.get('development_capacity', 1)
                if req_dev > cur_dev:
                    shortage_scores[gb.DEPT_DEV] = req_dev / max(1, cur_dev)

            # 最も不足度が高い部署を選択 (1.1倍以上の不足がある場合)
            if shortage_scores:
                best_dept = max(shortage_scores, key=shortage_scores.get)
                if shortage_scores[best_dept] > 1.1:
                    target_dept = best_dept

        # フォールバック: キャパシティ充足率に基づく採用 (人数ではなく負荷で判断)
        if not target_dept:
            # 現在のキャパシティ (all_capsから取得、なければ概算)
            current_caps = all_caps.get(self.company_id, {}) if all_caps else {}
            
            # 充足率 (Required / Current) を計算
            utilization = {}
            
            # 各部門の負荷状況
            req_prod = self.plan['required_capacity'].get('production', 0)
            cur_prod = current_caps.get('production_capacity', 1)
            if req_prod > 0: utilization[gb.DEPT_PRODUCTION] = req_prod / max(1, cur_prod)
            
            req_store = self.plan['required_capacity'].get('store', 0)
            cur_store_throughput = current_caps.get('store_ops_capacity', 1)
            # 修正: スループットベースで負荷率を計算
            if req_store > 0: utilization[gb.DEPT_STORE] = (req_store * sales_eff) / max(0.1, cur_store_throughput)
            
            req_sales = self.plan['required_capacity'].get('sales', 0)
            cur_sales = current_caps.get('sales_capacity', 1)
            if req_sales > 0: utilization[gb.DEPT_SALES] = req_sales / max(1, cur_sales)
            
            req_dev = self.plan['required_capacity'].get('development', 0)
            cur_dev = current_caps.get('development_capacity', 1)
            if req_dev > 0: utilization[gb.DEPT_DEV] = req_dev / max(1, cur_dev)

            # 業態ごとの採用候補
            candidates = []
            if self.company['type'] == 'npc_maker':
                candidates = [gb.DEPT_PRODUCTION, gb.DEPT_DEV, gb.DEPT_SALES]
            elif self.company['type'] == 'npc_retail':
                candidates = [gb.DEPT_STORE, gb.DEPT_SALES]
            
            # 最も逼迫している部署を探す
            best_dept = None
            max_util = 0
            for d in candidates:
                u = utilization.get(d, 0)
                if u > max_util:
                    max_util = u
                    best_dept = d
            
            # 閾値判定: GROWTHなら0.7(余裕を持って採用), STABLEなら0.95(ギリギリまで粘る)
            threshold = 0.7 if self.phase == 'GROWTH' else 0.95
            
            if best_dept and max_util > threshold:
                target_dept = best_dept
        
        if not target_dept:
            target_dept = random.choice(gb.DEPARTMENTS)

        if available_offers <= 0: return

        # 通常採用実行
        self._process_hiring_round(
            current_week, target_dept, available_offers, filtered_candidates, offered_npc_ids
        )

    def _process_hiring_round(self, current_week, target_dept, count, candidates, offered_npc_ids):
        """
        指定された部署・人数分の採用オファー処理を行う
        """
        if count <= 0: return 0
        
        # ターゲット業界の特定 (最初の事業部の業界とする)
        target_industry = self.divisions[0]['industry_key'] if self.divisions else 'automotive'
        
        # 人事担当者の能力取得
        hr_employees = [e for e in self.employees if e['department'] == gb.DEPT_HR]
        avg_hr = sum([e['hr'] for e in hr_employees]) / len(hr_employees) if hr_employees else 0
        
        # 人事能力による誤差範囲の計算
        error_range = 40 - (36 * (min(100, avg_hr) / 100.0))
        half_range = error_range / 2.0
        
        # CEOの判断精度
        ceo_precision = self._get_ceo_precision('hr')
        
        offers_made = 0
        
        for _ in range(count):
            # 予算チェック (年収の2倍程度の余裕があるか)
            if self.company['funds'] < gb.BASE_SALARY_YEARLY * gb.NPC_SCALE_FACTOR * 2:
                break

            best_candidate = None
            best_score = -1

            for cand in candidates:
                # 今回のループで既にオファー済みならスキップ
                if cand['id'] in offered_npc_ids:
                    continue

                # 能力値をファジー化して認知
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
                desired = cand['desired_salary']
                if desired == 0: desired = cand['salary']
                if desired == 0: desired = gb.BASE_SALARY_YEARLY
                
                noise_range = 0.4 * (1.0 - ceo_precision)
                evaluation_noise = random.uniform(1.0 - noise_range, 1.0 + noise_range)
                score = (stat_val / desired) * evaluation_noise
                
                if score > best_score:
                    best_score = score
                    best_candidate = cand
            
            if best_candidate:
                # オファー発行
                offer_salary = best_candidate['desired_salary'] if best_candidate['desired_salary'] > 0 else gb.BASE_SALARY_YEARLY
                
                db.execute_query("INSERT INTO job_offers (week, company_id, npc_id, offer_salary, target_dept) VALUES (?, ?, ?, ?, ?)",
                                 (current_week, self.company_id, best_candidate['id'], offer_salary, target_dept))
                
                db.log_file_event(current_week, self.company_id, "HR Hiring Offer", f"Offered {offer_salary} yen to {best_candidate['name']} (ID: {best_candidate['id']}) for {target_dept}")
                offered_npc_ids.add(best_candidate['id'])
                offers_made += 1
            else:
                break # 候補者がいなければ終了
        
        return offers_made

    def decide_restructuring(self, current_week):
        """
        リストラ策: CRISISフェーズで赤字の場合、人員削減や施設解約を行う
        """
        if self.phase != 'CRISIS': return
        
        # 修正: 従業員数が少なすぎる場合(5人以下)は解雇しない (事業継続不能になるため)
        if len(self.employees) <= 5: return

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
        
        # 修正: 資金状況に応じて複数人解雇する
        fixed_costs = self._calculate_weekly_fixed_costs()
        weeks_left = self.company['funds'] / max(1, fixed_costs)
        
        fire_count = 1
        if weeks_left < 4: fire_count = 5 # 資金ショート寸前なら大量解雇
        elif weeks_left < 8: fire_count = 3
        
        targets = scored_candidates[:fire_count]
        
        for _, target in targets:
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

    def decide_weekly_targets(self, current_week, designs, inventory, b2b_sales_history, market_total_sales_4w, economic_index, maker_stocks=None):
        """
        週次目標設定: シェア目標 -> 在庫目標 -> 生産/仕入目標 -> 必要キャパシティ算出
        """
        # 1. メーカーの生産目標設定
        if self.company['type'] == 'npc_maker':
            completed_designs = [d for d in designs if d['status'] == 'completed']
            
            # 市場環境
            # 修正: 自業界のメーカーのみをカウントする
            my_industry = self.company['industry']
            maker_count = db.fetch_one("SELECT COUNT(*) as cnt FROM companies WHERE type IN ('player', 'npc_maker') AND is_active = 1 AND industry = ?", (my_industry,))['cnt']
            maker_count = max(1, maker_count)
            
            # 修正: 自業界の市場規模（直近4週）を取得する
            market_stats_res = db.fetch_one("""
                SELECT SUM(w.b2b_sales) as total 
                FROM weekly_stats w
                JOIN companies c ON w.company_id = c.id
                WHERE w.week >= ? AND c.industry = ?
            """, (current_week - 4, my_industry))
            industry_total_sales_4w = market_stats_res['total'] if market_stats_res and market_stats_res['total'] else 0

            # 会社全体のシェアを計算 (Death Spiral防止のため、全社的な立ち位置を把握)
            my_total_sales_4w = 0
            for design in completed_designs:
                sales_history_item = next((s for s in b2b_sales_history if s['seller_id'] == self.company_id and s['design_id'] == design['id']), None)
                max_weekly = sales_history_item['max_weekly'] if sales_history_item else 0
                my_total_sales_4w += max_weekly * 4
            
            company_share = my_total_sales_4w / industry_total_sales_4w if industry_total_sales_4w > 0 else 1.0 / maker_count
            fair_share = 1.0 / maker_count
            
            self.plan['stats']['current_share'] = company_share
            self.plan['stats']['fair_share'] = fair_share
            
            # シェア挽回モード: Fair Shareの8割を下回ったらアグレッシブに動く
            is_recovery_mode = company_share < fair_share * 0.8

            for design in completed_designs:
                # 実績確認
                sales_history_item = next((s for s in b2b_sales_history if s['seller_id'] == self.company_id and s['design_id'] == design['id']), None)
                max_weekly_sales = sales_history_item['max_weekly'] if sales_history_item else 0
                estimated_4w_sales = max_weekly_sales * 4 # 最高週販ベースで4週間分を推計
                
                # シェア計算
                current_design_share = estimated_4w_sales / industry_total_sales_4w if industry_total_sales_4w > 0 else 1.0 / maker_count
                
                # 目標シェア設定
                brand_factor = max(0.5, min(2.0, self.company['brand_power'] / 50.0))
                if current_design_share == 0:
                    target_share = max(0.05, fair_share * brand_factor)
                else:
                    if is_recovery_mode:
                        # 縮小均衡回避: シェア低下時は現状維持ではなく、Fair Shareへの復帰を目指して高い目標を掲げる
                        # 現在のシェアの1.2倍 または Fair Shareとの差分の20%埋め の大きい方
                        recovery_target = current_design_share + (fair_share / len(completed_designs) - current_design_share) * 0.2
                        target_share = max(current_design_share * 1.2, recovery_target)
                    else:
                        growth_rate = 1.1 if self.phase == 'STABLE' else 1.3 if self.phase == 'GROWTH' else 0.95
                        target_share = current_design_share * growth_rate
                
                target_share = min(0.5, max(0.01, target_share))

                # レポート用統計データ (目標シェアは全製品の合計とする)
                self.plan['stats']['target_share'] += target_share
                
                # 需要予測
                trend_data = db.fetch_one("SELECT b2c_demand FROM market_trends WHERE industry_key = ? ORDER BY week DESC LIMIT 1", (my_industry,))
                base_demand = trend_data['b2c_demand'] if trend_data else gb.INDUSTRIES[my_industry]['base_demand']
                estimated_demand = base_demand * economic_index
                
                predicted_sales = estimated_demand * target_share
                if self.phase == 'GROWTH':
                    predicted_sales = max(predicted_sales, max_weekly_sales * 1.2)
                
                # 目標在庫
                weeks_stock = 1 if self.phase == 'GROWTH' else 1 if self.phase == 'STABLE' else 1
                target_stock = int(predicted_sales * weeks_stock)
                
                # 現在在庫
                stock_item = next((inv for inv in inventory if inv['design_id'] == design['id']), None)
                current_stock = stock_item['quantity'] if stock_item else 0
                
                # 必要生産数
                needed = max(0, target_stock - current_stock)

                # 生産平準化: 急激なゼロ生産を避ける (在庫が目標の1.5倍以内で、資金があるなら、最低限のラインを維持)
                if needed == 0 and current_stock < target_stock * 1.5 and self.phase != 'CRISIS':
                     needed = int(predicted_sales * 0.5)

                self.plan['target_production'][design['id']] = needed
                
                # 必要生産キャパシティ加算 (台数 / 効率)
                eff = design['production_efficiency'] if design['production_efficiency'] > 0 else 0.27
                # 効率は「1人週あたりの台数」なので、必要人数 = 台数 / 効率
                # キャパシティ値 = 人数 * SCALE_FACTOR
                req_man_power = needed / eff
                self.plan['required_capacity']['production'] += req_man_power * 50

        # 2. 小売の仕入目標設定
        elif self.company['type'] == 'npc_retail':
            # 市場のメーカー在庫から取り扱い候補を選定（簡易的に既存ロジックのスコアリング結果を想定）
            # ここでは「販売目標」を立てる
            
            # 自社の販売キャパシティ（現状）
            # 修正: 従業員数ではなく、実際の能力値合計を使用する
            store_employees = [e for e in self.employees if e['department'] == gb.DEPT_STORE]
            total_store_power = sum(e['store_ops'] for e in store_employees)
            
            # 業界ごとの販売効率を取得
            my_industry = self.company['industry']
            sales_eff = gb.BASE_SALES_EFFICIENCY
            if my_industry in gb.INDUSTRIES:
                sales_eff = gb.INDUSTRIES[my_industry].get('sales_efficiency_base', gb.BASE_SALES_EFFICIENCY)
            
            # 修正: 能力合計ベースでキャパシティを計算 (能力50を1人前とする)
            # 従業員がいない場合は最低値(50)を仮定して計算し、採用を促す
            base_power = total_store_power if total_store_power > 0 else 50
            estimated_capacity = (base_power / 50.0) * gb.NPC_SCALE_FACTOR * sales_eff
            
            # 市場全体の需要から「あるべき販売数(Fair Share)」を推計
            # 自分の業界・カテゴリの需要を取得
            my_industry = self.company['industry']
            target_demand = 0
            trend_data = db.fetch_one("SELECT b2c_demand FROM market_trends WHERE industry_key = ? ORDER BY week DESC LIMIT 1", (my_industry,))
            target_demand = trend_data['b2c_demand'] if trend_data else gb.INDUSTRIES[my_industry]['base_demand']
            
            # 修正: 自業界の小売のみをカウントする
            retailer_count = db.fetch_one("SELECT COUNT(*) as cnt FROM companies WHERE type = 'npc_retail' AND is_active = 1 AND industry = ?", (my_industry,))['cnt']
            fair_share_sales = (target_demand * economic_index) / max(1, retailer_count)

            # 目標販売数
            # 現在の能力ベースの成長と、市場ポテンシャルベースの目標の大きい方を採用する
            # 修正: 成長目標を1.2倍にして、採用閾値(1.1倍)を超えるようにする
            growth_target = estimated_capacity * 1.5 if self.phase == 'GROWTH' else estimated_capacity * 1.2
            if self.phase == 'GROWTH':
                # 成長期は、Fair Shareの120%まで目指す
                target_sales = max(growth_target, fair_share_sales * 1.2)
            else:
                # 安定期でも、Fair Shareの80%までは目指す (以前は20%で低すぎたためボトルネックになっていた)
                target_sales = max(growth_target, fair_share_sales * 0.8)
            
            # レポート用統計
            self.plan['stats']['target_sales'] = int(target_sales)
            self.plan['stats']['current_share'] = estimated_capacity / target_demand if target_demand > 0 else 0
            self.plan['stats']['target_share'] = target_sales / target_demand if target_demand > 0 else 0

            # 必要店舗キャパシティ
            # 販売数 = キャパシティ * 効率係数(能力/50)
            # 標準能力(50)と仮定して必要キャパを逆算
            req_store_cap = target_sales / sales_eff
            self.plan['required_capacity']['store'] = req_store_cap
            
            # 仕入目標数は decide_procurement で詳細に決めるが、ここでは総枠として保持
            # 在庫目標: 販売目標の6週分
            target_stock_total = target_sales * 6
            current_stock_total = sum(i['quantity'] for i in inventory)
            self.plan['target_procurement']['total'] = max(0, target_stock_total - current_stock_total)

        # 3. 共通: 営業・開発・施設の必要量算出
        
        # 営業: 取引数予測に基づく
        # メーカー: 生産数 = 出荷数と仮定
        # 小売: 仕入数 + 販売数
        if self.company['type'] == 'npc_maker':
            tx_volume = sum(self.plan['target_production'].values())
        else:
            tx_volume = self.plan['target_procurement'].get('total', 0) + target_sales
            
        self.plan['required_capacity']['sales'] = tx_volume * gb.REQ_CAPACITY_SALES_TRANSACTION
        
        # 開発 (メーカーのみ)
        if self.company['type'] == 'npc_maker' and self.phase != 'CRISIS':
            # 常に1ラインは動かしたい
            self.plan['required_capacity']['development'] = gb.REQ_CAPACITY_DEV_PROJECT

        # 施設必要量
        # 生産 -> 工場
        # 必要キャパシティ / SCALE_FACTOR = 必要人数
        req_prod_ppl = self.plan['required_capacity']['production'] / gb.NPC_SCALE_FACTOR
        self.plan['required_facility']['factory'] = int(req_prod_ppl)
        
        # 店舗 -> 店舗
        req_store_ppl = self.plan['required_capacity']['store'] / gb.NPC_SCALE_FACTOR
        self.plan['required_facility']['store'] = int(req_store_ppl)
        
        # オフィス (営業 + 開発 + 本社機能)
        req_sales_ppl = self.plan['required_capacity']['sales'] / gb.NPC_SCALE_FACTOR
        req_dev_ppl = self.plan['required_capacity']['development'] / gb.NPC_SCALE_FACTOR
        # 本社機能(HR/PR/Accounting)は全従業員の10%程度と仮定
        total_emp_est = req_prod_ppl + req_store_ppl + req_sales_ppl + req_dev_ppl
        req_admin_ppl = total_emp_est * 0.1
        self.plan['required_facility']['office'] = int(req_sales_ppl + req_dev_ppl + req_admin_ppl)

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

        total_man_power = 0
        for emp in effective_employees:
            # 能力50で1人分(1.0)の働き。これを設計書の効率と掛け合わせる。
            total_man_power += (emp['production'] / 50.0) * gb.NPC_SCALE_FACTOR
        
        # この事業部の製品のみ対象
        div_designs = [d for d in designs if d['division_id'] == division['id']]
        
        for design in div_designs:
            # 計画された生産数を使用
            to_produce = self.plan['target_production'].get(design['id'], 0)
            if to_produce <= 0: continue

            # キャパシティが1台分未満でも、確率的に1台作れるようにする（あるいは最低1台は作れるようにする）
            if total_man_power <= 0.1: break

            # 在庫確認
            stock_item = next((inv for inv in inventory if inv['design_id'] == design['id']), None)
            current_stock = stock_item['quantity'] if stock_item else 0

            # 設計書の生産効率係数を適用
            design_eff = design['production_efficiency']
            
            # 生産可能数: キャパシティ * 効率
            # 端数は確率的に切り上げ (例: 9.8台作れる能力なら80%の確率で10台、20%で9台)
            float_produce = total_man_power * design_eff
            max_produce = int(float_produce)
            if random.random() < (float_produce - max_produce):
                max_produce += 1
            
            to_produce = min(to_produce, max_produce)
            
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
                total_man_power -= used_capacity
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
        trends = db.fetch_all("SELECT industry_key, b2c_demand FROM market_trends WHERE week = ?", (current_week - 1,))
        demand_map = {t['industry_key']: t['b2c_demand'] for t in trends}

        # 商品スコアリング (コンセプト * ブランド / 価格)
        scored_items = []
        for item in maker_stocks:
            # 業界チェック: 自社の業界に含まれるカテゴリの商品のみ対象とする
            my_industry = self.company['industry']
            if my_industry in gb.INDUSTRIES:
                pass # 業界一致は前提

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
            cat_demand = demand_map.get(my_industry, 1000)
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
        # 1. 販売予測に基づく目標在庫設定
        sales_capacity = my_capabilities['store_throughput']
        
        # 直近4週のB2C販売実績を取得（実需の把握）
        sales_history = db.fetch_one("SELECT COUNT(*) as cnt FROM transactions WHERE seller_id = ? AND type = 'b2c' AND week >= ?", (self.company_id, current_week - 4))
        avg_weekly_sales = sales_history['cnt'] / 4.0 if sales_history else 0
        
        # 市場全体の需要から「あるべき販売数」を推計 (負のスパイラル脱却用)
        my_industry = self.company['industry']
        retailer_count = db.fetch_one("SELECT COUNT(*) as cnt FROM companies WHERE type = 'npc_retail' AND is_active = 1 AND industry = ?", (my_industry,))['cnt']
        retailer_count = max(1, retailer_count)
        
        # 自社が扱っているカテゴリの総需要を取得 (簡易的にdemand_mapの平均を使用)
        avg_market_demand = sum(demand_map.values()) / max(1, len(demand_map)) if demand_map else 1000
        fair_share_sales = avg_market_demand / retailer_count

        # 予測販売数: 実績の1.2倍 または キャパシティの50%（初期）
        if current_week > 8:
            # 実績ベース と 潜在シェアベース の大きい方を採用 (売れてないときも強気に仕入れる)
            # 修正: Fair Shareの80%を下限とする (以前は50%)
            base_projection = max(avg_weekly_sales * 1.2, fair_share_sales * 0.8, 10)
            if self.phase == 'GROWTH': base_projection *= 1.5
            
            # キャパシティ制限: 成長期はキャパシティを超えても仕入れる（採用が追いつくことを見越す）
            cap_limit = sales_capacity * 1.5 if self.phase == 'GROWTH' else sales_capacity
            projected_sales = min(base_projection, cap_limit)
        else:
            projected_sales = sales_capacity * 0.5
            
        # 2. 目標在庫の設定 (予測販売数の6週分)
        target_stock_total = int(projected_sales * 6)
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
        
        # シェア低下時は新商品開発を急ぐ
        current_share = self.plan['stats'].get('current_share', 0)
        fair_share = self.plan['stats'].get('fair_share', 0.1)
        is_underperforming = current_share < fair_share * 0.8

        # 製品が2つ未満、またはランダム（新陳代謝）で新規開発
        if completed_count < 2 or random.random() < 0.05 or (is_underperforming and random.random() < 0.2):
            # コンセプト決定 (1.0 - 5.0)
            # 企業の得意分野などがまだないのでランダム
            # コンセプトスコア等は開発完了時にStrategyに基づいて決定するため、ここでは仮置き
            
            # サプライヤー選択
            # 事業部の業界・カテゴリ定義を取得
            ind_key = division['industry_key']
            ind_def = gb.INDUSTRIES[ind_key]
            
            # 市場トレンド（需要）の取得
            trends = db.fetch_one("SELECT b2c_demand FROM market_trends WHERE week = ? AND industry_key = ?", (current_week - 1, ind_key))
            demand = trends['b2c_demand'] if trends else ind_def['base_demand']
            
            # 競合製品数（供給）の取得
            supply_counts = db.fetch_one("SELECT COUNT(*) as cnt FROM product_designs WHERE status = 'completed' AND industry_key = ?", (ind_key,))
            supply = supply_counts['cnt'] if supply_counts else 0
            
            # 平均価格の取得（利益率計算用）
            # avg_prices = db.fetch_one("SELECT AVG(sales_price) as avg_price FROM product_designs WHERE status = 'completed' AND industry_key = ?", (ind_key,))
            # mkt_price = avg_prices['avg_price'] if avg_prices and avg_prices['avg_price'] else 0

            # カテゴリ選定ロジック削除 -> 業界固定
            
            parts_config = {}
            total_score = 0
            total_cost = 0
            parts_def = ind_def['parts']
            
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
                (company_id, division_id, industry_key, name, material_score, concept_score, production_efficiency, base_price, sales_price, status, strategy, developed_week, parts_config)
                VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 'developing', ?, ?, ?)
            """, (self.company_id, division['id'], ind_key, name, avg_material_score, strategy, current_week, json.dumps(parts_config)))
            db.log_file_event(current_week, self.company_id, "Development Start", f"Started development of {name}")
            db.increment_weekly_stat(current_week, self.company_id, 'development_ordered', 1)

    def decide_order_fulfillment(self, current_week, orders, inventory):
        """
        メーカー用: 受注処理
        届いている注文を確認し、在庫があれば受注(Accepted)する
        修正: 在庫不足時は部分納品を行う
        """
        if self.company['type'] != 'npc_maker': return

        if not orders: return

        # 在庫情報のマッピング (リスト内の辞書オブジェクトを直接参照する)
        inv_dict = {item['design_id']: item for item in inventory}

        for order in orders:
            did = order['design_id']
            qty = order['quantity']
            
            item = inv_dict.get(did)
            
            if item and item['quantity'] > 0:
                # 部分納品対応
                fulfill_qty = min(qty, item['quantity'])
                
                # 金額の再計算 (単価 * 納品数)
                unit_price = order['amount'] / qty if qty > 0 else 0
                new_amount = int(unit_price * fulfill_qty)

                # 注文情報を更新して受注
                if fulfill_qty < qty:
                    db.execute_query("UPDATE b2b_orders SET quantity = ?, amount = ?, status = 'accepted' WHERE id = ?", 
                                     (fulfill_qty, new_amount, order['id']))
                    db.log_file_event(current_week, self.company_id, "B2B Partial Accept", f"Partially Accepted Order ID {order['id']} ({fulfill_qty}/{qty} units)")
                else:
                    db.execute_query("UPDATE b2b_orders SET status = 'accepted' WHERE id = ?", (order['id'],))
                    db.log_file_event(current_week, self.company_id, "B2B Accept", f"Accepted Order ID {order['id']} ({qty} units)")
                
                # メモリ上の在庫を即座に減らす
                item['quantity'] -= fulfill_qty
                
            else:
                # 在庫ゼロのため拒否
                db.execute_query("UPDATE b2b_orders SET status = 'rejected' WHERE id = ?", (order['id'],))
                db.log_file_event(current_week, self.company_id, "B2B Reject", f"Rejected Order ID {order['id']} (No Stock)")

    def decide_facilities(self, current_week):
        """
        施設管理: 従業員数に合わせて施設を確保する。過剰な場合は解約する。
        """
        # 部署ごとの従業員数を集計
        if not self.employees: return

        dept_counts = {}
        for emp in self.employees:
            d = emp['department']
            dept_counts[d] = dept_counts.get(d, 0) + 1

        # 必要な施設タイプと人数 (計画ベース)
        plan_factory = self.plan['required_facility'].get('factory', 0)
        plan_store = self.plan['required_facility'].get('store', 0)
        plan_office = self.plan['required_facility'].get('office', 0)

        # 現在の従業員を収容するのに最低限必要なサイズ (現状ベース)
        # 工場: 生産部
        curr_factory = dept_counts.get(gb.DEPT_PRODUCTION, 0)
        # 店舗: 店舗部
        curr_store = dept_counts.get(gb.DEPT_STORE, 0)
        # オフィス: その他全員 (生産・店舗以外)
        curr_office = sum(count for d, count in dept_counts.items() if d not in [gb.DEPT_PRODUCTION, gb.DEPT_STORE])

        # 拡張判断用: 現在の人員数のみに基づく (施設は即時確保できるため、先行投資しない)
        factory_needs_acquire = curr_factory
        store_needs_acquire = curr_store
        office_needs_acquire = curr_office

        # 縮小判断用: 計画と現状の大きい方 (将来の計画があるなら維持する)
        factory_needs_keep = max(plan_factory, curr_factory)
        store_needs_keep = max(plan_store, curr_store)
        office_needs_keep = max(plan_office, curr_office)

        # CRISIS時は拡張しない
        if self.phase == 'CRISIS': return

        # 現在の施設容量を確認
        facilities = db.fetch_all("SELECT id, type, size, rent, is_owned FROM facilities WHERE company_id = ?", (self.company_id,))
        current_cap = {'factory': 0, 'store': 0, 'office': 0}
        owned_facilities = {'factory': [], 'store': [], 'office': []}
        rented_facilities = {'factory': [], 'store': [], 'office': []}

        for fac in facilities:
            if fac['type'] in current_cap:
                current_cap[fac['type']] += fac['size']
                if fac['is_owned']:
                    owned_facilities[fac['type']].append(fac)
                else:
                    rented_facilities[fac['type']].append(fac)
        
        # ターゲット事業部 (NPCは単一事業部と仮定)
        target_div_id = self.divisions[0]['id'] if self.divisions else None

        # 不足分を計算して契約 (賃貸)
        def acquire_facility(ftype, needed_raw, current, rent_unit_price):
            needed_scaled = needed_raw * gb.NPC_SCALE_FACTOR
            # 余裕を持たせる (1.2倍)
            target_cap = int(needed_scaled * 1.2)
            
            if target_cap > current:
                shortage = target_cap - current
                
                # 空き物件を探す (不足分を埋めるために、大きい順に取得して埋めていく)
                # 修正: 1つの物件で満たそうとせず、複数の物件を組み合わせる
                available_list = db.fetch_all("""
                    SELECT id, rent, size FROM facilities 
                    WHERE company_id IS NULL AND type = ? 
                    ORDER BY size DESC, rent ASC
                """, (ftype,))
                
                for available in available_list:
                    if shortage <= 0: break

                    # 購入判断: 資金に余裕があれば購入する
                    # 施設割り当て: NPCは単一事業部制を基本とするため、すべての施設をターゲット事業部に割り当てる
                    assign_div_id = target_div_id
                    # オフィスは全社共通(Corporate)として割り当てることで、人事部等のスペースを確保する
                    if ftype == 'office': assign_div_id = None

                    # 余裕基準: 購入後も資金が1億円以上残る
                    purchase_price = available['rent'] * gb.FACILITY_PURCHASE_MULTIPLIER
                    
                    if self.company['funds'] > purchase_price + 100000000:
                        # 購入
                        db.execute_query("UPDATE facilities SET company_id = ?, division_id = ?, is_owned = 1 WHERE id = ?", 
                                         (self.company_id, assign_div_id, available['id']))
                        db.execute_query("UPDATE companies SET funds = funds - ? WHERE id = ?", (purchase_price, self.company_id))
                        db.execute_query("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, 'facility_purchase', ?)",
                                         (current_week, self.company_id, purchase_price))
                        
                        # メモリ上の資金も更新して、ループ内の次回の判定に反映させる (過剰購入防止)
                        self.company['funds'] -= purchase_price
                        
                        db.log_file_event(current_week, self.company_id, "Facility", f"Purchased {ftype} (Size: {available['size']})")
                    else:
                        # 賃貸
                        db.execute_query("UPDATE facilities SET company_id = ?, division_id = ?, is_owned = 0 WHERE id = ?", 
                                         (self.company_id, assign_div_id, available['id']))
                        db.log_file_event(current_week, self.company_id, "Facility", f"Rented {ftype} (Size: {available['size']})")
                    
                    shortage -= available['size']

        acquire_facility('factory', factory_needs_acquire, current_cap['factory'], gb.RENT_FACTORY)
        acquire_facility('store', store_needs_acquire, current_cap['store'], gb.RENT_STORE_BASE)
        acquire_facility('office', office_needs_acquire, current_cap['office'], gb.RENT_OFFICE)

        # 2. 縮小ロジック (過剰分を解約)
        # 稼働率が50%を下回る場合、賃貸物件を解約する
        def release_facility(ftype, needed_raw, current, rented_list):
            needed_scaled = needed_raw * gb.NPC_SCALE_FACTOR
            # 許容範囲 (必要量の1.5倍までは保持)
            max_keep_cap = int(needed_scaled * 1.5)
            
            if current > max_keep_cap and rented_list:
                # 解約候補: サイズが大きい順に解約を検討（一気に減らす）
                rented_list.sort(key=lambda x: x['size'], reverse=True)
                
                excess = current - max_keep_cap
                
                for fac in rented_list:
                    if excess >= fac['size'] * 0.8: # 8割以上過剰なら解約
                        db.execute_query("UPDATE facilities SET company_id = NULL, division_id = NULL, is_owned = 0 WHERE id = ?", (fac['id'],))
                        db.log_file_event(current_week, self.company_id, "Facility Release", f"Released {ftype} (Size: {fac['size']})")
                        excess -= fac['size']
                        if excess <= 0: break

        release_facility('factory', factory_needs_keep, current_cap['factory'], rented_facilities['factory'])
        release_facility('store', store_needs_keep, current_cap['store'], rented_facilities['store'])
        release_facility('office', office_needs_keep, current_cap['office'], rented_facilities['office'])

    def decide_advertising(self, current_week):
        """
        広告戦略: 資金に余裕があればブランド広告や商品広告を打つ
        """
        if self.phase == 'CRISIS': return

        # 予算設定の適正化: 売上高連動型へ変更
        # 直近の売上を取得 (簡易的にaccount_entriesから)
        recent_revenue_row = db.fetch_one("SELECT SUM(amount) as val FROM account_entries WHERE company_id = ? AND category = 'revenue' AND week = ?", (self.company_id, current_week - 1))
        recent_revenue = recent_revenue_row['val'] if recent_revenue_row and recent_revenue_row['val'] else 0
        
        # 基本予算: 売上の10%。売上がない場合は手持ち資金の0.5%または200万円の小さい方（最低限の露出維持）
        # シェア低下時は予算を増額する (対抗策)
        current_share = self.plan['stats'].get('current_share', 0)
        fair_share = self.plan['stats'].get('fair_share', 0.1)
        is_underperforming = current_share < fair_share * 0.8
        
        base_rate = 0.20 if is_underperforming else 0.10
        
        if recent_revenue > 0:
            budget = min(recent_revenue * base_rate, self.company['funds'] * 0.05, 100000000)
        else:
            budget = min(self.company['funds'] * 0.01, 5000000)
            
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

            current_share = self.plan['stats'].get('current_share', 0)
            fair_share = self.plan['stats'].get('fair_share', 0.1)
            is_underperforming = current_share < fair_share * 0.8

            for p in completed_designs:
                # 現在在庫
                stock_item = next((inv for inv in inventory if inv['design_id'] == p['id']), None)
                current_qty = stock_item['quantity'] if stock_item else 0
                
                # 直近4週間のB2B売上数
                sales_history_item = next((s for s in b2b_sales_history if s['seller_id'] == self.company_id and s['design_id'] == p['id']), None)
                max_weekly_sales = sales_history_item['max_weekly'] if sales_history_item else 0
                # avg_sales_qty = sales_qty_4w / 4.0 -> max_weekly_sales を基準にする
                
                # 競合価格の調査
                ind_key = p['industry_key']
                competitor_prices = db.fetch_all("SELECT sales_price FROM product_designs WHERE industry_key = ? AND status='completed' AND company_id != ?", (ind_key, self.company_id))
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
                if (current_qty > overstock_threshold and max_weekly_sales < (5 * patience)) or (is_underperforming and current_qty > overstock_threshold * 0.5):
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
                    
                elif current_qty < shortage_threshold and max_weekly_sales > (10 / patience):
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
