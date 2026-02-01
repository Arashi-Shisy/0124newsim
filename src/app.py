# c:\0124newSIm\src\app.py
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from markupsafe import Markup
import os
import json
import random
import sqlite3
from database import db
from simulation import Simulation
import gamebalance as gb

app = Flask(__name__)
app.secret_key = 'newsim_secret_key'

# シミュレーションインスタンス
sim = Simulation()

def get_player_company():
    """プレイヤー企業を取得する"""
    res = db.fetch_one("SELECT * FROM companies WHERE type = 'player' LIMIT 1")
    return res

@app.context_processor
def inject_common_data():
    """全テンプレートで共通して使えるデータを注入"""
    player = get_player_company()
    game_state = db.fetch_one("SELECT * FROM game_state")
    current_week = game_state['week'] if game_state else 0
    
    # 日付表示（週数から年月を簡易計算: 1月1週スタートと仮定）
    year = 2025 + (current_week - 1) // 52
    week_of_year = (current_week - 1) % 52 + 1
    date_str = f"{year}年 Week {week_of_year}"

    # プレイヤーの事業部リスト
    player_divisions = []
    if player:
        player_divisions = db.fetch_all("SELECT * FROM divisions WHERE company_id = ?", (player['id'],))

    # ヘッダー用企業能力データ
    header_caps = None
    if player:
        header_caps = sim.calculate_capabilities(player['id'])

    return dict(
        player=player,
        current_week=current_week,
        date_str=date_str,
        active_page=request.endpoint,
        header_caps=header_caps,
        npc_scale=gb.NPC_SCALE_FACTOR,
        player_divisions=player_divisions
    )

def get_ability_bounds(value, hr_power):
    """能力値と人事力から、表示範囲(low, high)を計算する共通関数"""
    # 誤差範囲: 人事力0で40(±20), 人事力100で4(±2)
    # 0-100の範囲に収める
    width = 40 - (36 * (min(100, max(0, hr_power)) / 100.0))
    
    # 週と値に基づいてシードを決定（週が変わると表示範囲も変わる＝再評価される）
    current_week = sim.get_current_week()
    seed = (current_week * 1000) + value
    rng = random.Random(seed)
    
    # 真の値が範囲内のどこに来るかをランダムに決定 (0.0 ~ 1.0)
    bias = rng.random()
    
    # 範囲の計算 (value = low + width * bias)
    low = value - (width * bias)
    high = low + width
    
    # 0-100の範囲に収める（幅を維持するようにスライド）
    if low < 0:
        low = 0
        high = width
    elif high > 100:
        high = 100
        low = 100 - width
        
    return max(0, int(low)), min(100, int(high))

@app.template_filter('ability_range')
def ability_range_filter(value, hr_power):
    """能力値を人事能力に応じた範囲表示文字列に変換する"""
    if value is None: return "-"
    low, high = get_ability_bounds(value, hr_power)
    return f"{low}-{high}"

def get_ability_color(value):
    """能力値に応じた色コードを返す"""
    if value < 40: return "#777777" # グレー
    if value <= 50: return "#cccccc" # 明るいグレー
    if value <= 60: return "#ffffff" # 白
    if value <= 70: return "#4caf50" # 緑
    if value <= 80: return "#b2ff59" # 明るい緑
    if value <= 90: return "#ffeb3b" # 黄色
    return "#ff5252" # 赤 (91以上)

@app.template_filter('ability_range_colored')
def ability_range_colored_filter(value, hr_power):
    """能力値を人事能力に応じた色付き範囲表示HTMLに変換する"""
    if value is None: return "-"
    low, high = get_ability_bounds(value, hr_power)
    
    low_color = get_ability_color(low)
    high_color = get_ability_color(high)
    
    return Markup(f'<span style="color: {low_color}">{low}</span>-<span style="color: {high_color}">{high}</span>')

@app.template_filter('perceived_value')
def perceived_value_filter(value, hr_power):
    """ソート用に、表示範囲の中央値（推定値）を返す"""
    if value is None: return 0
    low, high = get_ability_bounds(value, hr_power)
    
    return (low + high) / 2.0

@app.template_filter('format_week')
def format_week_filter(week):
    """通算週数を 'YYYY年 Week N' 形式に変換する"""
    if week is None: return "-"
    try:
        week = int(week)
    except:
        return week
    year = 2025 + (week - 1) // 52
    week_of_year = (week - 1) % 52 + 1
    return f"{year}年 Week {week_of_year}"

@app.template_filter('json_load')
def json_load_filter(value):
    if not value: return {}
    return json.loads(value)

@app.route('/')
def dashboard():
    player = get_player_company()
    if not player:
        return "Player company not found. Please run seed.py first."
    
    current_week = sim.get_current_week()
    
    # ダッシュボード用データの取得
    # 1. 資金
    funds = player['funds']
    
    # 2. 従業員数
    emp_count = db.fetch_one("SELECT COUNT(*) as cnt FROM npcs WHERE company_id = ?", (player['id'],))['cnt']
    # 2.5 平均忠誠度
    avg_loyalty_res = db.fetch_one("SELECT AVG(loyalty) as val FROM npcs WHERE company_id = ?", (player['id'],))
    avg_loyalty = avg_loyalty_res['val'] if avg_loyalty_res and avg_loyalty_res['val'] else 0

    # 3. アラート
    alerts = []
    if funds < 0:
        alerts.append("資金がマイナスです！倒産の危機です。")
    
    # 4. 未承認のB2B注文チェック
    pending_orders = db.fetch_one("SELECT COUNT(*) as cnt FROM b2b_orders WHERE seller_id = ? AND status = 'pending'", (player['id'],))['cnt']
    if pending_orders > 0:
        alerts.append(f"未承認の注文が {pending_orders} 件あります。「営業」画面で確認してください。")
    
    # 5. ニュース（直近のログ）
    news = db.fetch_all("SELECT * FROM news_logs WHERE week = ? ORDER BY id DESC LIMIT 5", (current_week - 1,))
    
    # 6. 世界情勢
    game_state = db.fetch_one("SELECT economic_index FROM game_state")
    economic_status = "好景気" if game_state['economic_index'] > 1.1 else "不景気" if game_state['economic_index'] < 0.9 else "普通"
    
    # 7. 労働市場
    total_npcs = db.fetch_one("SELECT COUNT(*) as cnt FROM npcs")['cnt']
    unemployed_npcs = db.fetch_one("SELECT COUNT(*) as cnt FROM npcs WHERE company_id IS NULL")['cnt']
    unemployment_rate = (unemployed_npcs / total_npcs) * 100 if total_npcs > 0 else 0

    # 8. 生産サマリ
    prod_stats = db.fetch_one("SELECT production_ordered FROM weekly_stats WHERE week = ? AND company_id = ?", (current_week, player['id']))
    production_this_week = prod_stats['production_ordered'] if prod_stats else 0

    # 9. 開発サマリ
    developing_project = db.fetch_one("SELECT name FROM product_designs WHERE company_id = ? AND status = 'developing'", (player['id'],))

    # 10. 在庫サマリ
    inventory_details = db.fetch_all("""
        SELECT d.name as product_name, i.quantity
        FROM inventory i JOIN product_designs d ON i.design_id = d.id
        WHERE i.company_id = ? AND i.quantity > 0
        ORDER BY i.quantity DESC
    """, (player['id'],))
    total_inventory = sum(item['quantity'] for item in inventory_details)

    dashboard_data = {
        "funds": funds,
        "emp_count": emp_count,
        "avg_loyalty": avg_loyalty,
        "alerts": alerts,
        "news": news,
        "economic_status": economic_status,
        "unemployment_rate": unemployment_rate,
        "production_this_week": production_this_week,
        "developing_project": developing_project,
        "pending_orders": pending_orders,
        "total_inventory": total_inventory,
        "inventory_details": inventory_details
    }
    return render_template('dashboard.html', **dashboard_data)

@app.route('/hr')
def hr():
    player = get_player_company()
    
    # 人事能力の取得（表示誤差計算用）
    caps = sim.calculate_capabilities(player['id'])
    
    # 従業員一覧
    employees = db.fetch_all("SELECT * FROM npcs WHERE company_id = ?", (player['id'],))
    
    # 部署リスト
    departments = gb.DEPARTMENTS

    # 部署別統計の集計
    dept_stats = {}
    for d in departments:
        count = len([e for e in employees if e['department'] == d])
        space = count * gb.NPC_SCALE_FACTOR
        dept_stats[d] = {'count': count, 'space': space}
    
    return render_template('hr.html', employees=employees, departments=departments, caps=caps, hr_power=caps['hr'], npc_scale=gb.NPC_SCALE_FACTOR, dept_stats=dept_stats)

@app.route('/hire')
def hire_page():
    player = get_player_company()
    
    # 人事能力の取得（表示誤差計算用）
    caps = sim.calculate_capabilities(player['id'])
    
    # 候補者一覧（労働市場）
    candidates = db.fetch_all("SELECT * FROM npcs WHERE company_id IS NULL")
    
    # 交渉中（オファー済み）の候補者取得
    offers = db.fetch_all("""
        SELECT j.*, n.name, n.age, n.desired_salary as current_desired, 
               n.diligence, n.adaptability, n.production, n.store_ops, n.sales, n.hr, n.development, n.pr, n.accounting, n.management
        FROM job_offers j
        JOIN npcs n ON j.npc_id = n.id
        WHERE j.company_id = ?
    """, (player['id'],))

    offered_npc_ids = [o['npc_id'] for o in offers]
    departments = gb.DEPARTMENTS
    
    return render_template('hire.html', candidates=candidates, offers=offers, offered_npc_ids=offered_npc_ids, departments=departments, hr_power=caps['hr'])

@app.route('/hr/change_dept', methods=['POST'])
def hr_change_dept():
    npc_id = request.form.get('npc_id')
    new_dept = request.form.get('new_dept')
    new_div_id = request.form.get('new_division_id') # Can be empty (None) for corporate depts
    new_role = request.form.get('new_role')
    
    player = get_player_company()

    if npc_id and new_dept:
        # 役職制限チェック (部長・部長補佐は各部署1人まで)
        if new_role in [gb.ROLE_MANAGER, gb.ROLE_ASSISTANT_MANAGER]:
            existing = db.fetch_one("""
                SELECT id, name FROM npcs 
                WHERE company_id = ? AND department = ? AND role = ? AND id != ?
            """, (player['id'], new_dept, new_role, npc_id))
            
            if existing:
                flash(f"{new_dept}には既に{existing['name']}が{new_role}として着任しています。各部署1名までです。", "error")
                return redirect(url_for('hr'))

        # division_idが空文字列ならNoneにする
        if not new_div_id:
            new_div_id = None

        db.execute_query("UPDATE npcs SET department = ?, division_id = ?, role = ? WHERE id = ?", (new_dept, new_div_id, new_role, npc_id))
        flash(f"人事異動を発令しました。", "success")
    
    return redirect(url_for('hr'))

@app.route('/hr/change_salary', methods=['POST'])
def hr_change_salary():
    npc_id = request.form.get('npc_id')
    new_salary = request.form.get('new_salary')
    
    if npc_id and new_salary:
        try:
            salary_int = int(new_salary)
            db.execute_query("UPDATE npcs SET salary = ? WHERE id = ?", (salary_int, npc_id))
            flash(f"給与改定を行いました。", "success")
        except ValueError:
            flash("給与には数値を入力してください。", "error")
            
    return redirect(url_for('hr'))

@app.route('/hr/fire', methods=['POST'])
def hr_fire():
    npc_id = request.form.get('npc_id')
    current_week = sim.get_current_week()
    player = get_player_company()
    
    if npc_id:
        db.execute_query("""
            UPDATE npcs SET company_id = NULL, department = NULL, role = NULL, 
            last_resigned_week = ?, last_company_id = ?, loyalty = 50 
            WHERE id = ?
        """, (current_week, player['id'], npc_id))
        flash("解雇しました。", "warning")
        
    return redirect(url_for('hr'))

@app.route('/hr/hire', methods=['POST'])
def hr_hire():
    npc_id = request.form.get('npc_id')
    offer_salary = request.form.get('offer_salary')
    target_dept = request.form.get('target_dept')
    current_week = sim.get_current_week()
    player = get_player_company()
    
    if npc_id and offer_salary:
        # オファー発行
        db.execute_query("""
            INSERT INTO job_offers (week, company_id, npc_id, offer_salary, target_dept)
            VALUES (?, ?, ?, ?, ?)
        """, (current_week, player['id'], npc_id, offer_salary, target_dept))
        flash("採用オファーを出しました。来週結果がわかります。", "info")
        
    return redirect(url_for('hire_page'))

@app.route('/hr/hire_bulk', methods=['POST'])
def hr_hire_bulk():
    # JSONデータを受け取る
    data = request.get_json()
    offers = data.get('offers', [])
    current_week = sim.get_current_week()
    player = get_player_company()
    
    # 実行用パラメータリストを作成
    params = []
    for offer in offers:
        npc_id = offer.get('npc_id')
        offer_salary = offer.get('offer_salary')
        target_dept = offer.get('target_dept')
        if npc_id and offer_salary:
            params.append((current_week, player['id'], npc_id, offer_salary, target_dept))
    
    count = len(params)
    if count > 0:
        with db.transaction() as conn:
            conn.cursor().executemany("""
                INSERT INTO job_offers (week, company_id, npc_id, offer_salary, target_dept)
                VALUES (?, ?, ?, ?, ?)
            """, params)
    
    flash(f"{count}名の候補者に一括オファーを出しました。", "success")
    return jsonify({'status': 'success', 'count': count})

@app.route('/production')
def production():
    player = get_player_company()
    
    # 選択された事業部 (デフォルトは最初の事業部)
    division_id = request.args.get('division_id')
    divisions = db.fetch_all("SELECT * FROM divisions WHERE company_id = ?", (player['id'],))
    
    if not divisions:
        return "No divisions found", 500
        
    if not division_id:
        division_id = divisions[0]['id']
    else:
        division_id = int(division_id)
        
    selected_division = next((d for d in divisions if d['id'] == division_id), divisions[0])
    
    # 能力とキャパシティの計算
    caps = sim.calculate_capabilities(player['id'])
    # 事業部ごとの能力を取得
    div_caps = caps.get('divisions', {}).get(division_id, {})
    
    # 完了済みの設計書（この事業部の生産可能な製品）
    designs = db.fetch_all("SELECT * FROM product_designs WHERE company_id = ? AND division_id = ? AND status = 'completed'", (player['id'], division_id))
    
    # 現在の在庫 (この事業部)
    inventory = db.fetch_all("""
        SELECT i.*, d.name as product_name 
        FROM inventory i JOIN product_designs d ON i.design_id = d.id
        WHERE i.company_id = ? AND i.division_id = ?
    """, (player['id'], division_id))
    inv_map = {i['design_id']: i['quantity'] for i in inventory}
    
    # 今週の生産済み数 (全社合計しか取れないため、簡易的に表示。本来は事業部ごとにログを取るべき)
    # ここでは「事業部キャパシティ」を表示し、使用量は「全社生産数」として参考表示するに留める
    stats = db.fetch_one("SELECT production_ordered FROM weekly_stats WHERE week = ? AND company_id = ?", (sim.get_current_week(), player['id']))
    current_produced = stats['production_ordered'] if stats else 0
    
    # 工場キャパシティ（この事業部の施設サイズ）
    facilities = db.fetch_all("SELECT size FROM facilities WHERE company_id = ? AND division_id = ? AND type = 'factory'", (player['id'], division_id))
    total_factory_size = sum(f['size'] for f in facilities)
    
    # 生産効率 (能力値 / 50 * 基準効率)
    # div_caps['production'] を使用
    prod_skill = div_caps.get('production', 0)
    efficiency = (prod_skill / 50.0) * gb.BASE_PRODUCTION_EFFICIENCY
    
    # 最大生産可能数 (人 * 効率) - 既に生産した分
    # 工場サイズ分の人数しか働けない
    prod_staff_count = len(db.fetch_all("SELECT id FROM npcs WHERE company_id = ? AND division_id = ? AND department = ?", (player['id'], division_id, gb.DEPT_PRODUCTION)))
    effective_staff_entities = min(prod_staff_count, int(total_factory_size // gb.NPC_SCALE_FACTOR))
    
    max_capacity = int(effective_staff_entities * gb.NPC_SCALE_FACTOR * efficiency)
    
    # 注意: current_producedは全社合計なので、事業部ごとの残キャパシティを正確には反映していない可能性があるが、
    # UI上は「この事業部の最大能力」を表示する。
    remaining_capacity = max_capacity # 簡易化: 毎回フルパワー出せると仮定（使用量減算は複雑なため省略）

    # 在庫サマリ
    total_inventory = sum(inv_map.values())
    inventory_value = sum(i['quantity'] * i['sales_price'] for i in inventory)

    return render_template('production.html', 
                           designs=designs, 
                           divisions=divisions,
                           selected_division=selected_division,
                           inv_map=inv_map, 
                           div_caps=div_caps, 
                           remaining_capacity=remaining_capacity,
                           max_capacity=max_capacity,
                           current_produced=current_produced, # 参考値
                           total_inventory=total_inventory,
                           inventory_value=inventory_value,
                           inventory=inventory)

@app.route('/production/order', methods=['POST'])
def production_order():
    player = get_player_company()
    design_id = request.form.get('design_id')
    division_id = request.form.get('division_id')
    quantity = int(request.form.get('quantity', 0))
    current_week = sim.get_current_week()
    
    if quantity > 0:
        # コスト計算
        design = db.fetch_one("SELECT * FROM product_designs WHERE id = ?", (design_id,))
        parts_config = json.loads(design['parts_config'])
        unit_cost = sum(p['cost'] for p in parts_config.values())
        total_cost = unit_cost * quantity
        
        if player['funds'] >= total_cost:
            # 資金消費
            db.execute_query("UPDATE companies SET funds = funds - ? WHERE id = ?", (total_cost, player['id']))
            # 在庫追加
            existing = db.fetch_one("SELECT id FROM inventory WHERE company_id = ? AND division_id = ? AND design_id = ?", (player['id'], division_id, design_id))
            if existing:
                db.execute_query("UPDATE inventory SET quantity = quantity + ? WHERE id = ?", (quantity, existing['id']))
            else:
                db.execute_query("INSERT INTO inventory (company_id, division_id, design_id, quantity, sales_price) VALUES (?, ?, ?, ?, ?)", 
                                 (player['id'], division_id, design_id, quantity, design['sales_price']))
            
            # 統計更新
            db.increment_weekly_stat(current_week, player['id'], 'production_ordered', quantity)
            db.execute_query("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, 'material', ?)",
                             (current_week, player['id'], total_cost))
            
            flash(f"{design['name']} を {quantity}台 生産しました。", "success")
        else:
            flash("資金が不足しています。", "error")
            
    return redirect(url_for('production', division_id=division_id))

@app.route('/store')
def store():
    player = get_player_company()
    caps = sim.calculate_capabilities(player['id'])
    
    # 店舗一覧
    stores = db.fetch_all("SELECT * FROM facilities WHERE company_id = ? AND type = 'store'", (player['id'],))
    
    # 在庫（店頭に並ぶ商品）
    inventory = db.fetch_all("""
        SELECT i.*, d.name as product_name, d.sales_price as msrp 
        FROM inventory i JOIN product_designs d ON i.design_id = d.id 
        WHERE i.company_id = ?
    """, (player['id'],))
    
    return render_template('store.html', stores=stores, inventory=inventory, caps=caps)

@app.route('/sales')
def sales():
    player = get_player_company()
    
    # 受注待ちの注文 (B2B)
    pending_orders = db.fetch_all("""
        SELECT o.*, c.name as buyer_name, d.name as product_name 
        FROM b2b_orders o 
        JOIN companies c ON o.buyer_id = c.id 
        JOIN product_designs d ON o.design_id = d.id
        WHERE o.seller_id = ? AND o.status = 'pending'
    """, (player['id'],))
    
    # 受注済み(未納品)の注文 (B2B)
    accepted_orders = db.fetch_all("""
        SELECT o.*, c.name as buyer_name, d.name as product_name 
        FROM b2b_orders o 
        JOIN companies c ON o.buyer_id = c.id 
        JOIN product_designs d ON o.design_id = d.id
        WHERE o.seller_id = ? AND o.status = 'accepted'
    """, (player['id'],))
    
    # 取引履歴
    history = db.fetch_all("""
        SELECT t.*, c.name as partner_name, d.name as product_name
        FROM transactions t
        LEFT JOIN companies c ON (t.buyer_id = c.id AND t.seller_id = ?) OR (t.seller_id = c.id AND t.buyer_id = ?)
        LEFT JOIN product_designs d ON t.design_id = d.id
        WHERE (t.seller_id = ? OR t.buyer_id = ?) AND t.type = 'b2b'
        ORDER BY t.id DESC LIMIT 20
    """, (player['id'], player['id'], player['id'], player['id']))
    
    # 仕入れ市場 (他社メーカーの在庫)
    market_stocks = db.fetch_all("""
        SELECT i.quantity, i.design_id, d.name as product_name, d.sales_price, d.concept_score, 
               i.company_id as seller_id, c.name as seller_name, c.brand_power
        FROM inventory i
        JOIN product_designs d ON i.design_id = d.id
        JOIN companies c ON i.company_id = c.id
        WHERE c.type IN ('npc_maker', 'player') AND c.id != ? AND c.is_active = 1 AND i.quantity > 0
    """, (player['id'],))
    
    # 自社在庫サマリ
    my_inventory = db.fetch_all("""
        SELECT i.quantity, d.name as product_name, d.sales_price, i.design_id
        FROM inventory i JOIN product_designs d ON i.design_id = d.id 
        WHERE i.company_id = ? AND i.quantity > 0
    """, (player['id'],))
    total_inventory = sum(i['quantity'] for i in my_inventory)

    return render_template('sales.html', pending_orders=pending_orders, accepted_orders=accepted_orders, history=history, market_stocks=market_stocks, my_inventory=my_inventory, total_inventory=total_inventory)

@app.route('/sales/action', methods=['POST'])
def sales_action():
    order_id = request.form.get('order_id')
    action = request.form.get('action') # accept or reject
    
    if order_id and action:
        status = 'accepted' if action == 'accept' else 'rejected'
        db.execute_query("UPDATE b2b_orders SET status = ? WHERE id = ?", (status, order_id))
        flash(f"注文を{'受注' if status=='accepted' else '拒否'}しました。", "info")
        
    return redirect(url_for('sales'))

@app.route('/sales/pricing', methods=['POST'])
def sales_pricing():
    player = get_player_company()
    design_id = request.form.get('design_id')
    new_price = request.form.get('new_price')
    
    if design_id and new_price:
        try:
            price_int = int(new_price)
            if price_int > 0:
                db.execute_query("UPDATE product_designs SET sales_price = ? WHERE id = ? AND company_id = ?", (price_int, design_id, player['id']))
                flash(f"販売価格を ¥{price_int:,} に改定しました。", "success")
        except ValueError:
            flash("価格には数値を入力してください。", "error")
            
    return redirect(url_for('sales'))

@app.route('/sales/buy', methods=['POST'])
def sales_buy():
    player = get_player_company()
    seller_id = request.form.get('seller_id')
    design_id = request.form.get('design_id')
    quantity = int(request.form.get('quantity', 0))
    price = int(request.form.get('price', 0))
    current_week = sim.get_current_week()
    
    if quantity > 0:
        amount = quantity * price
        if player['funds'] >= amount:
            db.execute_query("""
                INSERT INTO b2b_orders (week, buyer_id, seller_id, design_id, quantity, amount, status)
                VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """, (current_week, player['id'], seller_id, design_id, quantity, amount))
            flash(f"発注を行いました (承認待ち): {quantity}台", "success")
        else:
            flash("資金が不足しています。", "error")
            
    return redirect(url_for('sales'))

@app.route('/dev')
def dev():
    player = get_player_company()
    current_week = sim.get_current_week()
    
    # 開発中のプロジェクト
    developing = db.fetch_all("SELECT * FROM product_designs WHERE company_id = ? AND status = 'developing'", (player['id'],))
    
    # 完了済み
    completed = db.fetch_all("SELECT * FROM product_designs WHERE company_id = ? AND status = 'completed' ORDER BY id DESC", (player['id'],))
    
    # サプライヤー全取得 (JSでフィルタリングするため)
    all_suppliers = db.fetch_all("SELECT * FROM companies WHERE type = 'system_supplier'")
    suppliers_json = {}
    for s in all_suppliers:
        cat = s['part_category']
        if cat not in suppliers_json: suppliers_json[cat] = []
        suppliers_json[cat].append({
            'id': s['id'], 'name': s['name'], 
            'score': s['trait_material_score'], 'cost_mult': s['trait_cost_multiplier']
        })
        
    strategies = gb.DEV_STRATEGIES
    
    return render_template('dev.html', developing=developing, completed=completed, 
                           industries=gb.INDUSTRIES, suppliers_json=json.dumps(suppliers_json), strategies=gb.DEV_STRATEGIES,
                           current_week=current_week,
                           req_per_project=gb.REQ_CAPACITY_DEV_PROJECT)

@app.route('/dev/start', methods=['POST'])
def dev_start():
    player = get_player_company()
    current_week = sim.get_current_week()
    
    name = request.form.get('name')
    strategy = request.form.get('strategy')
    division_id = request.form.get('division_id')
    industry_key = request.form.get('industry_key')
    category_key = request.form.get('category_key')
    
    # 選択されたカテゴリのパーツ定義を取得
    try:
        parts_def = gb.INDUSTRIES[industry_key]['categories'][category_key]['parts']
    except KeyError:
        flash("業界・カテゴリの選択が不正です。", "error")
        return redirect(url_for('dev'))

    # パーツ構成の構築
    parts_config = {}
    total_score = 0
    
    for part in parts_def:
        sup_id = request.form.get(f"part_{part['key']}")
        if sup_id:
            sup = db.fetch_one("SELECT * FROM companies WHERE id = ?", (sup_id,))
            p_cost = int(part['base_cost'] * sup['trait_cost_multiplier'])
            parts_config[part['key']] = {
                "supplier_id": sup['id'],
                "score": sup['trait_material_score'],
                "cost": p_cost
            }
            total_score += sup['trait_material_score']
            
    avg_material_score = total_score / len(parts_def)
    
    db.execute_query("""
        INSERT INTO product_designs 
        (company_id, division_id, category_key, name, material_score, concept_score, production_efficiency, base_price, sales_price, status, strategy, developed_week, parts_config)
        VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 'developing', ?, ?, ?)
    """, (player['id'], division_id, category_key, name, avg_material_score, strategy, current_week, json.dumps(parts_config)))
    
    flash(f"新製品 {name} の開発を開始しました。", "success")
    return redirect(url_for('dev'))

@app.route('/next_week', methods=['POST'])
def next_week():
    new_week = sim.proceed_week()
    flash(f"第{new_week}週に進みました。", "info")
    return redirect(request.referrer or url_for('dashboard'))

@app.route('/pr')
def pr():
    player = get_player_company()
    products = db.fetch_all("SELECT * FROM product_designs WHERE company_id = ? AND status = 'completed'", (player['id'],))
    return render_template('pr.html', products=products)

@app.route('/facility')
def facility():
    player = get_player_company()
    
    # 保有/賃貸中の施設
    my_facilities = db.fetch_all("""
        SELECT f.*, d.name as division_name 
        FROM facilities f 
        LEFT JOIN divisions d ON f.division_id = d.id
        WHERE f.company_id = ?
    """, (player['id'],))
    
    # 市場の空き物件
    market_facilities = db.fetch_all("SELECT * FROM facilities WHERE company_id IS NULL LIMIT 50")
    
    return render_template('facility.html', my_facilities=my_facilities, market_facilities=market_facilities, purchase_multiplier=gb.FACILITY_PURCHASE_MULTIPLIER)

@app.route('/facility/contract', methods=['POST'])
def facility_contract():
    player = get_player_company()
    division_id = request.form.get('division_id')
    facility_id = request.form.get('facility_id')
    action = request.form.get('action') # 'rent' or 'buy'
    
    if facility_id and action:
        # 物件が空いているか確認
        fac = db.fetch_one("SELECT * FROM facilities WHERE id = ? AND company_id IS NULL", (facility_id,))
        if fac:
            # division_idが空文字ならNone (本社扱い)
            if not division_id: division_id = None

            if action == 'rent':
                # 契約処理 (賃貸)
                db.execute_query("UPDATE facilities SET company_id = ?, division_id = ?, is_owned = 0 WHERE id = ?", (player['id'], division_id, facility_id))
                flash(f"{fac['name']} (賃料: ¥{fac['rent']:,}/週) を賃貸契約しました。", "success")
            elif action == 'buy':
                # 購入処理
                purchase_price = fac['rent'] * gb.FACILITY_PURCHASE_MULTIPLIER
                if player['funds'] >= purchase_price:
                    db.execute_query("UPDATE facilities SET company_id = ?, division_id = ?, is_owned = 1 WHERE id = ?", (player['id'], division_id, facility_id))
                    db.execute_query("UPDATE companies SET funds = funds - ? WHERE id = ?", (purchase_price, player['id']))
                    db.execute_query("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, 'facility_purchase', ?)",
                                     (sim.get_current_week(), player['id'], purchase_price))
                    flash(f"{fac['name']} を ¥{purchase_price:,} で購入しました。", "success")
                else:
                    flash("資金が不足しています。", "error")
        else:
            flash("この物件は既に契約済みか、存在しません。", "error")
    
    return redirect(url_for('facility'))

@app.route('/facility/release', methods=['POST'])
def facility_release():
    player = get_player_company()
    facility_id = request.form.get('facility_id')
    action = request.form.get('action') # 'cancel' or 'sell'

    if facility_id and action:
        fac = db.fetch_one("SELECT * FROM facilities WHERE id = ? AND company_id = ?", (facility_id, player['id']))
        if fac:
            if action == 'cancel' and not fac['is_owned']:
                db.execute_query("UPDATE facilities SET company_id = NULL WHERE id = ?", (facility_id,))
                flash(f"{fac['name']} の賃貸契約を解約しました。", "info")
            elif action == 'sell' and fac['is_owned']:
                # 売却価格は購入価格の80%
                sell_price = int(fac['rent'] * gb.FACILITY_PURCHASE_MULTIPLIER * 0.8)
                db.execute_query("UPDATE facilities SET company_id = NULL, is_owned = 0 WHERE id = ?", (facility_id,))
                db.execute_query("UPDATE companies SET funds = funds + ? WHERE id = ?", (sell_price, player['id']))
                # 売却益として記録 (簡易的に facility_sell カテゴリ)
                db.execute_query("INSERT INTO account_entries (week, company_id, category, amount) VALUES (?, ?, 'facility_sell', ?)",
                                 (sim.get_current_week(), player['id'], sell_price))
                flash(f"{fac['name']} を ¥{sell_price:,} で売却しました。", "info")
    
    return redirect(url_for('facility'))

@app.route('/world')
def world():
    current_week = sim.get_current_week()
    target_week = max(1, current_week - 1)

    # 市場トレンド
    trends = db.fetch_all("SELECT * FROM market_trends ORDER BY week DESC LIMIT 10")
    # 企業ランキング
    ranking = db.fetch_all("SELECT * FROM companies WHERE type != 'system_supplier' AND is_active = 1 ORDER BY funds DESC")
    
    # 時価総額ランキング
    market_cap_ranking = db.fetch_all("SELECT * FROM companies WHERE type != 'system_supplier' AND is_active = 1 ORDER BY market_cap DESC")
    
    # 製品売上ランキング (直近週)
    product_ranking = db.fetch_all("""
        SELECT 
            d.name as product_name, 
            d.id as design_id,
            c.name as maker_name, 
            c.id as maker_id,
            SUM(t.quantity) as total_quantity, 
            SUM(t.amount) as total_sales
        FROM transactions t
        JOIN product_designs d ON t.design_id = d.id
        JOIN companies c ON d.company_id = c.id
        WHERE t.type = 'b2c' AND t.week = ?
        GROUP BY t.design_id
        ORDER BY total_sales DESC
        LIMIT 10
    """, (target_week,))
    
    return render_template('world.html', trends=trends, ranking=ranking, market_cap_ranking=market_cap_ranking, product_ranking=product_ranking, target_week=target_week)

@app.route('/finance')
def finance():
    player = get_player_company()
    current_week = sim.get_current_week()
    
    # パラメータ取得
    period = request.args.get('period', 'weekly') # weekly, quarterly, yearly
    try:
        target = int(request.args.get('target', 0))
    except:
        target = 0

    # ロジックをSimulationクラスに委譲
    data = sim.get_financial_report(player['id'], current_week, period, target)
    
    return render_template('finance.html', **data, period=period)

@app.route('/company/<int:company_id>')
def company_detail(company_id):
    comp = db.fetch_one("SELECT * FROM companies WHERE id = ?", (company_id,))
    if not comp: return "Company not found", 404
    
    # 製品一覧
    products = db.fetch_all("SELECT * FROM product_designs WHERE company_id = ? AND status = 'completed'", (company_id,))
    
    # 従業員数
    emp_count = db.fetch_one("SELECT COUNT(*) as cnt FROM npcs WHERE company_id = ?", (company_id,))['cnt']
    
    # 代表者 (CEO)
    ceo = db.fetch_one("SELECT * FROM npcs WHERE company_id = ? AND role = 'ceo'", (company_id,))
    
    # 財務簡易情報 (直近週)
    current_week = sim.get_current_week()
    stats = db.fetch_one("SELECT * FROM weekly_stats WHERE company_id = ? AND week = ?", (company_id, current_week - 1))
    
    # 財務レポート (PL)
    period = request.args.get('period', 'quarterly')
    if period not in ['quarterly', 'yearly']:
        period = 'quarterly'
    
    try:
        target = int(request.args.get('target', 0))
    except:
        target = 0
        
    report_data = sim.get_financial_report(company_id, current_week, period, target)
    
    return render_template('detail_company.html', comp=comp, products=products, emp_count=emp_count, ceo=ceo, stats=stats, industries=gb.INDUSTRIES, report_data=report_data, period=period)

@app.route('/ir')
def ir():
    player = get_player_company()
    current_week = sim.get_current_week()
    
    # 株価履歴
    history = db.fetch_all("SELECT * FROM stock_history WHERE company_id = ? ORDER BY week ASC", (player['id'],))
    
    # 最新の指標
    latest = history[-1] if history else None
    
    # 決算報告書
    reports = db.fetch_all("SELECT * FROM financial_reports WHERE company_id = ? ORDER BY week DESC", (player['id'],))
    
    # 株主構成 (簡易表示)
    shareholders = [
        {'name': '創業者 (あなた)', 'shares': player['outstanding_shares'], 'ratio': 100.0}
    ]
    
    # IPO要件チェック
    ipo_check = None
    if player['listing_status'] == 'private':
        is_eligible, reasons = sim.check_ipo_eligibility(player['id'])
        ipo_check = {
            'is_eligible': is_eligible,
            'reasons': reasons,
            'min_assets': gb.IPO_MIN_NET_ASSETS,
            'min_rating': gb.IPO_MIN_CREDIT_RATING,
            'min_profit_weeks': gb.IPO_MIN_PROFIT_WEEKS
        }
    
    return render_template('ir.html', 
                           player=player, 
                           history=history, 
                           latest=latest, 
                           reports=reports, 
                           shareholders=shareholders,
                           ipo_check=ipo_check,
                           listing_status=player['listing_status'])

@app.route('/product/<int:design_id>')
def product_detail(design_id):
    product = db.fetch_one("""
        SELECT p.*, c.name as maker_name, c.id as maker_id, d.industry_key
        FROM product_designs p 
        JOIN companies c ON p.company_id = c.id 
        LEFT JOIN divisions d ON p.division_id = d.id
        WHERE p.id = ?
    """, (design_id,))
    if not product: return "Product not found", 404
    
    # パーツ構成のデコード
    parts_config = json.loads(product['parts_config']) if product['parts_config'] else {}
    
    # パーツ詳細情報の取得
    parts_details = []
    total_cost = 0
    for key, conf in parts_config.items():
        supplier = db.fetch_one("SELECT name FROM companies WHERE id = ?", (conf['supplier_id'],))
        parts_details.append({
            'key': key,
            'supplier_name': supplier['name'] if supplier else "Unknown",
            'score': conf['score'],
            'cost': conf['cost']
        })
        total_cost += conf['cost']

    return render_template('detail_product.html', product=product, parts_details=parts_details, total_cost=total_cost, industries=gb.INDUSTRIES)

@app.route('/npc/<int:npc_id>')
def npc_detail(npc_id):
    npc = db.fetch_one("""
        SELECT n.*, c.name as company_name 
        FROM npcs n 
        LEFT JOIN companies c ON n.company_id = c.id 
        WHERE n.id = ?
    """, (npc_id,))
    if not npc: return "NPC not found", 404
    
    # プレイヤーの人事力を取得（能力値マスク用）
    player = get_player_company()
    caps = sim.calculate_capabilities(player['id'])
    hr_power = caps['hr']
    
    return render_template('detail_npc.html', npc=npc, hr_power=hr_power, industries=gb.INDUSTRIES)

@app.route('/ir/ipo_apply', methods=['POST'])
def ipo_apply():
    player = get_player_company()
    is_eligible, reasons = sim.check_ipo_eligibility(player['id'])
    
    if is_eligible:
        db.execute_query("UPDATE companies SET listing_status = 'applying' WHERE id = ?", (player['id'],))
        flash("IPO申請を行いました。次週、審査結果が発表されます。", "success")
    else:
        flash(f"IPO要件を満たしていません: {', '.join(reasons)}", "error")
        
    return redirect(url_for('ir'))

if __name__ == '__main__':
    # データベースがない場合は初期化
    if not os.path.exists(os.path.join(os.path.dirname(__file__), "newsim.db")):
        print("Database not found. Running seed...")
        from seed import run_seed
        run_seed()
        
    app.run(debug=True, port=5000)
